import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agyswitch.auth import build_auth_url, extract_email_from_token, find_free_port
from agyswitch.quota import format_duration
from agyswitch.vault import Profile, delete_profile, list_profiles, load_profile, save_profile


class TestVault(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.patcher = patch("agyswitch.vault.PROFILES_DIR", Path(self.temp_dir.name))
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.temp_dir.cleanup()

    def test_profile_crud(self):
        p = Profile(
            name="test_account",
            email="test@gmail.com",
            access_token="acc_123",
            refresh_token="ref_123",
        )
        save_profile(p)
        loaded = load_profile("test_account")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.email, "test@gmail.com")
        self.assertEqual(loaded.access_token, "acc_123")
        self.assertEqual(loaded.refresh_token, "ref_123")

        all_profiles = list_profiles()
        self.assertEqual(len(all_profiles), 1)
        self.assertEqual(all_profiles[0].name, "test_account")

        deleted = delete_profile("test_account")
        self.assertTrue(deleted)
        self.assertIsNone(load_profile("test_account"))


class TestAuth(unittest.TestCase):
    def test_find_free_port(self):
        port = find_free_port()
        self.assertIsInstance(port, int)
        self.assertGreater(port, 1024)

    def test_build_auth_url(self):
        url, redirect_uri = build_auth_url(51123, "test_state")
        self.assertIn("http://127.0.0.1:51123", redirect_uri)
        self.assertIn("accounts.google.com", url)
        self.assertIn("prompt=select_account", url)
        self.assertIn("state=test_state", url)

    def test_extract_email_from_token(self):
        # Base64url encoded payload: {"email": "hello@example.com"}
        # {"email": "hello@example.com"} in base64 is eyJlbWFpbCI6ICJoZWxsb0BleGFtcGxlLmNvbSJ9
        dummy_id_token = "header.eyJlbWFpbCI6ICJoZWxsb0BleGFtcGxlLmNvbSJ9.sig"
        email = extract_email_from_token({"id_token": dummy_id_token})
        self.assertEqual(email, "hello@example.com")


class TestQuota(unittest.TestCase):
    def test_format_duration(self):
        self.assertEqual(format_duration(0), "Now")
        self.assertEqual(format_duration(120), "2m")
        self.assertEqual(format_duration(7200), "2h 0m")
        self.assertEqual(format_duration(7325), "2h 2m")
        self.assertEqual(format_duration(86400), "1d 0h")
        self.assertEqual(format_duration(580000), "6d 17h")


class TestCli(unittest.TestCase):
    def test_b_alias_for_best(self):
        from agyswitch.cli import cli

        self.assertIn("best", cli.commands)
        self.assertIn("b", cli.commands)


if __name__ == "__main__":
    unittest.main()
