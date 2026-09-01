"""Download and launch verified AutoChzzk updates."""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import subprocess
import sys
import threading
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .chzzk_api import version_key
from .config import APP_NAME, APP_VERSION, UPDATE_DIR

DOWNLOAD_CHUNK_SIZE = 256 * 1024
DOWNLOAD_TIMEOUT_SECONDS = 30
MAX_UPDATE_SIZE_BYTES = 200 * 1024 * 1024
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)
VERSION_PATTERN = re.compile(r"^v?(\d+(?:\.\d+){1,3})$", re.IGNORECASE)


class UpdateError(Exception):
    """Base error for update metadata, download, and verification failures."""


class UpdateMetadataError(UpdateError):
    """Raised when a GitHub Release does not contain a safe installer asset."""


class UpdateIntegrityError(UpdateError):
    """Raised when a downloaded installer does not match its Release metadata."""


class UpdateCancelled(UpdateError):
    """Raised when the app closes while an update is downloading."""


@dataclass(frozen=True)
class UpdateInfo:
    version: str
    asset_name: str
    download_url: str
    sha256: str
    size: int


def find_available_update(release: dict, current_version: str = APP_VERSION) -> UpdateInfo | None:
    """Return a verified installer description for a newer GitHub Release."""
    tag_name = str(release.get("tag_name") or "").strip()
    version_match = VERSION_PATTERN.fullmatch(tag_name)
    if version_match is None:
        raise UpdateMetadataError("업데이트 버전 형식이 올바르지 않습니다.")

    version = version_match.group(1)
    if version_key(version) <= version_key(current_version):
        return None

    expected_name = f"{APP_NAME}-Setup-{version}.exe"
    assets = release.get("assets")
    if not isinstance(assets, list):
        raise UpdateMetadataError("업데이트 설치 파일 정보가 없습니다.")
    asset = next(
        (
            item
            for item in assets
            if isinstance(item, dict)
            and str(item.get("name") or "").casefold() == expected_name.casefold()
        ),
        None,
    )
    if asset is None:
        raise UpdateMetadataError("버전에 맞는 업데이트 설치 파일이 없습니다.")

    download_url = str(asset.get("browser_download_url") or "").strip()
    parsed_url = urllib.parse.urlparse(download_url)
    if parsed_url.scheme.casefold() != "https" or parsed_url.hostname not in {"github.com", "www.github.com"}:
        raise UpdateMetadataError("업데이트 다운로드 주소가 올바르지 않습니다.")

    digest = str(asset.get("digest") or "").strip()
    algorithm, separator, sha256 = digest.partition(":")
    if separator != ":" or algorithm.casefold() != "sha256" or SHA256_PATTERN.fullmatch(sha256) is None:
        raise UpdateMetadataError("업데이트 SHA-256 정보가 없습니다.")

    try:
        size = int(asset.get("size"))
    except (TypeError, ValueError) as exc:
        raise UpdateMetadataError("업데이트 파일 크기 정보가 올바르지 않습니다.") from exc
    if size <= 0 or size > MAX_UPDATE_SIZE_BYTES:
        raise UpdateMetadataError("업데이트 파일 크기가 허용 범위를 벗어났습니다.")

    return UpdateInfo(version, expected_name, download_url, sha256.casefold(), size)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(DOWNLOAD_CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def verify_installer(path: Path, expected_sha256: str) -> bool:
    if not path.is_file() or SHA256_PATTERN.fullmatch(expected_sha256) is None:
        return False
    return hmac.compare_digest(file_sha256(path), expected_sha256.casefold())


def download_update(
    info: UpdateInfo,
    destination: Path = UPDATE_DIR,
    progress: Callable[[int, int], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> Path:
    """Download to a partial file, verify it, then atomically publish it locally."""
    if Path(info.asset_name).name != info.asset_name:
        raise UpdateMetadataError("업데이트 파일 이름이 올바르지 않습니다.")

    destination.mkdir(parents=True, exist_ok=True)
    installer_path = destination / info.asset_name
    partial_path = destination / f"{info.asset_name}.part"

    if verify_installer(installer_path, info.sha256):
        if progress is not None:
            progress(info.size, info.size)
        return installer_path
    installer_path.unlink(missing_ok=True)
    partial_path.unlink(missing_ok=True)

    if cancel_event is not None and cancel_event.is_set():
        raise UpdateCancelled("업데이트 다운로드가 취소되었습니다.")

    request = urllib.request.Request(
        info.download_url,
        headers={"User-Agent": f"{APP_NAME}/{APP_VERSION}", "Accept": "application/octet-stream"},
    )
    downloaded = 0
    try:
        if progress is not None:
            progress(0, info.size)
        with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response, partial_path.open("wb") as file:
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    raise UpdateCancelled("업데이트 다운로드가 취소되었습니다.")
                chunk = response.read(DOWNLOAD_CHUNK_SIZE)
                if not chunk:
                    break
                downloaded += len(chunk)
                if downloaded > info.size or downloaded > MAX_UPDATE_SIZE_BYTES:
                    raise UpdateIntegrityError("업데이트 파일 크기가 일치하지 않습니다.")
                file.write(chunk)
                if progress is not None:
                    progress(downloaded, info.size)

        if downloaded != info.size:
            raise UpdateIntegrityError("업데이트 파일 크기가 일치하지 않습니다.")
        if not verify_installer(partial_path, info.sha256):
            raise UpdateIntegrityError("업데이트 파일의 SHA-256이 일치하지 않습니다.")
        partial_path.replace(installer_path)
        return installer_path
    except Exception:
        partial_path.unlink(missing_ok=True)
        raise


def launch_installer(installer_path: Path) -> None:
    """Request UAC and start Inno Setup in silent update mode."""
    if sys.platform != "win32":
        raise OSError("자동 업데이트 설치는 Windows에서만 사용할 수 있습니다.")
    if not installer_path.is_file() or installer_path.suffix.casefold() != ".exe":
        raise OSError("업데이트 설치 파일을 찾을 수 없습니다.")

    arguments = subprocess.list2cmdline(
        ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-", "/AUTOUPDATE=1"]
    )
    os.startfile(
        str(installer_path),
        operation="runas",
        arguments=arguments,
        cwd=str(installer_path.parent),
        show_cmd=0,
    )
