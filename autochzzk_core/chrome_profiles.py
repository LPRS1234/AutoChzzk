"""Chrome profile discovery."""
from __future__ import annotations

import json
import os
from pathlib import Path


def get_chrome_profiles() -> list[dict[str, str]]:
    local_state = Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "User Data" / "Local State"
    user_data_dir = local_state.parent
    try:
        info_cache = json.loads(local_state.read_text(encoding="utf-8")).get("profile", {}).get("info_cache", {})
    except (OSError, json.JSONDecodeError):
        info_cache = {}

    profiles = []
    for directory, info in info_cache.items():
        if not isinstance(directory, str) or not isinstance(info, dict):
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
    return profiles or [{"directory": "Default", "name": "기본 프로필", "gaia_id": "", "email": ""}]

