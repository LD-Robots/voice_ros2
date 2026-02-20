#!/usr/bin/env python3
"""
Robot command parser.

Transforms free text into a canonical command format.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re
import unicodedata


def normalize_text(text: str) -> str:
    """Lowercase and normalize text for robust command parsing."""
    if not text:
        return ""

    cleaned = text.strip().lower()
    cleaned = cleaned.replace("-", " ")
    cleaned = cleaned.replace("_", " ")

    # "10,5" -> "10.5"
    cleaned = re.sub(r"(\d),(\d)", r"\1.\2", cleaned)

    normalized = unicodedata.normalize("NFD", cleaned)
    normalized = "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")
    normalized = re.sub(r"[^a-z0-9.\s]", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


@dataclass(frozen=True)
class ParsedRobotCommand:
    action: str
    target: str = ""
    value: float = 0.0
    unit: str = ""
    tags: list[str] = field(default_factory=list)
    confidence: float = 0.85
    requires_confirmation: bool = False


def _strip_preamble(text: str) -> str:
    """Remove polite or addressing prefixes."""
    out = text
    pattern = re.compile(r"^(?:please|te rog|hey robot|hello robot|robot)\s+")
    while True:
        updated = pattern.sub("", out).strip()
        if updated == out:
            return out
        out = updated


def _parse_move(text: str) -> ParsedRobotCommand | None:
    direction = "forward"
    work = text

    backward_markers = ("backward", "backwards", "back", "inapoi", "in spate")
    if any(marker in work for marker in backward_markers):
        direction = "backward"

    move_patterns = [
        r"^(?:go|move|walk)\s+(?:forward|ahead|backward|backwards|back)?\s*(\d+(?:\.\d+)?)\s*(?:m|meter|meters)$",
        r"^(?:mergi|deplaseaza te|deplasare)\s+(?:inapoi|in spate|inainte)?\s*(\d+(?:\.\d+)?)\s*(?:m|metru|metri)$",
    ]
    for pattern in move_patterns:
        match = re.match(pattern, work)
        if match:
            return ParsedRobotCommand(
                action="move",
                target=direction,
                value=float(match.group(1)),
                unit="m",
                confidence=0.93,
            )

    # Lightweight fallback with default distance.
    fallback_patterns = [
        r"^(?:go|move|walk)\s+(?:forward|ahead|backward|backwards|back)$",
        r"^(?:mergi|deplaseaza te)\s+(?:inainte|inapoi|in spate)$",
    ]
    for pattern in fallback_patterns:
        if re.match(pattern, work):
            return ParsedRobotCommand(
                action="move",
                target=direction,
                value=1.0,
                unit="m",
                confidence=0.75,
            )
    return None


def _parse_raise_hand(text: str) -> ParsedRobotCommand | None:
    hand_patterns = [
        (r"^(?:raise|lift)\s+(?:your\s+)?left\s+hand$", "left_hand"),
        (r"^(?:raise|lift)\s+(?:your\s+)?right\s+hand$", "right_hand"),
        (r"^(?:raise|lift)\s+(?:your\s+)?(?:both\s+)?hands?$", "both_hands"),
        (r"^ridica\s+mana\s+stanga$", "left_hand"),
        (r"^ridica\s+mana\s+dreapta$", "right_hand"),
        (r"^ridica\s+(?:mainile|mana)$", "both_hands"),
    ]
    for pattern, target in hand_patterns:
        if re.match(pattern, text):
            return ParsedRobotCommand(
                action="raise_hand",
                target=target,
                confidence=0.92,
            )
    return None


def _parse_dance(text: str) -> ParsedRobotCommand | None:
    patterns = [
        r"^(?:dance|start dancing)$",
        r"^(?:danseaza|incepe sa dansezi)$",
    ]
    for pattern in patterns:
        if re.match(pattern, text):
            return ParsedRobotCommand(
                action="dance",
                target="whole_body",
                confidence=0.9,
            )
    return None


def _parse_stop(text: str) -> ParsedRobotCommand | None:
    patterns = [
        r"^(?:stop|stop now|halt|freeze)$",
        r"^(?:opreste te|stop robot)$",
    ]
    for pattern in patterns:
        if re.match(pattern, text):
            return ParsedRobotCommand(
                action="stop",
                target="all",
                confidence=0.95,
            )
    return None


def parse_robot_command(text: str) -> tuple[ParsedRobotCommand | None, str]:
    """
    Parse robot command from raw text.
    Returns tuple(parsed_command_or_none, normalized_text).
    """
    normalized = normalize_text(text)
    if not normalized:
        return None, normalized

    stripped = _strip_preamble(normalized)
    for parser in (_parse_stop, _parse_raise_hand, _parse_move, _parse_dance):
        parsed = parser(stripped)
        if parsed is not None:
            return parsed, normalized
    return None, normalized
