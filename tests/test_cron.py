"""Tests for supervisor.cron — CronScheduler and parse_schedule."""
from __future__ import annotations

import datetime
import uuid
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import patch

import pytest

from supervisor.cron import CronScheduler, parse_schedule

UTC = datetime.timezone.utc

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dt(year=2025, month=1, day=6, hour=8, minute=0):
    """Monday 2025-01-06 08:00 UTC (weekday 0)."""
    return datetime.datetime(year, month, day, hour, minute, 0, tzinfo=UTC)


# 2025-01-06 is a Monday (weekday=0)
MONDAY    = datetime.datetime(2025, 1, 6,  8, 0, tzinfo=UTC)
TUESDAY   = datetime.datetime(2025, 1, 7,  8, 0, tzinfo=UTC)
WEDNESDAY = datetime.datetime(2025, 1, 8,  8, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# parse_schedule — weekdays
# ---------------------------------------------------------------------------

class TestParseWeekday:
    def test_every_monday_from_tuesday(self):
        """From Tuesday, next Monday is 6 days away."""
        dt, desc = parse_schedule("every monday", from_dt=TUESDAY)
        assert dt.weekday() == 0  # Monday
        assert dt > TUESDAY
        assert (dt - TUESDAY).days == 6

    def test_every_monday_at_09_from_tuesday(self):
        dt, desc = parse_schedule("every monday at 09:00", from_dt=TUESDAY)
        assert dt.weekday() == 0
        assert dt.hour == 9
        assert dt.minute == 0

    def test_every_monday_from_monday_before_time(self):
        """From Monday 08:00 asking for 'every monday at 09:00' → today at 09:00."""
        dt, desc = parse_schedule("every monday at 09:00", from_dt=MONDAY)
        assert dt.weekday() == 0
        assert dt.hour == 9
        assert dt.date() == MONDAY.date()

    def test_every_monday_from_monday_after_time(self):
        """From Monday 10:00 asking for 'every monday at 09:00' → next Monday."""
        from_dt = MONDAY.replace(hour=10)
        dt, desc = parse_schedule("every monday at 09:00", from_dt=from_dt)
        assert dt.weekday() == 0
        assert (dt - from_dt).days >= 6

    def test_every_monday_default_time(self):
        """No 'at HH:MM' defaults to 09:00."""
        dt, desc = parse_schedule("every monday", from_dt=TUESDAY)
        assert dt.hour == 9

    def test_weekly_on_monday_alias(self):
        """'weekly on monday' is an alias for 'every monday'."""
        dt1, _ = parse_schedule("every monday at 09:00", from_dt=TUESDAY)
        dt2, _ = parse_schedule("weekly on monday at 09:00", from_dt=TUESDAY)
        assert dt1 == dt2

    def test_every_friday(self):
        dt, _ = parse_schedule("every friday", from_dt=MONDAY)
        assert dt.weekday() == 4  # Friday
        assert (dt - MONDAY).days == 4

    def test_description(self):
        _, desc = parse_schedule("every wednesday at 10:30", from_dt=MONDAY)
        assert "Wednesday" in desc
        assert "10:30" in desc


# ---------------------------------------------------------------------------
# parse_schedule — daily
# ---------------------------------------------------------------------------

class TestParseDaily:
    def test_daily_before_time(self):
        """Before 08:30 today → fire today at 08:30."""
        from_dt = datetime.datetime(2025, 1, 6, 7, 0, tzinfo=UTC)
        dt, desc = parse_schedule("daily at 08:30", from_dt=from_dt)
        assert dt.date() == from_dt.date()
        assert dt.hour == 8
        assert dt.minute == 30

    def test_daily_after_time(self):
        """After 08:30 today → fire tomorrow at 08:30."""
        from_dt = datetime.datetime(2025, 1, 6, 9, 0, tzinfo=UTC)
        dt, desc = parse_schedule("daily at 08:30", from_dt=from_dt)
        assert dt.date() == (from_dt + datetime.timedelta(days=1)).date()
        assert dt.hour == 8
        assert dt.minute == 30

    def test_daily_description(self):
        _, desc = parse_schedule("daily at 08:30", from_dt=MONDAY)
        assert "Daily" in desc
        assert "08:30" in desc


# ---------------------------------------------------------------------------
# parse_schedule — every N hours / minutes / days
# ---------------------------------------------------------------------------

class TestParseInterval:
    def test_every_2_hours(self):
        dt, desc = parse_schedule("every 2 hours", from_dt=MONDAY)
        assert dt == MONDAY + datetime.timedelta(hours=2)
        assert "2 hours" in desc

    def test_every_1_hour(self):
        dt, desc = parse_schedule("every 1 hour", from_dt=MONDAY)
        assert dt == MONDAY + datetime.timedelta(hours=1)
        assert "1 hour" in desc  # singular

    def test_every_30_minutes(self):
        dt, desc = parse_schedule("every 30 minutes", from_dt=MONDAY)
        assert dt == MONDAY + datetime.timedelta(minutes=30)
        assert "30 minutes" in desc

    def test_every_1_minute(self):
        dt, desc = parse_schedule("every 1 minute", from_dt=MONDAY)
        assert dt == MONDAY + datetime.timedelta(minutes=1)
        assert "1 minute" in desc

    def test_every_3_days(self):
        dt, desc = parse_schedule("every 3 days", from_dt=MONDAY)
        base = MONDAY + datetime.timedelta(days=3)
        assert dt.date() == base.date()
        assert dt.hour == 9  # default time
        assert "3 days" in desc

    def test_every_3_days_at_time(self):
        dt, desc = parse_schedule("every 3 days at 14:00", from_dt=MONDAY)
        assert dt.hour == 14
        assert "14:00" in desc

    def test_every_1_day_singular(self):
        dt, desc = parse_schedule("every 1 day", from_dt=MONDAY)
        base = MONDAY + datetime.timedelta(days=1)
        assert dt.date() == base.date()
        assert "1 day" in desc


# ---------------------------------------------------------------------------
# parse_schedule — errors
# ---------------------------------------------------------------------------

class TestParseErrors:
    def test_invalid_expression(self):
        with pytest.raises(ValueError, match="Unrecognized"):
            parse_schedule("every other tuesday at noon")

    def test_empty_string(self):
        with pytest.raises(ValueError):
            parse_schedule("")

    def test_bad_time_format(self):
        with pytest.raises(ValueError):
            parse_schedule("daily at 25:00")

    def test_returns_utc_aware(self):
        dt, _ = parse_schedule("every monday", from_dt=MONDAY)
        assert dt.tzinfo is not None


# ---------------------------------------------------------------------------
# CronScheduler — add / list / delete
# ---------------------------------------------------------------------------

class TestCronSchedulerCRUD:
    def test_add_and_list(self, tmp_path):
        s = CronScheduler(tmp_path)
        job = s.add_job("Do something", "every monday at 09:00", chat_id=123)
        assert job["id"]
        assert job["description"] == "Do something"
        assert job["schedule_expr"] == "every monday at 09:00"
        assert job["chat_id"] == 123
        assert job["last_run_utc"] is None

        jobs = s.list_jobs()
        assert len(jobs) == 1
        assert jobs[0]["id"] == job["id"]

    def test_add_multiple(self, tmp_path):
        s = CronScheduler(tmp_path)
        s.add_job("Task A", "daily at 08:00", chat_id=1)
        s.add_job("Task B", "every 2 hours", chat_id=1)
        assert len(s.list_jobs()) == 2

    def test_delete_existing(self, tmp_path):
        s = CronScheduler(tmp_path)
        job = s.add_job("Task", "every monday", chat_id=1)
        result = s.delete_job(job["id"])
        assert result is True
        assert s.list_jobs() == []

    def test_delete_nonexistent(self, tmp_path):
        s = CronScheduler(tmp_path)
        result = s.delete_job("nonexistent-id")
        assert result is False

    def test_persistence(self, tmp_path):
        """Jobs persist across CronScheduler instances (same drive_root)."""
        s1 = CronScheduler(tmp_path)
        job = s1.add_job("Persisted", "daily at 09:00", chat_id=42)

        s2 = CronScheduler(tmp_path)
        jobs = s2.list_jobs()
        assert len(jobs) == 1
        assert jobs[0]["id"] == job["id"]

    def test_add_invalid_schedule_raises(self, tmp_path):
        s = CronScheduler(tmp_path)
        with pytest.raises(ValueError):
            s.add_job("Bad", "every other purple moon", chat_id=1)


# ---------------------------------------------------------------------------
# CronScheduler — tick
# ---------------------------------------------------------------------------

class TestCronSchedulerTick:
    def _make_due_job(self, past_dt: datetime.datetime) -> Dict[str, Any]:
        """Return a raw job dict with next_run_utc in the past."""
        return {
            "id": uuid.uuid4().hex,
            "description": "Test task",
            "schedule_expr": "every 1 hour",
            "human_desc": "Every 1 hour",
            "next_run_utc": past_dt.isoformat(),
            "chat_id": 99,
            "created_at": datetime.datetime.now(UTC).isoformat(),
            "last_run_utc": None,
        }

    def test_fires_due_jobs(self, tmp_path):
        s = CronScheduler(tmp_path)
        past = datetime.datetime.now(UTC) - datetime.timedelta(hours=2)
        job = s.add_job("Run me", "every 1 hour", chat_id=5)
        # Manually set next_run_utc to the past via direct save
        jobs = s.list_jobs()
        jobs[0]["next_run_utc"] = past.isoformat()
        s._save(jobs)

        enqueued: List[Dict] = []
        fired = s.tick(enqueue_fn=enqueued.append)

        assert fired == 1
        assert len(enqueued) == 1
        assert enqueued[0]["text"] == "Run me"
        assert enqueued[0]["chat_id"] == 5
        assert enqueued[0]["type"] == "task"

    def test_updates_last_run_and_next_run(self, tmp_path):
        s = CronScheduler(tmp_path)
        past = datetime.datetime.now(UTC) - datetime.timedelta(hours=2)
        s.add_job("Run me", "every 1 hour", chat_id=5)
        jobs = s.list_jobs()
        jobs[0]["next_run_utc"] = past.isoformat()
        s._save(jobs)

        before_tick = datetime.datetime.now(UTC)
        s.tick(enqueue_fn=lambda _: None)
        after_tick = datetime.datetime.now(UTC)

        updated = s.list_jobs()[0]
        last_run = datetime.datetime.fromisoformat(updated["last_run_utc"])
        next_run = datetime.datetime.fromisoformat(updated["next_run_utc"])

        assert before_tick <= last_run <= after_tick
        assert next_run > after_tick  # next run is in the future

    def test_does_not_fire_future_jobs(self, tmp_path):
        s = CronScheduler(tmp_path)
        s.add_job("Not yet", "every 1 hour", chat_id=1)  # next_run is ~1hr from now

        enqueued: List[Dict] = []
        fired = s.tick(enqueue_fn=enqueued.append)

        assert fired == 0
        assert enqueued == []

    def test_fires_multiple_due_jobs(self, tmp_path):
        s = CronScheduler(tmp_path)
        past = datetime.datetime.now(UTC) - datetime.timedelta(minutes=5)

        for i in range(3):
            s.add_job(f"Task {i}", "every 1 hour", chat_id=i)
        # Push all next_run to the past
        jobs = s.list_jobs()
        for j in jobs:
            j["next_run_utc"] = past.isoformat()
        s._save(jobs)

        enqueued: List[Dict] = []
        fired = s.tick(enqueue_fn=enqueued.append)

        assert fired == 3
        assert len(enqueued) == 3

    def test_tick_on_empty_scheduler(self, tmp_path):
        s = CronScheduler(tmp_path)
        fired = s.tick(enqueue_fn=lambda _: None)
        assert fired == 0


# ---------------------------------------------------------------------------
# Timezone support
# ---------------------------------------------------------------------------

class TestTimezone:
    """Tests for timezone-aware scheduling."""

    def test_daily_in_utc3_before_local_time(self):
        """
        User in UTC+3. It's 08:00 UTC (= 11:00 local).
        'daily at 12:00' → today at 12:00 local = 09:00 UTC (still in future).
        """
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Europe/Moscow")  # UTC+3
        # 2025-01-06 08:00 UTC = 11:00 Moscow
        from_dt = datetime.datetime(2025, 1, 6, 8, 0, tzinfo=UTC)
        dt, desc = parse_schedule("daily at 12:00", from_dt=from_dt, tz=tz)
        assert dt == datetime.datetime(2025, 1, 6, 9, 0, tzinfo=UTC)  # 12:00 Moscow = 09:00 UTC
        assert "Europe/Moscow" in desc

    def test_daily_in_utc3_after_local_time(self):
        """
        User in UTC+3. It's 12:00 UTC (= 15:00 local).
        'daily at 12:00' → tomorrow at 12:00 local = tomorrow 09:00 UTC.
        """
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Europe/Moscow")  # UTC+3
        from_dt = datetime.datetime(2025, 1, 6, 12, 0, tzinfo=UTC)
        dt, desc = parse_schedule("daily at 12:00", from_dt=from_dt, tz=tz)
        assert dt == datetime.datetime(2025, 1, 7, 9, 0, tzinfo=UTC)  # next day 12:00 Moscow = 09:00 UTC
        assert "Europe/Moscow" in desc

    def test_weekday_in_utc3(self):
        """
        Weekday scheduling should respect timezone.
        """
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Europe/Moscow")
        from_dt = datetime.datetime(2025, 1, 7, 22, 0, tzinfo=UTC)  # Tuesday 22:00 UTC = Wednesday 01:00 Moscow
        dt, desc = parse_schedule("every wednesday at 10:00", from_dt=from_dt, tz=tz)
        # Wednesday 10:00 Moscow = Wednesday 07:00 UTC
        # from_dt is already Wednesday 01:00 Moscow, so 10:00 is still ahead today
        assert dt.weekday() == 2  # Wednesday UTC
        assert dt.hour == 7  # 10:00 Moscow = 07:00 UTC
        assert "Europe/Moscow" in desc

    def test_utc_no_suffix(self):
        """UTC timezone → no suffix in human_desc."""
        dt, desc = parse_schedule("daily at 09:00", from_dt=MONDAY)
        assert "UTC" not in desc or desc == "Daily at 09:00"
        # More precise check: no parenthetical
        assert "(" not in desc

    def test_default_tz_utc(self):
        """Without TZ env, default is UTC."""
        import os
        os.environ.pop("TZ", None)
        from supervisor.cron import _get_default_tz
        import importlib, supervisor.cron
        importlib.reload(supervisor.cron)
        # Re-import to get fresh version
        from supervisor.cron import _get_default_tz as get_tz
        result = get_tz()
        assert result == datetime.timezone.utc or str(result) == "UTC"

    def test_tz_env_sets_timezone(self, monkeypatch):
        """TZ env var is respected."""
        monkeypatch.setenv("TZ", "Europe/Moscow")
        from supervisor.cron import _get_default_tz
        import importlib, supervisor.cron
        importlib.reload(supervisor.cron)
        from supervisor.cron import _get_default_tz as get_tz
        tz = get_tz()
        from zoneinfo import ZoneInfo
        assert str(tz) == "Europe/Moscow"
