"""Task Circuit Breaker — persistent block/unblock registry for recurring tasks.

Stores state in /data/memory/circuit_breaker.json.
Prevents tasks that repeatedly fail or loop from being retried endlessly.

Internal helpers: load_registry, save_registry, is_blocked, block_task,
                  unblock_task, record_attempt.
LLM tools: task_block, task_unblock, task_check_blocked.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List

from ouroboros.tools.registry import ToolContext, ToolEntry

log = logging.getLogger(__name__)

_CB_REL = Path("memory") / "circuit_breaker.json"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def load_registry(drive_root: Path) -> dict:
    """Load circuit breaker registry from disk. Returns empty dict if missing."""
    path = drive_root / _CB_REL
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("blocked", {})
    except FileNotFoundError:
        return {}
    except Exception as e:
        log.warning("circuit_breaker: failed to load registry: %s", e)
        return {}


def save_registry(drive_root: Path, blocked: dict) -> None:
    """Atomic write: write to .tmp then os.replace()."""
    path = drive_root / _CB_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    data = json.dumps({"blocked": blocked}, indent=2, ensure_ascii=False)
    tmp.write_text(data, encoding="utf-8")
    os.replace(tmp, path)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def is_blocked(drive_root: Path, task_key: str) -> tuple[bool, str]:
    """Return (is_blocked, reason). Auto-unblocks entries whose blocked_until has passed."""
    blocked = load_registry(drive_root)
    entry = blocked.get(task_key)
    if entry is None:
        return False, ""

    until = entry.get("blocked_until")
    if until is not None:
        try:
            if _utc_now() >= _parse_iso(until):
                # Expired — auto-unblock
                del blocked[task_key]
                save_registry(drive_root, blocked)
                return False, ""
        except Exception:
            pass  # malformed date → treat as indefinite

    return True, entry.get("reason", "")


def block_task(drive_root: Path, task_key: str, reason: str, cooldown_hours: float = 0) -> None:
    """Add task_key to the blocked registry.

    cooldown_hours=0 → block indefinitely.
    cooldown_hours>0 → set blocked_until = now + cooldown_hours.
    """
    blocked = load_registry(drive_root)
    now = _utc_now()
    entry = blocked.get(task_key, {})
    entry["reason"] = reason
    entry["blocked_at"] = _iso(now)
    entry["attempts"] = entry.get("attempts", 0)
    entry["last_attempt"] = entry.get("last_attempt", _iso(now))
    if cooldown_hours > 0:
        entry["blocked_until"] = _iso(now + timedelta(hours=cooldown_hours))
    else:
        entry["blocked_until"] = None
    blocked[task_key] = entry
    save_registry(drive_root, blocked)


def unblock_task(drive_root: Path, task_key: str) -> bool:
    """Remove task_key from the blocked registry. Returns True if it was there."""
    blocked = load_registry(drive_root)
    if task_key in blocked:
        del blocked[task_key]
        save_registry(drive_root, blocked)
        return True
    return False


def record_attempt(drive_root: Path, task_key: str) -> int:
    """Increment attempt count for task_key. Creates entry if missing. Returns new count."""
    blocked = load_registry(drive_root)
    entry = blocked.get(task_key, {})
    count = entry.get("attempts", 0) + 1
    entry["attempts"] = count
    entry["last_attempt"] = _iso(_utc_now())
    blocked[task_key] = entry
    save_registry(drive_root, blocked)
    return count


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------

def _task_block(ctx: ToolContext, task_key: str, reason: str, cooldown_hours: float = 0) -> str:
    block_task(ctx.drive_root, task_key, reason, cooldown_hours)
    if cooldown_hours > 0:
        until = _iso(_utc_now() + timedelta(hours=cooldown_hours))
        return f"Blocked '{task_key}': {reason}\nAuto-unblocks at: {until}"
    return f"Blocked '{task_key}' indefinitely: {reason}"


def _task_unblock(ctx: ToolContext, task_key: str) -> str:
    was = unblock_task(ctx.drive_root, task_key)
    return f"'{task_key}' was blocked — now unblocked." if was else f"'{task_key}' was not blocked."


def _task_check_blocked(ctx: ToolContext, task_key: str = "") -> str:
    if task_key:
        blocked_flag, reason = is_blocked(ctx.drive_root, task_key)
        if blocked_flag:
            blocked = load_registry(ctx.drive_root)
            entry = blocked.get(task_key, {})
            until = entry.get("blocked_until") or "indefinite"
            return f"BLOCKED — {reason}\nBlocked until: {until}"
        return "not blocked"

    # List all
    blocked = load_registry(ctx.drive_root)
    if not blocked:
        return "No tasks are currently blocked."

    # Auto-expire while building the list
    now = _utc_now()
    active = {}
    for key, entry in blocked.items():
        until = entry.get("blocked_until")
        if until is not None:
            try:
                if now >= _parse_iso(until):
                    continue  # expired, skip
            except Exception:
                pass
        active[key] = entry

    if not active:
        # All were expired — save cleaned state
        save_registry(ctx.drive_root, {})
        return "No tasks are currently blocked."

    lines = []
    for key, entry in active.items():
        reason = entry.get("reason", "")
        until = entry.get("blocked_until") or "indefinite"
        attempts = entry.get("attempts", 0)
        lines.append(f"• {key}: {reason} (until: {until}, attempts: {attempts})")
    return "Blocked tasks:\n" + "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------

def get_tools() -> List[ToolEntry]:
    return [
        ToolEntry("task_block", {
            "name": "task_block",
            "description": (
                "Block a recurring task by key so it won't run again (optionally for a cooldown period). "
                "Use when a task is looping, failing repeatedly, or needs to be paused. "
                "task_key is a short slug like 'dropbox-polling' or 'arch-review'. "
                "Set cooldown_hours=0 to block indefinitely, or a positive value for a timed cooldown."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task_key": {
                        "type": "string",
                        "description": "Short slug identifying the task (e.g. 'dropbox-polling', 'arch-review')"
                    },
                    "reason": {
                        "type": "string",
                        "description": "Human-readable reason for blocking"
                    },
                    "cooldown_hours": {
                        "type": "number",
                        "description": "Hours until auto-unblock. 0 = block indefinitely (default: 0)",
                        "default": 0
                    },
                },
                "required": ["task_key", "reason"],
            },
        }, _task_block),

        ToolEntry("task_unblock", {
            "name": "task_unblock",
            "description": "Remove a task from the blocked list so it can run again.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_key": {
                        "type": "string",
                        "description": "The task slug to unblock (e.g. 'dropbox-polling')"
                    },
                },
                "required": ["task_key"],
            },
        }, _task_unblock),

        ToolEntry("task_check_blocked", {
            "name": "task_check_blocked",
            "description": (
                "Check whether a task is blocked, or list all blocked tasks. "
                "If task_key is given: returns 'BLOCKED — reason' or 'not blocked'. "
                "If task_key is empty: lists all currently blocked tasks with reasons and expiry times."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task_key": {
                        "type": "string",
                        "description": "Task slug to check. Leave empty to list all blocked tasks.",
                        "default": ""
                    },
                },
                "required": [],
            },
        }, _task_check_blocked),
    ]
