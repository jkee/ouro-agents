"""Data volume cleanup tool — weekly maintenance of /data/."""

from __future__ import annotations

import logging
import os
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import List

from ouro.tools.registry import ToolContext, ToolEntry

log = logging.getLogger(__name__)

DATA_DIR = Path("/data")

# Patterns for temp files to delete from /data/ root
TEMP_PATTERNS = [
    "tmp_*.py",
    "cam*_*.jpg",
    "cam*_*.png",
    "cam*_*.txt",
    "*_debug.*",
    "*_b64.txt",
]

# Patterns for one-off scripts to archive (move to /data/archive/)
ARCHIVE_PATTERNS = [
    "create_*.py",
    "parse_*.py",
    "test_*.py",
]

# Directories that must never be touched
PROTECTED_DIRS = {"memory", "state", "locks", "index"}

# Files that must never be deleted
PROTECTED_FILES = {
    "crons.json",
    "flights.json",
    "flights_pending_notify.txt",
    "country_days.json",
    "robotics_sent.json",
    "chat.jsonl",
}

# .md files in /data/ root that are safe to keep (never delete these)
SAFE_MD_FILES = {"flights.md", "photo_models.md"}


def _match_any(name: str, patterns: List[str]) -> bool:
    from fnmatch import fnmatch
    return any(fnmatch(name, p) for p in patterns)


def _is_protected(path: Path) -> bool:
    """Return True if path must not be deleted."""
    if path.is_dir():
        return True
    if path.name in PROTECTED_FILES:
        return True
    # Protect anything under protected dirs
    for part in path.parts:
        if part in PROTECTED_DIRS:
            return True
    return False


def _run_data_cleanup(ctx: ToolContext, dry_run: bool = False, **kwargs) -> str:
    deleted: list[str] = []
    moved: list[str] = []
    skipped: list[str] = []
    now = datetime.now()

    def do_delete(p: Path, reason: str) -> None:
        if _is_protected(p):
            skipped.append(str(p))
            return
        log.info("cleanup: delete %s (%s)", p, reason)
        if not dry_run:
            p.unlink(missing_ok=True)
        deleted.append(str(p))

    def do_move(p: Path, dest: Path) -> None:
        if _is_protected(p):
            skipped.append(str(p))
            return
        log.info("cleanup: archive %s -> %s", p, dest)
        if not dry_run:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(p), str(dest))
        moved.append(f"{p} -> {dest}")

    # --- 1. Temp files in /data/ root ---
    for p in DATA_DIR.iterdir():
        if p.is_dir():
            continue
        if _match_any(p.name, TEMP_PATTERNS):
            do_delete(p, "temp file")

    # --- 2. One-off scripts in /data/ root -> /data/archive/ ---
    archive_dir = DATA_DIR / "archive"
    for p in DATA_DIR.iterdir():
        if p.is_dir():
            continue
        if _match_any(p.name, ARCHIVE_PATTERNS):
            dest = archive_dir / p.name
            do_move(p, dest)

    # --- 3. Task results older than 14 days, keep at least 50 most recent ---
    task_results_dir = DATA_DIR / "task_results"
    if task_results_dir.exists():
        files = sorted(
            [f for f in task_results_dir.iterdir() if f.is_file()],
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )
        cutoff_14d = now - timedelta(days=14)
        for i, f in enumerate(files):
            if i < 50:
                continue  # always keep 50 most recent
            mtime = datetime.fromtimestamp(f.stat().st_mtime)
            if mtime < cutoff_14d:
                do_delete(f, "task_result >14d")

    # --- 4. Log task files older than 30 days ---
    logs_tasks_dir = DATA_DIR / "logs" / "tasks"
    if logs_tasks_dir.exists():
        cutoff_30d = now - timedelta(days=30)
        for f in logs_tasks_dir.iterdir():
            if f.is_dir():
                continue
            mtime = datetime.fromtimestamp(f.stat().st_mtime)
            if mtime < cutoff_30d:
                do_delete(f, "log/tasks >30d")

    # --- 5. Stale root files older than 60 days matching safe patterns ---
    cutoff_60d = now - timedelta(days=60)
    for p in DATA_DIR.iterdir():
        if p.is_dir():
            continue
        if p.name in PROTECTED_FILES:
            continue
        mtime = datetime.fromtimestamp(p.stat().st_mtime)
        if mtime >= cutoff_60d:
            continue
        name_lower = p.name.lower()
        if p.suffix == ".html" and p.name.lower() != "index.html":
            do_delete(p, "stale .html >60d")
        elif p.suffix == ".md" and p.name not in SAFE_MD_FILES:
            do_delete(p, "stale .md >60d")

    # --- Summary ---
    mode = "[DRY RUN] " if dry_run else ""
    lines = [f"{mode}Data cleanup complete."]
    lines.append(f"  Deleted:  {len(deleted)} file(s)")
    lines.append(f"  Archived: {len(moved)} file(s)")
    if skipped:
        lines.append(f"  Skipped (protected): {len(skipped)}")
    if deleted:
        lines.append("\nDeleted:")
        lines.extend(f"  - {p}" for p in deleted)
    if moved:
        lines.append("\nArchived:")
        lines.extend(f"  - {m}" for m in moved)
    return "\n".join(lines)


def get_tools() -> List[ToolEntry]:
    return [
        ToolEntry(
            "run_data_cleanup",
            {
                "name": "run_data_cleanup",
                "description": (
                    "Clean up stale files in /data/ — temp files, old task results, "
                    "one-off scripts. Run weekly for maintenance."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "dry_run": {
                            "type": "boolean",
                            "description": "If true, report what would be deleted without deleting. Default: false.",
                        }
                    },
                    "required": [],
                },
            },
            _run_data_cleanup,
            timeout_sec=60,
        )
    ]
