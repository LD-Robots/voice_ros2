# conversational_server/conversational_server/stream_shaper.py
"""
Stream Shaper - Netezire flux LLM → TTS

Transformă tokenii LLM într-un stream de fraze complete pentru TTS mai natural:
  - Prebuffer inițial pentru start lin
  - Livrează la punctuație sau soft_max_chars
  - Idle flush când nu mai vin tokeni
"""
from __future__ import annotations
import time
from typing import Iterable, Iterator

_BOUNDARY = ".!?…:;"


def _has_boundary(s: str) -> bool:
    return any(ch in s for ch in _BOUNDARY)


def _cut_soft(s: str, soft_max_chars: int) -> tuple:
    """Taie la ultimul spațiu înainte de soft_max."""
    if len(s) <= soft_max_chars:
        return s, ""
    cut = s.rfind(" ", 0, soft_max_chars)
    if cut < 40:  # prea aproape de început? taie direct
        cut = soft_max_chars
    return s[:cut].rstrip(), s[cut:].lstrip()


def shape_stream(
    token_iter: Iterable[str],
    prebuffer_chars: int = 120,   # așteaptă puțin înainte de primul sunet => start mai lin
    min_chunk_chars: int = 60,    # nu livra bucăți prea mici
    soft_max_chars: int = 140,    # forțează flush dacă devine prea lung fără punctuație
    max_idle_ms: int = 250,       # dacă nu vin tokeni o fracțiune de secundă, flushează ce ai
) -> Iterator[str]:
    """
    Strânge tokenii în fraze stabile:
      - pornește vorbirea doar după ~prebuffer_chars
      - apoi livrează când găsește punctuație sau depășește soft_max_chars
      - dacă nu mai vin tokeni o clipă, flushează ce ai (max_idle_ms)
    """
    buf = []
    buf_chars = 0

    # 1) prebuffer inițial — evită startul în mijloc de propoziție
    t_last = time.monotonic()
    for tok in token_iter:
        buf.append(tok)
        buf_chars += len(tok)
        t_last = time.monotonic()
        if buf_chars >= prebuffer_chars:
            break

    if buf_chars:
        yield "".join(buf)
        buf = []
        buf_chars = 0

    # 2) rulare normală — preferă propoziții complete, dar fără pauze lungi
    carry = ""
    t_last = time.monotonic()
    for tok in token_iter:
        carry += tok
        now = time.monotonic()
        
        # avem propoziție completă?
        if _has_boundary(carry) and len(carry) >= min_chunk_chars:
            out = carry
            carry = ""
            yield out
            t_last = now
            continue

        # prea lung fără punctuație? taie blând
        if len(carry) >= soft_max_chars:
            head, tail = _cut_soft(carry, soft_max_chars)
            if head:
                yield head
                t_last = now
            carry = tail
            continue

        # idle flush (dacă nu mai vin tokeni)
        if (now - t_last) * 1000 >= max_idle_ms and carry:
            yield carry
            carry = ""
            t_last = now

    # 3) finalizează restul
    if carry.strip():
        yield carry
