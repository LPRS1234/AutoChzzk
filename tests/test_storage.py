import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from autochzzk_core import storage


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'channels.json'
        self.settings = Path(self.directory.name) / 'settings.json'
        for name, value in [('DATA_PATH', self.path), ('SETTINGS_PATH', self.settings)]:
            patcher = patch.object(storage, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(storage._blocked_paths.clear)

    def test_missing_files_are_empty(self):
        self.assertEqual(storage.load_channels(), [])
        self.assertEqual(storage.load_settings(), {})

    def test_corruption_blocks_later_empty_save(self):
        for source in ['[', 'null', '{}', '[{"id":123}]']:
            with self.subTest(source=source):
                self.path.write_text(source, encoding='utf-8')
                with self.assertRaises(storage.StorageError):
                    storage.load_channels()
                with self.assertRaises(storage.StorageError):
                    storage.save_channels([])
                self.assertEqual(self.path.read_text(encoding='utf-8'), source)

    def test_valid_channels_preserve_unknown_fields_and_legacy_interval(self):
        original = [{'id': 'a' * 32, 'extra': {'keep': True}}]
        self.path.write_text(json.dumps(original), encoding='utf-8')
        loaded = storage.load_channels()
        self.assertEqual(loaded[0]['interval'], 60)
        storage.save_channels(loaded)
        self.assertEqual(json.loads(self.path.read_text()) [0]['extra'], {'keep': True})
        self.assertEqual(json.loads(self.path.with_suffix('.json.bak').read_text()), original)

    def test_invalid_known_fields_reject_whole_file(self):
        for field, value in [('name', []), ('enabled', 'false'), ('interval', None), ('interval', True), ('interval', 'bad')]:
            with self.subTest(field=field, value=value):
                original = [{'id': 'a' * 32, field: value}]
                self.path.write_text(json.dumps(original), encoding='utf-8')
                with self.assertRaises(storage.StorageError):
                    storage.load_channels()
                self.assertEqual(json.loads(self.path.read_text()), original)

    def test_settings_error_never_overwrites_original(self):
        for source in ['[', 'null', '[]', '{"chrome_profile_directory": 123}']:
            self.settings.write_text(source, encoding='utf-8')
            with self.assertRaises(storage.StorageError):
                storage.load_settings()
            with self.assertRaises(storage.StorageError):
                storage.save_settings({})
            self.assertEqual(self.settings.read_text(), source)

    def test_failed_atomic_replace_keeps_original(self):
        original = json.dumps([{'id': 'a' * 32}])
        self.path.write_text(original, encoding='utf-8')
        real_replace = storage.os.replace
        def fail_data_replace(source, destination):
            if destination == self.path:
                raise OSError('simulated disk failure')
            return real_replace(source, destination)
        with patch.object(storage.os, 'replace', side_effect=fail_data_replace), self.assertRaises(storage.StorageError):
            storage.save_channels([])
        self.assertEqual(self.path.read_text(), original)
        self.assertFalse(list(self.path.parent.glob('*.tmp')))

    def test_corruption_after_load_is_preserved(self):
        self.path.write_text('[]', encoding='utf-8')
        storage.load_channels()
        self.path.write_text('[', encoding='utf-8')
        with self.assertRaises(storage.StorageError):
            storage.save_channels([])
        self.assertEqual(self.path.read_text(), '[')

    def test_unknown_settings_fields_survive(self):
        data = {'future': {'option': [1, 2]}}
        self.settings.write_text(json.dumps(data), encoding='utf-8')
        storage.save_settings(storage.load_settings())
        self.assertEqual(json.loads(self.settings.read_text()), data)

    def test_channel_ids_are_normalized_for_extension_matching(self):
        self.path.write_text(json.dumps([{'id': 'A' * 32}]), encoding='utf-8')
        self.assertEqual(storage.load_channels()[0]['id'], 'a' * 32)

    def test_backup_failure_keeps_original_and_previous_backup(self):
        original = json.dumps([{'id': 'a' * 32}])
        backup = self.path.with_suffix('.json.bak')
        self.path.write_text(original, encoding='utf-8')
        backup.write_text('previous backup', encoding='utf-8')
        with patch.object(storage.os, 'replace', side_effect=OSError('simulated backup failure')), self.assertRaises(storage.StorageError):
            storage.save_channels([])
        self.assertEqual(self.path.read_text(), original)
        self.assertEqual(backup.read_text(), 'previous backup')
        self.assertFalse(list(self.path.parent.glob('*.tmp')))
