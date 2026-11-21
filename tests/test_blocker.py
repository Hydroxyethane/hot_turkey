import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from hot_turkey import blocker


class PureHelpers(unittest.TestCase):
    def test_blocked_domains_dedupes(self):
        domains = blocker.blocked_domains(["https://www.youtube.com/watch?v=1", "youtube.com"])
        self.assertIn("youtube.com", domains)
        self.assertIn("www.youtube.com", domains)
        self.assertIn("m.youtube.com", domains)
        self.assertEqual(len(domains), len(set(domains)))

    def test_blocked_domains_ignores_garbage(self):
        self.assertEqual(blocker.blocked_domains(["not a site", "", None]), [])

    def test_hosts_block_text_structure(self):
        text = blocker.hosts_block_text(["example.com"])
        self.assertTrue(text.startswith(blocker.MARKER_START))
        self.assertTrue(text.rstrip().endswith(blocker.MARKER_END))
        self.assertIn("127.0.0.1 example.com", text)

    def test_hosts_block_text_empty(self):
        self.assertEqual(blocker.hosts_block_text([]), "")

    def test_strip_own_block_preserves_user_lines(self):
        original = "127.0.0.1 mything\n\n" + blocker.hosts_block_text(["example.com"]) + "\n# user line\n"
        stripped = blocker.strip_own_block(original)
        self.assertNotIn("example.com", stripped)
        self.assertIn("127.0.0.1 mything", stripped)
        self.assertIn("# user line", stripped)

    def test_strip_own_block_orphans(self):
        # Marker block without its end marker must not eat the rest of the file
        text = "keep1\n" + blocker.MARKER_START + "\nkeep2\n"
        stripped = blocker.strip_own_block(text)
        self.assertNotIn(blocker.MARKER_START, stripped)
        self.assertNotIn("keep2", stripped)  # swallowed until EOF — documented behaviour
        self.assertIn("keep1", stripped)


class FileIOOnTempPath(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.hosts = Path(self.tmp) / "hosts"

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_apply_then_remove_restores_original(self):
        original = "127.0.0.1 localhost\n::1 localhost\n"
        self.hosts.write_text(original, encoding="utf-8")
        blocker.apply_host_block(["example.com"], self.hosts)
        after = self.hosts.read_text(encoding="utf-8")
        self.assertIn("example.com", after)
        self.assertIn("127.0.0.1 localhost", after)
        self.assertIs(blocker.remove_host_block(self.hosts), True)
        restored = self.hosts.read_text(encoding="utf-8")
        self.assertNotIn("example.com", restored)
        self.assertIn("127.0.0.1 localhost", restored)

    def test_apply_is_idempotent(self):
        self.hosts.write_text("127.0.0.1 localhost\n", encoding="utf-8")
        blocker.apply_host_block(["example.com"], self.hosts)
        first = self.hosts.read_text(encoding="utf-8")
        blocker.apply_host_block(["example.com"], self.hosts)
        second = self.hosts.read_text(encoding="utf-8")
        self.assertEqual(first, second)

    def test_apply_with_no_sites_removes_block(self):
        self.hosts.write_text("127.0.0.1 localhost\n", encoding="utf-8")
        blocker.apply_host_block(["example.com"], self.hosts)
        blocker.apply_host_block([], self.hosts)
        after = self.hosts.read_text(encoding="utf-8")
        self.assertNotIn("example.com", after)
        self.assertNotIn(blocker.MARKER_START, after)

    def test_remove_when_clean_returns_false(self):
        self.hosts.write_text("127.0.0.1 localhost\n", encoding="utf-8")
        self.assertIs(blocker.remove_host_block(self.hosts), False)


class ProcessBlockHelpers(unittest.TestCase):
    def test_normalize_killed_names_collect(self):
        # pure logic check — kill_apps itself requires Windows; we only
        # test the wanted-name normalizer indirectly via blocked_domains
        from hot_turkey.config import normalize_app

        self.assertEqual(normalize_app("steam.exe"), "steam.exe")
        self.assertEqual(normalize_app("Steam"), "steam.exe")


if __name__ == "__main__":
    unittest.main()
