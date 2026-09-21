"""Exercise safe failure modes using disposable repositories, never the user's Git data."""
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import repository_health_check as health


class RepositoryHealthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)

    def test_healthy_repository_passes(self):
        self.assertTrue(health.check_repository(self.root)["ok"])

    def test_truncated_object_fails_without_repair(self):
        oid = subprocess.check_output(
            ["git", "-C", str(self.root), "hash-object", "-w", "--stdin"],
            input=b"health-check-fixture",
        ).decode().strip()
        obj = self.root / ".git" / "objects" / oid[:2] / oid[2:]
        obj.chmod(0o600)  # Only this disposable test object's bytes are damaged.
        obj.write_bytes(b"")
        result = health.check_repository(self.root)
        self.assertFalse(result["ok"])
        self.assertNotEqual(result["fsck_exit"], 0)
        self.assertEqual(obj.read_bytes(), b"")

    def test_offload_detection_stops_before_fsck(self):
        original = Path.lstat
        target = self.root / ".git" / "HEAD"

        def lstat(path, *args, **kwargs):
            if path == target:
                return SimpleNamespace(st_flags=health.SF_DATALESS)
            return original(path, *args, **kwargs)

        with patch.object(Path, "lstat", lstat), patch.object(health.subprocess, "run") as run:
            result = health.check_repository(self.root)
        self.assertFalse(result["ok"])
        self.assertEqual(result["offloaded_paths"], [".git/HEAD"])
        run.assert_not_called()

    def test_timeout_is_not_a_pass(self):
        with patch.object(health.subprocess, "run", side_effect=subprocess.TimeoutExpired("git", 1)):
            self.assertFalse(health.check_repository(self.root, timeout=1)["ok"])


if __name__ == "__main__":
    unittest.main()
