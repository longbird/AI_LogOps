"""Keyword and phrase matching for call quality analysis."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import cast

logger = logging.getLogger(__name__)

_BASE_DIR = Path(__file__).parent


def _load_json(filename: str) -> list[str]:
    path = _BASE_DIR / filename
    if not path.exists():
        logger.warning("Keyword file not found: %s", path)
        return []
    with path.open("r", encoding="utf-8") as f:
        data = cast(object, json.load(f))
    if not isinstance(data, list):
        return []
    items = cast(list[object], data)
    if not all(isinstance(item, str) for item in items):
        return []
    return cast(list[str], items)


def check_required_phrases(text: str) -> tuple[bool, list[str]]:
    phrases = _load_json("required_phrases.json")
    matched = [p for p in phrases if p in text]
    return len(matched) > 0, matched


def check_forbidden_words(text: str) -> tuple[bool, list[str]]:
    words = _load_json("forbidden_words.json")
    detected = [w for w in words if w in text]
    return len(detected) > 0, detected
