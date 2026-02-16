# conversational_server/conversational_server/stream_shaper.py
"""
Stream Shaper - Smoothing LLM → TTS stream

Transforms LLM tokens into a stream of complete phrases for more natural TTS:
  - Initial prebuffer for a smooth start
  - Deliver at punctuation or soft_max_chars
  - Idle flush when no tokens arrive
"""
from __future__ import annotations
import time
from typing import Iterable, Iterator

_BOUNDARY = ".!?…:;"


def _has_boundary(s: str) -> bool:
    return any(ch in s for ch in _BOUNDARY)


def _cut_soft(s: str, soft_max_chars: int) -> tuple:
    """Cut at the last space before soft_max."""
    if len(s) <= soft_max_chars:
        return s, ""
    cut = s.rfind(" ", 0, soft_max_chars)
    if cut < 40:  # too close to the start? cut directly
        cut = soft_max_chars
    return s[:cut].rstrip(), s[cut:].lstrip()


def shape_stream(
    token_iter: Iterable[str],
    prebuffer_chars: int = 120,   # wait a bit before first sound => smoother start
    min_chunk_chars: int = 60,    # don't deliver chunks that are too small
    soft_max_chars: int = 140,    # force flush if too long without punctuation
    max_idle_ms: int = 250,       # if no tokens arrive briefly, flush what you have
) -> Iterator[str]:
    """
    Pack tokens into stable phrases:
      - start speaking only after ~prebuffer_chars
      - then deliver when punctuation appears or soft_max_chars is exceeded
      - if tokens stop briefly, flush what you have (max_idle_ms)
    """
    buf = []
    buf_chars = 0

    # 1) initial prebuffer — avoid starting mid-sentence
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

    # 2) normal flow — prefer complete sentences, but avoid long pauses
    carry = ""
    t_last = time.monotonic()
    for tok in token_iter:
        carry += tok
        now = time.monotonic()
        
        # do we have a complete sentence?
        if _has_boundary(carry) and len(carry) >= min_chunk_chars:
            out = carry
            carry = ""
            yield out
            t_last = now
            continue

        # too long without punctuation? soft cut
        if len(carry) >= soft_max_chars:
            head, tail = _cut_soft(carry, soft_max_chars)
            if head:
                yield head
                t_last = now
            carry = tail
            continue

        # idle flush (if no tokens arrive)
        if (now - t_last) * 1000 >= max_idle_ms and carry:
            yield carry
            carry = ""
            t_last = now

    # 3) finalize the remainder
    if carry.strip():
        yield carry
