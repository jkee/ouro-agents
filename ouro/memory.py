"""
Ouro — Memory.

Scratchpad, identity, chat history.
Contract: load scratchpad/identity, chat_history().
"""

from __future__ import annotations

import json
import logging
import pathlib
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from ouro.utils import utc_now_iso, read_text, write_text, append_jsonl, short

log = logging.getLogger(__name__)


class Memory:
    """Ouro memory management: scratchpad, identity, chat history, logs."""

    def __init__(self, drive_root: pathlib.Path, repo_dir: Optional[pathlib.Path] = None):
        self.drive_root = drive_root
        self.repo_dir = repo_dir

    # --- Paths ---

    def _memory_path(self, rel: str) -> pathlib.Path:
        return (self.drive_root / "memory" / rel).resolve()

    def scratchpad_path(self) -> pathlib.Path:
        return self._memory_path("scratchpad.md")

    def identity_path(self) -> pathlib.Path:
        return self._memory_path("identity.md")

    def user_context_path(self) -> pathlib.Path:
        return self._memory_path("USER_CONTEXT.md")

    def journal_path(self) -> pathlib.Path:
        return self._memory_path("scratchpad_journal.jsonl")

    def logs_path(self, name: str) -> pathlib.Path:
        return (self.drive_root / "logs" / name).resolve()

    # --- Load / save ---

    def _read_with_retry(self, p: pathlib.Path, max_attempts: int = 3, delay: float = 1.0) -> str:
        """Read a file with retry logic to handle transient drive access issues."""
        last_exc: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            try:
                return read_text(p)
            except Exception as exc:
                last_exc = exc
                log.warning("Attempt %d/%d failed reading %s: %s", attempt, max_attempts, p, exc)
                if attempt < max_attempts:
                    time.sleep(delay)
        raise last_exc  # type: ignore[misc]

    def _safe_load_or_create(self, p: pathlib.Path, default_fn, max_attempts: int = 3, delay: float = 0.5) -> str:
        """
        Robustly load a file without relying on p.exists().

        Strategy:
        - Attempt to read the file directly (catches OSError including ENOENT).
        - On FileNotFoundError: retry up to max_attempts (handles transient mount issues).
        - On other OSError: retry as well (transient I/O problem).
        - Only creates a default if ALL attempts fail with a file-not-found-like error.
        - Never calls write_text after a bare p.exists() check — that is the bug this fixes.
        """
        last_exc: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            try:
                return read_text(p)
            except FileNotFoundError as exc:
                last_exc = exc
                if attempt < max_attempts:
                    log.debug("File not found attempt %d/%d: %s — will retry", attempt, max_attempts, p)
                    time.sleep(delay)
            except OSError as exc:
                last_exc = exc
                log.warning("OSError attempt %d/%d reading %s: %s", attempt, max_attempts, p, exc)
                if attempt < max_attempts:
                    time.sleep(delay)
        # File genuinely absent after all retries — safe to create default
        log.info("Creating default for missing file: %s", p)
        default = default_fn()
        write_text(p, default)
        return default

    def load_scratchpad(self) -> str:
        return self._safe_load_or_create(self.scratchpad_path(), self._default_scratchpad)

    def save_scratchpad(self, content: str) -> None:
        write_text(self.scratchpad_path(), content)

    def load_identity(self) -> str:
        return self._safe_load_or_create(self.identity_path(), self._default_identity)

    def load_user_context(self) -> str:
        return self._safe_load_or_create(self.user_context_path(), self._default_user_context)

    def save_user_context(self, content: str) -> None:
        write_text(self.user_context_path(), content)

    def ensure_files(self) -> None:
        """Create memory files if they don't exist (idempotent, safe against transient mount issues)."""
        self.load_scratchpad()
        self.load_identity()
        self.load_user_context()
        # Journal is append-only — create only if truly missing
        journal = self.journal_path()
        for attempt in range(3):
            try:
                read_text(journal)
                break
            except FileNotFoundError:
                if attempt == 2:
                    write_text(journal, "")
                else:
                    time.sleep(0.5)
            except OSError:
                if attempt == 2:
                    write_text(journal, "")
                else:
                    time.sleep(0.5)

    # --- Chat history ---

    def chat_history(self, count: int = 100, offset: int = 0, search: str = "") -> str:
        """Read from logs/chat.jsonl. count messages, offset from end, filter by search."""
        chat_path = self.logs_path("chat.jsonl")
        try:
            raw_content = chat_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return "(chat history is empty)"
        except OSError:
            return "(chat history is empty)"

        try:
            raw_lines = raw_content.strip().split("\n")
            entries = []
            for line in raw_lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except Exception:
                    log.debug(f"Failed to parse JSON line in chat_history: {line[:100]}")
                    continue

            if search:
                search_lower = search.lower()
                entries = [e for e in entries if search_lower in str(e.get("text", "")).lower()]

            if offset > 0:
                entries = entries[:-offset] if offset < len(entries) else []

            entries = entries[-count:] if count < len(entries) else entries

            if not entries:
                return "(no messages matching query)"

            lines = []
            for e in entries:
                dir_raw = str(e.get("direction", "")).lower()
                direction = "→" if dir_raw in ("out", "outgoing") else "←"
                ts = str(e.get("ts", ""))[:16]
                raw_text = str(e.get("text", ""))
                if dir_raw in ("out", "outgoing"):
                    text = short(raw_text, 800)
                else:
                    text = raw_text  # never truncate owner's messages
                lines.append(f"{direction} [{ts}] {text}")

            return f"Showing {len(entries)} messages:\n\n" + "\n".join(lines)
        except Exception as e:
            return f"(error reading history: {e})"

    # --- JSONL tail reading ---

    def read_jsonl_tail(self, log_name: str, max_entries: int = 100, max_age_hours: float = 0) -> List[Dict[str, Any]]:
        """Read the last max_entries records from a JSONL file."""
        path = self.logs_path(log_name)
        try:
            raw_content = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        except OSError:
            return []
        try:
            lines = raw_content.strip().split("\n")
            tail = lines[-max_entries:] if max_entries < len(lines) else lines
            entries = []
            for line in tail:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except Exception:
                    log.debug(f"Failed to parse JSON line in read_jsonl_tail: {line[:100]}", exc_info=True)
                    continue
            if max_age_hours > 0:
                cutoff = (datetime.now(timezone.utc) - timedelta(hours=max_age_hours)).isoformat()
                entries = [e for e in entries if not e.get("ts") or e["ts"] >= cutoff]
            return entries
        except Exception:
            log.warning(f"Failed to read JSONL tail from {log_name}", exc_info=True)
            return []

    # --- Log summarization ---

    def summarize_chat(self, entries: List[Dict[str, Any]]) -> str:
        if not entries:
            return ""
        lines = []
        for e in entries[-100:]:
            dir_raw = str(e.get("direction", "")).lower()
            direction = "→" if dir_raw in ("out", "outgoing") else "←"
            ts_full = e.get("ts", "")
            ts_hhmm = ts_full[11:16] if len(ts_full) >= 16 else ""
            # Creator messages: no truncation (most valuable context)
            # Outgoing messages: truncate to 800 chars
            raw_text = str(e.get("text", ""))
            if dir_raw in ("out", "outgoing"):
                text = short(raw_text, 800)
            else:
                text = raw_text  # never truncate owner's messages
            lines.append(f"{direction} {ts_hhmm} {text}")
        return "\n".join(lines)

    def summarize_progress(self, entries: List[Dict[str, Any]], limit: int = 15) -> str:
        """Summarize progress.jsonl entries (Ouro's self-talk / progress messages)."""
        if not entries:
            return ""
        lines = []
        for e in entries[-limit:]:
            ts_full = e.get("ts", "")
            ts_hhmm = ts_full[11:16] if len(ts_full) >= 16 else ""
            text = short(str(e.get("text", "")), 300)
            lines.append(f"⚙️ {ts_hhmm} {text}")
        return "\n".join(lines)

    def summarize_tools(self, entries: List[Dict[str, Any]]) -> str:
        if not entries:
            return ""
        lines = []
        for e in entries[-10:]:
            tool = e.get("tool") or e.get("tool_name") or "?"
            args = e.get("args", {})
            hints = []
            for key in ("path", "dir", "commit_message", "query"):
                if key in args:
                    hints.append(f"{key}={short(str(args[key]), 60)}")
            if "cmd" in args:
                hints.append(f"cmd={short(str(args['cmd']), 80)}")
            hint_str = ", ".join(hints) if hints else ""
            status = "✓" if ("result_preview" in e and not str(e.get("result_preview", "")).lstrip().startswith("⚠️")) else "·"
            lines.append(f"{status} {tool} {hint_str}".strip())
        return "\n".join(lines)

    def summarize_events(self, entries: List[Dict[str, Any]]) -> str:
        if not entries:
            return ""
        type_counts: Counter = Counter()
        for e in entries:
            type_counts[e.get("type", "unknown")] += 1
        top_types = type_counts.most_common(10)
        lines = ["Event counts:"]
        for evt_type, count in top_types:
            lines.append(f"  {evt_type}: {count}")
        error_types = {"tool_error", "telegram_api_error", "task_error", "tool_rounds_exceeded"}
        errors = [e for e in entries if e.get("type") in error_types]
        if errors:
            lines.append("\nRecent errors:")
            for e in errors[-10:]:
                lines.append(f"  {e.get('type', '?')}: {short(str(e.get('error', '')), 120)}")
        return "\n".join(lines)

    def summarize_supervisor(self, entries: List[Dict[str, Any]]) -> str:
        if not entries:
            return ""
        for e in reversed(entries):
            if e.get("type") in ("launcher_start", "restart", "boot"):
                branch = e.get("branch") or e.get("git_branch") or "?"
                sha = short(str(e.get("sha") or e.get("git_sha") or ""), 12)
                return f"{e['type']}: {e.get('ts', '')} branch={branch} sha={sha}"
        return ""

    def append_journal(self, entry: Dict[str, Any]) -> None:
        append_jsonl(self.journal_path(), entry)

    # --- Defaults ---

    def _default_scratchpad(self) -> str:
        return f"# Scratchpad\n\nUpdatedAt: {utc_now_iso()}\n\n(empty — write anything here)\n"

    def _default_identity(self) -> str:
        return (
            "# Who I Am\n\n"
            "I am Ouro. This file is my persistent self-identification.\n"
            "I can write anything here: how I see myself, how I want to communicate,\n"
            "what matters to me, what I have understood about myself.\n\n"
            "This file is read at every dialogue and influences my responses.\n"
            "I update it when I feel the need, via update_identity.\n"
        )

    def _default_user_context(self) -> str:
        return (
            "# User Context\n\n"
            "Key information about the user. Keep under 1000 characters.\n\n"
            "- **Who:** (not yet known)\n"
            "- **Main goals:** (not yet known)\n"
            "- **Current priorities:** (not yet known)\n"
        )
