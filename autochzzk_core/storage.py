"""Persistence for channels and application settings."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import threading

from .config import CHANNEL_ID_PATTERN, DATA_PATH, SETTINGS_PATH


class StorageError(OSError):
    """Stored data could not be safely read or written."""


_storage_lock = threading.RLock()
_blocked_paths: set[Path] = set()


def _validate_channels(loaded: object) -> list[dict]:
    if not isinstance(loaded, list):
        raise StorageError("채널 파일은 목록이어야 합니다.")
    channels = []
    seen = set()
    for index, item in enumerate(loaded):
        if not isinstance(item, dict):
            raise StorageError(f"채널 {index + 1}의 형식이 올바르지 않습니다.")
        channel_id = item.get("id")
        # item이 dict인지, item의 id값이 채널id 형식인지 검사
        if not isinstance(channel_id, str) or not CHANNEL_ID_PATTERN.fullmatch(channel_id):
            raise StorageError(f"채널 {index + 1}의 ID 형식이 올바르지 않습니다.")
        if channel_id.lower() in seen:
            raise StorageError("중복된 채널 ID가 있습니다.")
        seen.add(channel_id.lower())
        if "name" in item and not isinstance(item["name"], str):
            raise StorageError(f"채널 {index + 1}의 이름 형식이 올바르지 않습니다.")
        if "enabled" in item and not isinstance(item["enabled"], bool):
            raise StorageError(f"채널 {index + 1}의 감지 설정이 올바르지 않습니다.")
        interval = item.get("interval", 60)
        if isinstance(interval, bool) or not isinstance(interval, (int, str)):
            raise StorageError(f"채널 {index + 1}의 확인 간격이 올바르지 않습니다.")
        try:
            interval = max(15, int(interval))  # 둘 중 큰값 반환: 이전 버전의 짧은 간격 호환
        except ValueError as exc:
            raise StorageError(f"채널 {index + 1}의 확인 간격이 올바르지 않습니다.") from exc
        normalized = dict(item)  # 알 수 없는 추가 필드도 그대로 보존한다.
        normalized["id"] = channel_id.lower()
        normalized["interval"] = interval
        channels.append(normalized)
    return channels


def _validate_settings(loaded: object) -> dict:
    if not isinstance(loaded, dict):
        raise StorageError("설정 파일은 객체이어야 합니다.")
    if "chrome_profile_directory" in loaded and not isinstance(loaded["chrome_profile_directory"], str):
        raise StorageError("Chrome 프로필 설정 형식이 올바르지 않습니다.")
    return dict(loaded)


def _load(path: Path, validator, default):
    with _storage_lock:
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))  # utf-8로 읽은 JSON을 Python 값으로 변환
            result = validator(loaded)
        except FileNotFoundError:
            return default
        except (OSError, ValueError, UnicodeError) as exc:
            _blocked_paths.add(path.resolve())
            raise StorageError(f"{path.name}을 읽지 못했습니다. 원본을 보존하고 저장을 중지합니다.") from exc
        _blocked_paths.discard(path.resolve())
        return result


def _replace_bytes(path: Path, data: bytes) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=path.name + ".", suffix=".tmp", delete=False) as output:
            temporary = Path(output.name)
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _save(path: Path, value, validator) -> None:
    with _storage_lock:
        if path.resolve() in _blocked_paths:
            raise StorageError(f"{path.name} 읽기 오류로 저장이 중지되어 있습니다.")
        try:
            validated = validator(value)
            # json.dumps: dict를 json으로 변환, ensure_ascii=False: 한글도 그대로 저장, indent=2: 들여쓰기 2칸
            encoded = json.dumps(validated, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
            try:
                original = path.read_bytes()
            except FileNotFoundError:
                original = None
            if original is not None:
                # 실행 중 외부에서 손상된 경우에도 원본을 덮어쓰지 않는다.
                validator(json.loads(original.decode("utf-8")))
                _replace_bytes(path.with_suffix(path.suffix + ".bak"), original)
            _replace_bytes(path, encoded)
        except (OSError, ValueError, TypeError, UnicodeError) as exc:
            raise StorageError(f"{path.name}을 안전하게 저장하지 못했습니다.") from exc


def load_channels() -> list[dict]:
    return _load(DATA_PATH, _validate_channels, [])


def save_channels(channels: list[dict]) -> None:
    _save(DATA_PATH, channels, _validate_channels)


def load_settings() -> dict:
    return _load(SETTINGS_PATH, _validate_settings, {})


def save_settings(settings: dict) -> None:
    _save(SETTINGS_PATH, settings, _validate_settings)
