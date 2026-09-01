"""Persistence for channels and application settings."""
from __future__ import annotations

import json

from .config import CHANNEL_ID_PATTERN, DATA_PATH, SETTINGS_PATH


def load_channels() -> list[dict]:
    try:
        loaded = json.loads(DATA_PATH.read_text(encoding="utf-8"))
        channels = []
        for item in loaded:
            if not isinstance(item, dict) or not CHANNEL_ID_PATTERN.fullmatch(item.get("id", "")):
                continue
            try:
                item["interval"] = max(15, int(item.get("interval", 60)))
            except (TypeError, ValueError):
                item["interval"] = 60
            channels.append(item)
        return channels
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_channels(channels: list[dict]) -> None:
    DATA_PATH.write_text(json.dumps(channels, ensure_ascii=False, indent=2), encoding="utf-8")


def load_settings() -> dict:
    try:
        loaded = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        return loaded if isinstance(loaded, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_settings(settings: dict) -> None:
    SETTINGS_PATH.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")

