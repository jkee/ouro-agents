"""
Cron-style task scheduler for Ouro.

Supported schedule expressions:
  - "every monday [at 09:00]"
  - "weekly on monday [at 09:00]"
  - "daily at 09:00"
  - "every 2 hours"
  - "every 30 minutes"
  - "every 3 days [at 09:00]"
"""
from __future__ import annotations

import datetime
import json
import logging
import re
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

CRON_FILE = "state/cron_tasks.json"

_WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}

_WEEKDAY_PATTERN = "|".join(_WEEKDAYS.keys())


def _parse_time(time_str: str) -> Tuple[int, int]:
    """Parse HH:MM into (hour, minute). Defaults to 09:00 if empty/None."""
    if not time_str:
        return 9, 0
    m = re.match(r"^(\d{1,2}):(\d{2})$", time_str.strip())
    if not m:
        raise ValueError(f"Invalid time format: {time_str!r}. Expected HH:MM.")
    h, mi = int(m.group(1)), int(m.group(2))
    if not (0 <= h <= 23 and 0 <= mi <= 59):
        raise ValueError(f"Time out of range: {time_str!r}.")
    return h, mi


def parse_schedule(expr: str, from_dt: Optional[datetime.datetime] = None) -> Tuple[datetime.datetime, str]:
    """
    Parse a human-friendly schedule expression.
    Returns (next_run_utc, human_readable_description).
    Raises ValueError with a helpful message if the expression is unrecognized.
    """
    if from_dt is None:
        from_dt = datetime.datetime.now(datetime.timezone.utc)
    # Ensure from_dt is UTC-aware
    if from_dt.tzinfo is None:
        from_dt = from_dt.replace(tzinfo=datetime.timezone.utc)

    s = expr.strip().lower()

    # "every monday [at HH:MM]" or "weekly on monday [at HH:MM]"
    m = re.match(
        rf"^(?:every|weekly\s+on)\s+({_WEEKDAY_PATTERN})(?:\s+at\s+(\d{{1,2}}:\d{{2}}))?$",
        s,
    )
    if m:
        day_name = m.group(1)
        time_str = m.group(2) or "09:00"
        h, mi = _parse_time(time_str)
        target_wd = _WEEKDAYS[day_name]
        current_wd = from_dt.weekday()
        days_ahead = target_wd - current_wd

        candidate = from_dt.replace(hour=h, minute=mi, second=0, microsecond=0)
        if days_ahead == 0:
            # Same weekday: fire today if we're still before the target time
            next_run = candidate if from_dt < candidate else candidate + datetime.timedelta(weeks=1)
        elif days_ahead > 0:
            next_run = candidate + datetime.timedelta(days=days_ahead)
        else:
            next_run = candidate + datetime.timedelta(days=days_ahead + 7)

        desc = f"Every {day_name.capitalize()} at {time_str}"
        return next_run, desc

    # "daily at HH:MM"
    m = re.match(r"^daily\s+at\s+(\d{1,2}:\d{2})$", s)
    if m:
        time_str = m.group(1)
        h, mi = _parse_time(time_str)
        candidate = from_dt.replace(hour=h, minute=mi, second=0, microsecond=0)
        next_run = candidate if from_dt < candidate else candidate + datetime.timedelta(days=1)
        desc = f"Daily at {time_str}"
        return next_run, desc

    # "every N hours"
    m = re.match(r"^every\s+(\d+)\s+hours?$", s)
    if m:
        n = int(m.group(1))
        if n < 1:
            raise ValueError("Interval must be at least 1 hour.")
        next_run = from_dt + datetime.timedelta(hours=n)
        desc = f"Every {n} hour{'s' if n != 1 else ''}"
        return next_run, desc

    # "every N minutes"
    m = re.match(r"^every\s+(\d+)\s+minutes?$", s)
    if m:
        n = int(m.group(1))
        if n < 1:
            raise ValueError("Interval must be at least 1 minute.")
        next_run = from_dt + datetime.timedelta(minutes=n)
        desc = f"Every {n} minute{'s' if n != 1 else ''}"
        return next_run, desc

    # "every N days [at HH:MM]"
    m = re.match(r"^every\s+(\d+)\s+days?(?:\s+at\s+(\d{1,2}:\d{2}))?$", s)
    if m:
        n = int(m.group(1))
        if n < 1:
            raise ValueError("Interval must be at least 1 day.")
        time_str = m.group(2) or "09:00"
        h, mi = _parse_time(time_str)
        base = from_dt + datetime.timedelta(days=n)
        next_run = base.replace(hour=h, minute=mi, second=0, microsecond=0)
        desc = f"Every {n} day{'s' if n != 1 else ''}" + (f" at {time_str}" if m.group(2) else "")
        return next_run, desc

    raise ValueError(
        f"Unrecognized schedule expression: {expr!r}. "
        "Supported formats: 'every monday [at HH:MM]', 'weekly on monday [at HH:MM]', "
        "'daily at HH:MM', 'every N hours', 'every N minutes', 'every N days [at HH:MM]'."
    )


class CronScheduler:
    """Thread-safe cron job scheduler persisted to JSON."""

    def __init__(self, drive_root: Path):
        self._drive_root = drive_root
        self._lock = threading.Lock()
        self._path = drive_root / CRON_FILE

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_job(self, description: str, schedule_expr: str, chat_id: int) -> Dict[str, Any]:
        """Add a new scheduled job. Returns the job dict."""
        next_run, human_desc = parse_schedule(schedule_expr)
        job: Dict[str, Any] = {
            "id": uuid.uuid4().hex,
            "description": description,
            "schedule_expr": schedule_expr,
            "human_desc": human_desc,
            "next_run_utc": next_run.isoformat(),
            "chat_id": chat_id,
            "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "last_run_utc": None,
        }
        with self._lock:
            jobs = self._load()
            jobs.append(job)
            self._save(jobs)
        log.info("Cron job added: %s (%s) → next %s", job["id"], schedule_expr, next_run.isoformat())
        return job

    def list_jobs(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._load())

    def delete_job(self, job_id: str) -> bool:
        with self._lock:
            jobs = self._load()
            filtered = [j for j in jobs if j["id"] != job_id]
            if len(filtered) == len(jobs):
                return False
            self._save(filtered)
        log.info("Cron job deleted: %s", job_id)
        return True

    def tick(self, enqueue_fn) -> int:
        """
        Check all jobs. Enqueue any that are due (next_run_utc <= now).
        Updates last_run_utc and computes new next_run_utc for fired jobs.
        Returns number of jobs fired.
        """
        now = datetime.datetime.now(datetime.timezone.utc)
        fired = 0
        with self._lock:
            jobs = self._load()
            for job in jobs:
                try:
                    next_run = datetime.datetime.fromisoformat(job["next_run_utc"])
                    if next_run.tzinfo is None:
                        next_run = next_run.replace(tzinfo=datetime.timezone.utc)
                except (KeyError, ValueError):
                    log.warning("Cron job %s has invalid next_run_utc, skipping", job.get("id"))
                    continue

                if next_run <= now:
                    task = {
                        "id": uuid.uuid4().hex,
                        "type": "task",
                        "chat_id": job["chat_id"],
                        "text": job["description"],
                    }
                    try:
                        enqueue_fn(task)
                        fired += 1
                        log.info("Cron fired job %s: %s", job["id"], job["description"][:80])
                    except Exception:
                        log.warning("Cron enqueue failed for job %s", job["id"], exc_info=True)
                        continue

                    job["last_run_utc"] = now.isoformat()
                    try:
                        new_next, _ = parse_schedule(job["schedule_expr"], from_dt=now)
                        job["next_run_utc"] = new_next.isoformat()
                    except Exception:
                        log.warning("Cron failed to recompute next_run for job %s", job["id"], exc_info=True)

            self._save(jobs)
        return fired

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load(self) -> List[Dict[str, Any]]:
        """Load jobs from disk. Must be called with self._lock held."""
        if not self._path.exists():
            return []
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except Exception:
            log.warning("Failed to read cron file, starting fresh", exc_info=True)
            return []

    def _save(self, jobs: List[Dict[str, Any]]) -> None:
        """Save jobs to disk atomically. Must be called with self._lock held."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(jobs, indent=2, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self._path)
        except Exception:
            log.error("Failed to save cron file", exc_info=True)
            tmp.unlink(missing_ok=True)
