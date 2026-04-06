"""
Supervisor — Cron scheduler.

Persistent recurring tasks: store, due-checking, main-loop integration.
Cron data lives at /data/crons.json with its own file lock.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import pathlib
import uuid
from typing import Any, Callable, Dict, List, Optional

from croniter import croniter

from supervisor.state import (
    acquire_file_lock, release_file_lock, atomic_write_text, append_jsonl,
    EVOLUTION_BUDGET_RESERVE,
    CRON_BUDGET_RESERVE,
)
from supervisor.telegram import send_with_budget

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Module-level config (set via init())
# ---------------------------------------------------------------------------
DRIVE_ROOT: pathlib.Path = pathlib.Path(os.environ.get("DRIVE_ROOT", "/data"))
CRONS_PATH: pathlib.Path = DRIVE_ROOT / "crons.json"
CRONS_LOCK_PATH: pathlib.Path = DRIVE_ROOT / "locks" / "crons.lock"


def init(drive_root: pathlib.Path) -> None:
    global DRIVE_ROOT, CRONS_PATH, CRONS_LOCK_PATH
    DRIVE_ROOT = drive_root
    CRONS_PATH = drive_root / "crons.json"
    CRONS_LOCK_PATH = drive_root / "locks" / "crons.lock"


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def load_crons() -> List[Dict[str, Any]]:
    """Load crons list from disk. Returns empty list on missing/corrupt file."""
    try:
        if not CRONS_PATH.exists():
            return []
        data = json.loads(CRONS_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            crons = data.get("crons")
            return crons if isinstance(crons, list) else []
        return []
    except Exception:
        log.warning("Failed to load crons from %s", CRONS_PATH, exc_info=True)
        return []


def save_crons(crons: List[Dict[str, Any]]) -> None:
    """Save crons list to disk with file lock + atomic write."""
    lock_fd = acquire_file_lock(CRONS_LOCK_PATH)
    try:
        payload = json.dumps({"crons": crons}, ensure_ascii=False, indent=2)
        atomic_write_text(CRONS_PATH, payload)
    finally:
        release_file_lock(CRONS_LOCK_PATH, lock_fd)


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def add_cron(expression: str, description: str, notify: bool = False) -> Dict[str, Any]:
    """Add a new cron. Validates expression via croniter. Returns the new entry."""
    expression = str(expression).strip()
    description = str(description).strip()
    if not expression:
        raise ValueError("Cron expression cannot be empty")
    if not description:
        raise ValueError("Description cannot be empty")
    if not croniter.is_valid(expression):
        raise ValueError(f"Invalid cron expression: {expression}")

    entry = {
        "id": uuid.uuid4().hex[:8],
        "expression": expression,
        "description": description,
        "enabled": True,
        "notify": bool(notify),
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "last_fired_at": None,
        "last_task_id": None,
        "fire_count": 0,
    }

    lock_fd = acquire_file_lock(CRONS_LOCK_PATH)
    try:
        crons = load_crons()
        crons.append(entry)
        payload = json.dumps({"crons": crons}, ensure_ascii=False, indent=2)
        atomic_write_text(CRONS_PATH, payload)
    finally:
        release_file_lock(CRONS_LOCK_PATH, lock_fd)

    return entry


def remove_cron(cron_id: str) -> bool:
    """Remove cron by ID. Returns True if found and removed."""
    lock_fd = acquire_file_lock(CRONS_LOCK_PATH)
    try:
        crons = load_crons()
        original_len = len(crons)
        crons = [c for c in crons if c.get("id") != cron_id]
        if len(crons) == original_len:
            return False
        payload = json.dumps({"crons": crons}, ensure_ascii=False, indent=2)
        atomic_write_text(CRONS_PATH, payload)
        return True
    finally:
        release_file_lock(CRONS_LOCK_PATH, lock_fd)


def toggle_cron(cron_id: str, enabled: Optional[bool] = None) -> Optional[Dict[str, Any]]:
    """Toggle or set enabled state. Returns updated cron or None if not found."""
    lock_fd = acquire_file_lock(CRONS_LOCK_PATH)
    try:
        crons = load_crons()
        target = None
        for c in crons:
            if c.get("id") == cron_id:
                target = c
                break
        if target is None:
            return None
        if enabled is None:
            target["enabled"] = not target.get("enabled", True)
        else:
            target["enabled"] = bool(enabled)
        payload = json.dumps({"crons": crons}, ensure_ascii=False, indent=2)
        atomic_write_text(CRONS_PATH, payload)
        return dict(target)
    finally:
        release_file_lock(CRONS_LOCK_PATH, lock_fd)


def list_crons() -> List[Dict[str, Any]]:
    """Return all crons."""
    return load_crons()


# ---------------------------------------------------------------------------
# Due-checking + enqueue (called from launcher main loop)
# ---------------------------------------------------------------------------

def _is_due(cron: Dict[str, Any], now: datetime.datetime) -> bool:
    """Check if a cron should fire based on its expression and last_fired_at."""
    expression = cron.get("expression", "")
    last_fired = cron.get("last_fired_at")

    if last_fired:
        try:
            base = datetime.datetime.fromisoformat(str(last_fired).replace("Z", "+00:00"))
        except Exception:
            base = now - datetime.timedelta(hours=24)
    else:
        # Never fired: use created_at as base
        created = cron.get("created_at", "")
        try:
            base = datetime.datetime.fromisoformat(str(created).replace("Z", "+00:00"))
        except Exception:
            base = now - datetime.timedelta(hours=24)

    try:
        it = croniter(expression, base)
        next_fire = it.get_next(datetime.datetime)
        return next_fire <= now
    except Exception:
        log.debug("croniter failed for expression=%s base=%s", expression, base, exc_info=True)
        return False


def check_and_enqueue_due_crons(
    running: dict,
    enqueue_fn: Callable,
    owner_chat_id: int,
    budget_remaining: float,
    _now: Optional[datetime.datetime] = None,
) -> int:
    """Check all enabled crons and enqueue those that are due.

    Called once per main-loop iteration. Non-blocking, fast.
    Returns count of crons fired.
    """
    crons = load_crons()
    if not crons:
        return 0

    # Budget gate
    if budget_remaining < CRON_BUDGET_RESERVE:
        return 0

    if not owner_chat_id:
        return 0

    now = _now or datetime.datetime.now(datetime.timezone.utc)
    fired = 0
    changed = False

    for cron in crons:
        if not cron.get("enabled", True):
            continue

        # Overlap prevention: skip if last task still running
        last_task_id = cron.get("last_task_id")
        if last_task_id and last_task_id in running:
            continue

        if not _is_due(cron, now):
            continue

        # Fire
        tid = uuid.uuid4().hex[:8]
        task = {
            "id": tid,
            "type": "cron",
            "chat_id": int(owner_chat_id),
            "text": cron.get("description", "cron task"),
            "cron_id": cron.get("id"),
            "silent": not cron.get("notify", True),  # if notify=false → silent mode
        }
        enqueue_fn(task)

        cron["last_fired_at"] = now.isoformat()
        cron["last_task_id"] = tid
        cron["fire_count"] = int(cron.get("fire_count") or 0) + 1
        changed = True
        fired += 1

        # Log
        try:
            append_jsonl(
                DRIVE_ROOT / "logs" / "supervisor.jsonl",
                {
                    "ts": now.isoformat(),
                    "type": "cron_fired",
                    "cron_id": cron.get("id"),
                    "task_id": tid,
                    "expression": cron.get("expression"),
                    "fire_count": cron["fire_count"],
                },
            )
        except Exception:
            log.debug("Failed to log cron fire", exc_info=True)

        # Optional notification
        if cron.get("notify") and owner_chat_id:
            try:
                send_with_budget(
                    int(owner_chat_id),
                    f"⏰ Cron [{cron.get('id')}] fired: {cron.get('description', '')[:80]}",
                )
            except Exception:
                log.debug("Failed to send cron notification", exc_info=True)

    if changed:
        save_crons(crons)

    return fired
