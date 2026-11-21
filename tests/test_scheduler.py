import os
import sys
import unittest
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from hot_turkey.config import parse_hhmm
from hot_turkey.scheduler import any_schedule_active, describe_schedule, schedule_active


def at(day, weekday, hh, mm):
    """Build a datetime for a given real weekday, e.g. at("2026-11-23", 3, 18, 30)."""
    return datetime.strptime(f"{day} {hh:02d}:{mm:02d}", "%Y-%m-%d %H:%M")


# 2026-11-23 is a Monday? Check: 2026-11-23...
# Known anchor: 2026-11-21 (today) — we verify empirically.
SAT = 5  # 0=Monday ... 6=Sunday


class ParseHHMM(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(parse_hhmm("17:00"), (17, 0))
        self.assertEqual(parse_hhmm("9:30"), (9, 30))
        self.assertEqual(parse_hhmm("23:59"), (23, 59))

    def test_invalid(self):
        for bad in ["", "17", "17:60", "24:00", "x", "17:00:00", None]:
            with self.assertRaises(ValueError, msg=bad):
                parse_hhmm(bad)


# A *real* date with a known weekday: 2026-11-23 is a Monday.
MON = "2026-11-23"
TUE = "2026-11-24"
SUN = "2026-11-22"  # Sunday (weekday 6)


class ScheduleActive(unittest.TestCase):
    def test_overnight_evening_window(self):
        # Monday 22:00 -> Tuesday 07:00, Monday listed
        sched = {"enabled": True, "days": [0], "start": "22:00", "end": "07:00"}
        self.assertTrue(schedule_active(sched, at(MON, 0, 23, 0)))   # Monday 23:00
        self.assertFalse(schedule_active(sched, at(MON, 0, 10, 0)))  # Monday 10:00

    def test_overnight_early_morning_next_day(self):
        # Monday 22:00 -> Tuesday 07:00: Tuesday 03:00 is inside the window
        sched = {"enabled": True, "days": [0], "start": "22:00", "end": "07:00"}
        self.assertTrue(schedule_active(sched, at(TUE, 1, 3, 0)))

    def test_overnight_unlisted_previous_day(self):
        # No Monday listed: Tuesday 03:00 is NOT inside
        sched = {"enabled": True, "days": [1], "start": "22:00", "end": "07:00"}
        self.assertFalse(schedule_active(sched, at(TUE, 1, 3, 0)))

    def test_non_overnight(self):
        sched = {"enabled": True, "days": [0, 1, 2, 3, 4], "start": "17:00", "end": "22:00"}
        self.assertTrue(schedule_active(sched, at(MON, 0, 19, 30)))
        self.assertFalse(schedule_active(sched, at(MON, 0, 16, 59)))
        self.assertFalse(schedule_active(sched, at(MON, 0, 22, 0)))  # end exclusive

    def test_at_start_boundary(self):
        sched = {"enabled": True, "days": [0], "start": "17:00", "end": "22:00"}
        self.assertTrue(schedule_active(sched, at(MON, 0, 17, 0)))

    def test_all_day_start_equals_end(self):
        sched = {"enabled": True, "days": [0, 1], "start": "00:00", "end": "00:00"}
        self.assertTrue(schedule_active(sched, at(MON, 0, 13, 0)))
        self.assertFalse(schedule_active(sched, at(SUN, 6, 13, 0)))

    def test_disabled(self):
        sched = {"enabled": False, "days": [0], "start": "17:00", "end": "22:00"}
        self.assertFalse(schedule_active(sched, at(MON, 0, 19, 0)))

    def test_empty_days(self):
        sched = {"enabled": True, "days": [], "start": "17:00", "end": "22:00"}
        self.assertFalse(schedule_active(sched, at(MON, 0, 19, 0)))

    def test_invalid_times(self):
        sched = {"enabled": True, "days": [0], "start": "25:00", "end": "22:00"}
        self.assertFalse(schedule_active(sched, at(MON, 0, 19, 0)))

    def test_any_schedule_active(self):
        schedules = [
            {"enabled": True, "days": [0], "start": "17:00", "end": "22:00"},
            {"enabled": True, "days": [1], "start": "17:00", "end": "22:00"},
        ]
        self.assertTrue(any_schedule_active(schedules, at(MON, 0, 19, 0)))
        self.assertFalse(any_schedule_active(schedules, at(MON, 0, 10, 0)))

    def test_describe_schedule(self):
        s = {"enabled": True, "days": [0, 1, 2, 3, 4], "start": "17:00", "end": "22:00"}
        self.assertEqual(describe_schedule(s), "Mon-Fri 17:00-22:00")
        s = {"enabled": True, "days": [5, 6], "start": "22:00", "end": "07:00"}
        self.assertEqual(describe_schedule(s), "Weekend 22:00-07:00")


if __name__ == "__main__":
    unittest.main()
