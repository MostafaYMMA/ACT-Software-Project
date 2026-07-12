import os
import tempfile
import unittest
from unittest.mock import patch

import ui.athu as athu


class VerifyPasswordTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)

        self.config_dir = os.path.join(self.temp_dir.name, ".timecard_app")
        os.makedirs(self.config_dir, exist_ok=True)
        self.accounts_file = os.path.join(self.config_dir, "accounts.json")

        self.config_patcher = patch.object(athu, "CONFIG_DIR", self.config_dir)
        self.file_patcher = patch.object(athu, "ACCOUNTS_FILE", self.accounts_file)
        self.config_patcher.start()
        self.file_patcher.start()
        self.addCleanup(self.config_patcher.stop)
        self.addCleanup(self.file_patcher.stop)

    def test_verify_password(self):
        athu.save_account("Alice", "secret123")

        self.assertTrue(athu.verify_password("Alice", "secret123"))
        self.assertFalse(athu.verify_password("Alice", "wrong-pass"))
        self.assertFalse(athu.verify_password("Bob", "secret123"))


if __name__ == "__main__":
    unittest.main()
