import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from hot_turkey import config as cfg


class Normalizers(unittest.TestCase):
    def test_normalize_site(self):
        self.assertEqual(cfg.normalize_site("https://www.youtube.com/watch?v=1"), "youtube.com")
        self.assertEqual(cfg.normalize_site("reddit.com"), "reddit.com")
        self.assertEqual(cfg.normalize_site("www.netflix.com/"), "netflix.com")
        self.assertEqual(cfg.normalize_site("  Twitch.TV/"), "twitch.tv")
        self.assertEqual(cfg.normalize_site(""), "")
        self.assertEqual(cfg.normalize_site("not a site"), "")
        self.assertEqual(cfg.normalize_site("localhost"), "")
        self.assertEqual(cfg.normalize_site("192.168.0.1"), "")

    def test_normalize_app(self):
        self.assertEqual(cfg.normalize_app("steam"), "steam.exe")
        self.assertEqual(cfg.normalize_app("Steam.exe"), "steam.exe")
        self.assertEqual(cfg.normalize_app("D:\\Games\\Epic\\EPICGAMESLAUNCHER.exe"), "epicgameslauncher.exe")
        self.assertEqual(cfg.normalize_app(""), "")
        self.assertEqual(cfg.normalize_app("???"), "")


class ScheduleNormalize(unittest.TestCase):
    def test_valid(self):
        s = cfg.normalize_schedule({"enabled": True, "days": [0, 4], "start": "17:00", "end": "22:00"})
        self.assertEqual(s, {"enabled": True, "days": [0, 4], "start": "17:00", "end": "22:00"})

    def test_bad_time_or_days(self):
        self.assertIsNone(cfg.normalize_schedule({"start": "99:00", "end": "22:00", "days": [0]}))
        self.assertIsNone(cfg.normalize_schedule({"start": "17:00", "end": "22:00", "days": []}))
        self.assertIsNone(cfg.normalize_schedule({"start": "17:00", "end": "22:00", "days": [7]}))

    def test_days_remapped_and_deduped(self):
        s = cfg.normalize_schedule({"days": [2, 0, 2, 6], "start": "8:00", "end": "9:30"})
        self.assertEqual(s["days"], [0, 2, 6])
        self.assertEqual(s["start"], "08:00")


class LoadSave(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = Path(self.tmp) / "config.json"

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_missing_file_creates_defaults(self):
        loaded = cfg.load_config(self.path)
        self.assertEqual(loaded["sites"], cfg.DEFAULT_SITES)
        self.assertTrue(self.path.exists())

    def test_roundtrip(self):
        loaded = cfg.load_config(self.path)
        loaded["sites"] = ["example.com"]
        loaded["apps"] = ["test.exe"]
        cfg.save_config(loaded, self.path)
        again = cfg.load_config(self.path)
        self.assertEqual(again["sites"], ["example.com"])
        self.assertEqual(again["apps"], ["test.exe"])

    def test_corrupt_file_backed_up(self):
        self.path.write_text("{not json!!!", encoding="utf-8")
        loaded = cfg.load_config(self.path)
        self.assertEqual(loaded["sites"], cfg.DEFAULT_SITES)
        backup = self.path.with_suffix(".broken.json")
        self.assertTrue(backup.exists())

    def test_state_roundtrip(self):
        state_path = self.path.parent / "state.json"
        state = {"mode": "timed", "source": "manual", "started_at": 1.0, "ends_at": 2.0}
        cfg.save_state(state, state_path)
        self.assertEqual(cfg.load_state(state_path), state)
        cfg.clear_state(state_path)
        self.assertIsNone(cfg.load_state(state_path))

    def test_state_invalid_mode_rejected(self):
        state_path = self.path.parent / "state.json"
        state_path.write_text('{"mode":"bogus"}', encoding="utf-8")
        self.assertIsNone(cfg.load_state(state_path))


if __name__ == "__main__":
    unittest.main()
