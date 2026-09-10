import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from autochzzk_core.chrome_profiles import ProfileReadError, get_chrome_profiles


class ChromeProfilesTests(unittest.TestCase):
    def test_duplicate_labels_are_unique(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory) / "Google" / "Chrome" / "User Data"
            base.mkdir(parents=True)
            for name in ("Default", "Profile 1"):
                (base / name).mkdir()
            (base / "Local State").write_text(json.dumps({"profile": {"info_cache": {
                "Default": {"name": "Same"}, "Profile 1": {"name": "Same"}}}}), encoding="utf-8")
            with patch.dict("os.environ", {"LOCALAPPDATA": directory}):
                profiles = get_chrome_profiles()
            self.assertEqual(len({profile["name"] for profile in profiles}), 2)

    def test_corrupt_profile_state_is_not_reported_as_deleted(self):
        with patch.object(Path, "read_text", return_value="["):
            with self.assertRaises(ProfileReadError):
                get_chrome_profiles()
        with patch.object(Path, "read_text", return_value='{"profile": null}'):
            with self.assertRaises(ProfileReadError):
                get_chrome_profiles()

    def test_temporarily_missing_state_is_not_reported_as_default_profile(self):
        with patch.object(Path, "read_text", side_effect=FileNotFoundError):
            with self.assertRaises(ProfileReadError):
                get_chrome_profiles()
