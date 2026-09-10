import io
import json
import unittest
from unittest.mock import patch
import urllib.error

from autochzzk_core import chzzk_api as api


class ApiTests(unittest.TestCase):
    def live(self, payload):
        with patch.object(api.urllib.request, 'urlopen', return_value=io.StringIO(json.dumps(payload))):
            return api.get_live_status('a' * 32)

    def test_only_explicit_valid_states_are_accepted(self):
        for status, expected in [('OPEN', True), ('CLOSE', False)]:
            self.assertEqual(self.live({'code': 200, 'content': {'status': status, 'liveTitle': 'test'}}), (expected, 'test'))

    def test_invalid_responses_are_retryable_not_offline(self):
        for payload in [None, [], {}, {'code': 500, 'content': {'status': 'CLOSE'}}, {'code': 200, 'content': None}, {'code': 200, 'content': [1]}, {'code': 200, 'content': {}}, {'code': 200, 'content': {'status': 'UNKNOWN'}}, {'code': 200, 'content': {'status': 'OPEN', 'liveTitle': []}}]:
            with self.subTest(payload=payload), self.assertRaises(urllib.error.URLError):
                self.live(payload)

    def test_invalid_json_is_retryable(self):
        with patch.object(api.urllib.request, 'urlopen', return_value=io.StringIO('[')), self.assertRaises(api.ApiResponseError):
            api.get_live_status('a' * 32)

    def test_channel_name_must_be_text(self):
        with patch.object(api, 'request_content', return_value={'channelName': [1]}), self.assertRaises(api.ApiResponseError):
            api.get_channel_name('a' * 32)

    def test_release_requires_object(self):
        for source in ['null', '[]', '123', '[']:
            with self.subTest(source=source), patch.object(api.urllib.request, 'urlopen', return_value=io.StringIO(source)), self.assertRaises(api.ApiResponseError):
                api.get_latest_release()

    def test_release_preserves_valid_metadata(self):
        release = {'tag_name': 'v1.0.0', 'assets': []}
        with patch.object(api.urllib.request, 'urlopen', return_value=io.StringIO(json.dumps(release))):
            self.assertEqual(api.get_latest_release(), release)
