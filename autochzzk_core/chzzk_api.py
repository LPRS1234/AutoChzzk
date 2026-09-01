"""CHZZK and GitHub API helpers."""
from __future__ import annotations

import json
import re
import urllib.request

from .config import (
    APP_NAME,
    APP_VERSION,
    CHANNEL_API_URL,
    CHANNEL_ID_PATTERN,
    LIVE_API_URL,
    UPDATE_API_URL,
    URL_ID_PATTERN,
)


def extract_channel_id(value: str) -> str | None:
    value = value.strip()
    if CHANNEL_ID_PATTERN.fullmatch(value):
        return value.lower()
    match = URL_ID_PATTERN.search(value)
    return match.group(1).lower() if match else None


def request_content(url: str) -> dict:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 AutoChzzk/1.1", "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.load(response).get("content") or {}


def get_channel_name(channel_id: str) -> str:
    return request_content(CHANNEL_API_URL.format(channel_id=channel_id)).get("channelName") or channel_id


def get_live_status(channel_id: str) -> tuple[bool, str]:
    content = request_content(LIVE_API_URL.format(channel_id=channel_id))
    return content.get("status") == "OPEN", content.get("liveTitle") or "제목 없는 방송"


def version_key(version: str) -> tuple[int, ...]:
    """Convert a release tag such as v1.2.0 into a comparable version tuple."""
    numbers = re.findall(r"\d+", version)
    return tuple(int(number) for number in numbers) if numbers else ()


def get_latest_release() -> dict:
    request = urllib.request.Request(
        UPDATE_API_URL,
        headers={"User-Agent": f"{APP_NAME}/{APP_VERSION}", "Accept": "application/vnd.github+json"},
    )
    with urllib.request.urlopen(request, timeout=8) as response:
        return json.load(response)

