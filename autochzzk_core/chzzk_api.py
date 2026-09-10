"""CHZZK and GitHub API helpers."""
from __future__ import annotations

import json #json -> dict 변환
import re
import urllib.error
import urllib.request #치지직, github api에 http 요청

from .config import (
    APP_NAME, #GitHub API 요청에 넣는 애플리케이션 이름
    APP_VERSION, #현재 앱 버전
    CHANNEL_API_URL, #치지직 채널 정보 api
    CHANNEL_ID_PATTERN, #채널 id 형식검사 정규식
    LIVE_API_URL, #치지직 라이브 상태 api
    UPDATE_API_URL, #github 최신 릴리즈 api
    URL_ID_PATTERN, #URL에서 채널 id 추출을 위한 정규식
)


def extract_channel_id(value: str) -> str | None:
    value = value.strip() #앞뒤 공백제거
    if CHANNEL_ID_PATTERN.fullmatch(value): #입력값이 채널id 형식인지 검사
        return value.lower()
    match = URL_ID_PATTERN.search(value)
    return match.group(1).lower() if match else None


class ApiResponseError(urllib.error.URLError):
    """The remote response does not establish a valid channel state."""


def request_content(url: str) -> dict:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": f"Mozilla/5.0 AutoChzzk/{APP_VERSION}", "Accept": "application/json"},
    ) #기본 get요청
    with urllib.request.urlopen(request, timeout=15) as response:
        try:
            payload = json.load(response)  # 받아온 JSON을 dict로 변환
        except (ValueError, UnicodeError) as exc:
            raise ApiResponseError("API 응답을 해석하지 못했습니다.") from exc
    if not isinstance(payload, dict) or payload.get("code") != 200:
        raise ApiResponseError("API 성공 응답이 아닙니다.")
    content = payload.get("content")
    if not isinstance(content, dict):
        raise ApiResponseError("API content 형식이 올바르지 않습니다.")
    return content


def get_channel_name(channel_id: str) -> str:
    name = request_content(CHANNEL_API_URL.format(channel_id=channel_id)).get("channelName")
    if not isinstance(name, str) or not name.strip():
        raise ApiResponseError("채널 이름이 없는 응답입니다.")
    return name


def get_live_status(channel_id: str) -> tuple[bool, str]:
    content = request_content(LIVE_API_URL.format(channel_id=channel_id))
    status = content.get("status")
    if status not in ("OPEN", "CLOSE"):
        raise ApiResponseError("방송 상태가 확인되지 않은 응답입니다.")
    title = content.get("liveTitle")
    if title is not None and not isinstance(title, str):
        raise ApiResponseError("방송 제목 형식이 올바르지 않습니다.")
    return status == "OPEN", title or "제목 없는 방송"


def version_key(version: str) -> tuple[int, ...]:
    """Convert a release tag such as v1.2.0 into a comparable version tuple."""
    numbers = re.findall(r"\d+", version)
    return tuple(int(number) for number in numbers) if numbers else ()


def get_latest_release() -> dict: #dict를 반환한다
    request = urllib.request.Request(
        UPDATE_API_URL,
        headers={"User-Agent": f"{APP_NAME}/{APP_VERSION}", "Accept": "application/vnd.github+json"},
    ) #어떤 주소로 요청할지 설계도 작성. UPDATE_API_URL로 요청, headers: 서버로전달하는 부가정보
    with urllib.request.urlopen(request, timeout=8) as response: #실제 요청을 request에 따라 보내고, 응답을 response에 저장. timeout: 8초동안 응답없으면 에러발생
        try:
            release = json.load(response)
        except (ValueError, UnicodeError) as exc:
            raise ApiResponseError("업데이트 응답을 해석하지 못했습니다.") from exc
    if not isinstance(release, dict):
        raise ApiResponseError("업데이트 응답 형식이 올바르지 않습니다.")
    return release
