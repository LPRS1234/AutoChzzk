from __future__ import annotations

import hashlib
import io
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from autochzzk_core.updater import (
    UpdateCancelled,
    UpdateInfo,
    UpdateIntegrityError,
    UpdateMetadataError,
    download_update,
    find_available_update,
    get_release_version,
    launch_installer,
    verify_installer,
)


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()


def release_for(version: str, content: bytes) -> dict:
    digest = hashlib.sha256(content).hexdigest()
    return {
        "tag_name": f"v{version}",
        "assets": [
            {
                "name": f"AutoChzzk-Setup-{version}.exe",
                "browser_download_url": f"https://github.com/LPRS1234/AutoChzzk/releases/download/v{version}/AutoChzzk-Setup-{version}.exe",
                "digest": f"sha256:{digest}",
                "size": len(content),
            }
        ],
    }


class UpdateMetadataTests(unittest.TestCase):
    def test_release_version_accepts_optional_v_prefix(self) -> None:
        self.assertEqual(get_release_version({"tag_name": "v1.2.2"}), "1.2.2")

    def test_invalid_release_version_is_rejected(self) -> None:
        with self.assertRaises(UpdateMetadataError):
            get_release_version({"tag_name": "latest"})

    def test_same_version_has_no_update(self) -> None:
        self.assertIsNone(find_available_update(release_for("1.2.1", b"setup"), "1.2.1"))

    def test_new_version_returns_exact_installer(self) -> None:
        info = find_available_update(release_for("1.2.2", b"setup"), "1.2.1")
        self.assertIsNotNone(info)
        self.assertEqual(info.version, "1.2.2")
        self.assertEqual(info.asset_name, "AutoChzzk-Setup-1.2.2.exe")

    def test_missing_digest_is_rejected(self) -> None:
        release = release_for("1.2.2", b"setup")
        release["assets"][0]["digest"] = None
        with self.assertRaises(UpdateMetadataError):
            find_available_update(release, "1.2.1")


class UpdateDownloadTests(unittest.TestCase):
    def test_download_is_verified_and_promoted(self) -> None:
        content = b"verified setup contents"
        info = find_available_update(release_for("1.2.2", content), "1.2.1")
        with tempfile.TemporaryDirectory() as temp_dir:
            with mock.patch("urllib.request.urlopen", return_value=FakeResponse(content)):
                path = download_update(info, Path(temp_dir))
            self.assertEqual(path.read_bytes(), content)
            self.assertTrue(verify_installer(path, info.sha256))
            self.assertFalse(path.with_name(f"{path.name}.part").exists())

    def test_hash_mismatch_removes_partial_file(self) -> None:
        expected = b"expected setup"
        received = b"modified setup"
        release = release_for("1.2.2", expected)
        release["assets"][0]["size"] = len(received)
        info = find_available_update(release, "1.2.1")
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir)
            with mock.patch("urllib.request.urlopen", return_value=FakeResponse(received)):
                with self.assertRaises(UpdateIntegrityError):
                    download_update(info, destination)
            self.assertFalse((destination / f"{info.asset_name}.part").exists())
            self.assertFalse((destination / info.asset_name).exists())

    def test_cancelled_download_does_not_create_file(self) -> None:
        content = b"setup"
        info = find_available_update(release_for("1.2.2", content), "1.2.1")
        cancel_event = threading.Event()
        cancel_event.set()
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(UpdateCancelled):
                download_update(info, Path(temp_dir), cancel_event=cancel_event)
            self.assertFalse((Path(temp_dir) / info.asset_name).exists())


class InstallerLaunchTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows 전용 테스트")
    def test_installer_uses_uac_and_silent_arguments(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            installer = Path(temp_dir) / "AutoChzzk-Setup-1.2.2.exe"
            installer.touch()
            with mock.patch.object(os, "startfile", create=True) as startfile:
                launch_installer(installer)
            _, kwargs = startfile.call_args
            self.assertEqual(kwargs["operation"], "runas")
            self.assertIn("/VERYSILENT", kwargs["arguments"])
            self.assertIn("/SUPPRESSMSGBOXES", kwargs["arguments"])
            self.assertIn("/AUTOUPDATE=1", kwargs["arguments"])


if __name__ == "__main__":
    unittest.main()
