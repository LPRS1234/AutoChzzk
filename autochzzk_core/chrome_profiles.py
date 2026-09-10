"""Chrome profile discovery."""
from __future__ import annotations

import json
import os
from pathlib import Path


class ProfileReadError(OSError):
    """Profile discovery failed; existing selection must be retained."""


def get_chrome_profiles() -> list[dict[str, str]]:
    local_state = Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "User Data" / "Local State"
    user_data_dir = local_state.parent
    try:
        data = json.loads(local_state.read_text(encoding="utf-8"))
        info_cache = data["profile"]["info_cache"]
        if not isinstance(info_cache, dict):
            raise ValueError("Invalid profile cache")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ProfileReadError("Chrome 프로필 정보를 읽지 못했습니다.") from exc

    profiles = []
    for directory, info in info_cache.items():
        if not isinstance(directory, str) or not isinstance(info, dict):
            continue
        if directory in (".", "..") or any(char in directory for char in "/\\:"):
            continue
        if not (user_data_dir / directory).is_dir():
            continue
        email = str(info.get("user_name") or "")
        name = email.split("@", 1)[0] if "@" in email else str(info.get("name") or info.get("gaia_name") or directory)
        profiles.append(
            {
                "directory": directory,
                "name": name,
                "gaia_id": str(info.get("gaia_id") or ""),
                "email": email,
            }
        )
    names = [profile["name"] for profile in profiles]
    used = set()
    for profile in profiles:
        base = profile["name"]
        if names.count(base) > 1:
            base = f"{base} ({profile['directory']})"
        label = base
        number = 2
        while label in used:
            label = f"{base} [{number}]"
            number += 1
        profile["name"] = label
        used.add(label)
    return profiles or [{"directory": "Default", "name": "기본 프로필", "gaia_id": "", "email": ""}]

