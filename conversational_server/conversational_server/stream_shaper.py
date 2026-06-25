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

_BOUNDARY = ".!?…:;,-"


def _has_boundary(s: str) -> bool:
    return any(ch in s for ch in _BOUNDARY)


def _find_boundary_split(s: str) -> int:
    """
    Find the index of the rightmost boundary character in s
    that is either at the end of the string or followed by whitespace.
    Returns -1 if not found.
    """
    for i in range(len(s) - 1, -1, -1):
        if s[i] in _BOUNDARY:
            if i == len(s) - 1 or s[i+1].isspace():
                return i
    return -1


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
    token_iter = iter(token_iter)
    buf = []
    buf_chars = 0

    # 1) initial prebuffer — avoid starting mid-sentence
    t_last = time.monotonic()
    for tok in token_iter:
        buf.append(tok)
        buf_chars += len(tok)
        t_last = time.monotonic()
        if buf_chars >= prebuffer_chars:
            full_buf = "".join(buf)
            idx = _find_boundary_split(full_buf)
            if idx != -1:
                head = full_buf[:idx+1]
                tail = full_buf[idx+1:]
                yield head.strip()
                buf = [tail] if tail else []
                buf_chars = len(tail)
                break
            elif (_has_boundary(tok) or " " in tok or tok.endswith(" ") or tok.startswith(" ")):
                yield full_buf.strip()
                buf = []
                buf_chars = 0
                break

    if buf:
        yield "".join(buf).strip()
        buf = []
        buf_chars = 0

    # 2) normal flow — prefer complete sentences, but avoid long pauses
    carry = ""
    t_last = time.monotonic()
    for tok in token_iter:
        carry += tok
        now = time.monotonic()
        
        # do we have a complete sentence? split strictly at the boundary
        idx = _find_boundary_split(carry)
        if idx != -1:
            head = carry[:idx+1]
            if len(head) >= min_chunk_chars:
                tail = carry[idx+1:]
                yield head.strip()
                carry = tail.lstrip()
                t_last = now
                continue

        # too long without punctuation? soft cut
        if len(carry) >= soft_max_chars:
            head, tail = _cut_soft(carry, soft_max_chars)
            if head:
                yield head.strip()
                t_last = now
            carry = tail.lstrip()
            continue

        # idle flush (if no tokens arrive)
        if (now - t_last) * 1000 >= max_idle_ms and carry:
            yield carry.strip()
            carry = ""
            t_last = now

    # 3) finalize the remainder
    if carry.strip():
        yield carry.strip()
