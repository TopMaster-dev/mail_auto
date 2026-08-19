"""
Tests for the password change helper.

The bug these guard against: a bcrypt hash contains `$`, so routing one through
a shell corrupts it, and a corrupted hash is indistinguishable from a wrong
password at the login screen.
"""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import bcrypt

from admin.change_password import _KEY, _write_hash


def _hash_in(env_path: Path) -> str:
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if line.startswith(_KEY):
            return line.split("=", 1)[1].strip()
    return ""


class TestWriteHash(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.env = Path(self._tmp.name) / ".env"

    def tearDown(self):
        self._tmp.cleanup()

    def test_replaces_existing_line_and_verifies(self):
        self.env.write_text(
            "GMAIL_ADDRESS=a@b.c\n"
            f"{_KEY}=$2b$12$oldoldoldoldoldoldoldoldoldoldoldoldoldoldoldoldoldold\n"
            "FLASK_SECRET_KEY=xyz\n", encoding="utf-8")
        new = bcrypt.hashpw(b"correct horse", bcrypt.gensalt(rounds=4)).decode()
        _write_hash(self.env, new)

        stored = _hash_in(self.env)
        self.assertEqual(stored, new)
        self.assertTrue(bcrypt.checkpw(b"correct horse", stored.encode()))

    def test_dollar_signs_survive_intact(self):
        # The whole point: $2b$12$ must land verbatim, not be eaten as $2/$1.
        self.env.write_text(f"{_KEY}=placeholder\n", encoding="utf-8")
        new = bcrypt.hashpw(b"pw", bcrypt.gensalt(rounds=4)).decode()
        _write_hash(self.env, new)

        stored = _hash_in(self.env)
        self.assertEqual(stored.count("$"), 3)
        self.assertTrue(stored.startswith("$2"))
        self.assertEqual(len(stored), 60)

    def test_other_lines_are_untouched(self):
        self.env.write_text(
            "GMAIL_ADDRESS=a@b.c\n"
            f"{_KEY}=old\n"
            "SPREADSHEET_ID=sheet-123\n", encoding="utf-8")
        _write_hash(self.env, bcrypt.hashpw(b"pw", bcrypt.gensalt(rounds=4)).decode())

        text = self.env.read_text(encoding="utf-8")
        self.assertIn("GMAIL_ADDRESS=a@b.c", text)
        self.assertIn("SPREADSHEET_ID=sheet-123", text)

    def test_appends_when_key_absent(self):
        self.env.write_text("GMAIL_ADDRESS=a@b.c\n", encoding="utf-8")
        new = bcrypt.hashpw(b"pw", bcrypt.gensalt(rounds=4)).decode()
        _write_hash(self.env, new)
        self.assertEqual(_hash_in(self.env), new)

    def test_file_permissions_are_preserved(self):
        # Truncate-in-place rather than rename: a fresh file would come back
        # with default permissions instead of 600.
        self.env.write_text(f"{_KEY}=old\n", encoding="utf-8")
        self.env.chmod(0o600)
        before = self.env.stat().st_mode
        _write_hash(self.env, bcrypt.hashpw(b"pw", bcrypt.gensalt(rounds=4)).decode())
        self.assertEqual(self.env.stat().st_mode, before)


if __name__ == "__main__":
    unittest.main()
