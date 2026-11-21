"""Schedule matching for Hot Turkey.

Pure and platform independent: pass in a ``datetime`` and get a bool.

Schedule entry shape::

    {"enabled": bool, "days": [0..6], "start": "HH:MM", "end": "HH:MM"}

Days use Python weekday numbers (0 = Monday .. 6 = Sunday).

Window rules:
  * ``start == end``  -> the window is "all day" on the listed days.
  * ``start < end``   -> window on the listed days (e.g. 17:00-22:00).
  * ``start > end``   -> overnight window crossing midnight (e.g. 22:00-07:00).
    For the pre-dawn hours of the next day the *previous* day must be in
    the list; for the evening hours the *current* day must be listed.
"""

from datetime import datetime, time

from hot_turkey.config import parse_hhmm


def schedule_active(schedule: dict, now: datetime) -> bool:
    if not isinstance(schedule, dict) or not schedule.get("enabled"):
        return False
    try:
        days = set(schedule.get("days", []))
        sh, sm = parse_hhmm(schedule["start"])
        eh, em = parse_hhmm(schedule["end"])
    except (KeyError, TypeError, ValueError):
        return False
    if not days:
        return False

    start = time(sh, sm)
    end = time(eh, em)
    t = now.time()

    if start == end:
        return now.weekday() in days

    if start < end:
        return (start <= t < end) and (now.weekday() in days)

    # Overnight window.
    if t >= start:
        # Window started earlier today.
        return now.weekday() in days
    if t < end:
        # Window started yesterday (early-morning hours).
        return (now.weekday() - 1) % 7 in days
    return False


def any_schedule_active(schedules, now=None) -> bool:
    now = now or datetime.now()
    return any(schedule_active(s, now) for s in schedules)


def describe_schedule(schedule: dict) -> str:
    """Short human label, e.g. 'Mon-Fri 17:00-22:00'."""
    names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    try:
        days = sorted(set(schedule.get("days", [])))
        start = schedule["start"]
        end = schedule["end"]
    except (KeyError, TypeError):
        return "invalid schedule"
    if days == [0, 1, 2, 3, 4]:
        day_label = "Mon-Fri"
    elif days == [5, 6]:
        day_label = "Weekend"
    elif days == [0, 1, 2, 3, 4, 5, 6]:
        day_label = "Every day"
    else:
        day_label = ", ".join(names[d] for d in days if 0 <= d <= 6)
    return f"{day_label} {start}-{end}"
