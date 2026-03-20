"""
Supervisor — Slash command handling.

Processes owner commands like /panic, /restart, /status, /evolve, etc.
Extracted from launcher.py.
"""

from __future__ import annotations

import logging
import sys
import uuid
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from supervisor.config import Config

log = logging.getLogger(__name__)


def handle_supervisor_command(
    text: str,
    chat_id: int,
    *,
    cfg: "Config",
    tg_offset: int = 0,
    send_with_budget: Any,
    load_state: Any,
    save_state: Any,
    safe_restart: Any,
    kill_workers: Any,
    get_chat_agent: Any,
    reset_chat_agent: Any,
    consciousness: Any,
    pending: list,
    queue_lock: Any,
    sort_pending: Any,
    persist_queue_snapshot: Any,
    queue_review_task: Any,
    status_text_fn: Any,
    workers: dict,
    running: dict,
) -> Any:
    """Handle supervisor slash-commands.

    Returns:
        True  -- terminal command fully handled (caller should `continue`)
        str   -- dual-path note to prepend (caller falls through to LLM)
        ""    -- not a recognized command (falsy, caller falls through)
    """
    lowered = text.strip().lower()

    if lowered.startswith("/panic"):
        send_with_budget(chat_id, "\U0001f6d1 PANIC: stopping everything now.")
        kill_workers()
        st2 = load_state()
        st2["tg_offset"] = tg_offset
        save_state(st2)
        sys.exit(0)

    if lowered.startswith("/restart"):
        st2 = load_state()
        st2["session_id"] = uuid.uuid4().hex
        st2["tg_offset"] = tg_offset
        save_state(st2)
        send_with_budget(chat_id, "\u267b\ufe0f Restarting (soft).")
        ok, msg = safe_restart(reason="owner_restart", unsynced_policy="rescue_and_reset")
        if not ok:
            send_with_budget(chat_id, f"\u26a0\ufe0f Restart cancelled: {msg}")
            return True
        kill_workers()
        sys.exit(1)

    # Dual-path commands: supervisor handles + LLM sees a note
    if lowered.startswith("/status"):
        status = status_text_fn(workers, pending, running, cfg.soft_timeout_sec, cfg.hard_timeout_sec)
        send_with_budget(chat_id, status, force_budget=True)
        return "[Supervisor handled /status \u2014 status text already sent to chat]\n"

    if lowered.startswith("/review"):
        queue_review_task(reason="owner:/review", force=True)
        return "[Supervisor handled /review \u2014 review task queued]\n"

    if lowered.startswith("/evolve"):
        parts = lowered.split()
        action = parts[1] if len(parts) > 1 else "on"
        turn_on = action not in ("off", "stop", "0")
        st2 = load_state()
        st2["evolution_mode_enabled"] = bool(turn_on)
        save_state(st2)
        if not turn_on:
            with queue_lock:
                pending[:] = [t for t in pending if str(t.get("type")) != "evolution"]
                sort_pending()
            persist_queue_snapshot(reason="evolve_off")
        state_str = "ON" if turn_on else "OFF"
        send_with_budget(chat_id, f"\U0001f9ec Evolution: {state_str}")
        return f"[Supervisor handled /evolve \u2014 evolution toggled {state_str}]\n"

    if lowered.startswith("/bg"):
        parts = lowered.split()
        action = parts[1] if len(parts) > 1 else "status"
        if action in ("start", "on", "1"):
            result = consciousness.start()
            send_with_budget(chat_id, f"\U0001f9e0 {result}")
        elif action in ("stop", "off", "0"):
            result = consciousness.stop()
            send_with_budget(chat_id, f"\U0001f9e0 {result}")
        else:
            bg_status = "running" if consciousness.is_running else "stopped"
            send_with_budget(chat_id, f"\U0001f9e0 Background consciousness: {bg_status}")
        return f"[Supervisor handled /bg {action}]\n"

    if lowered.startswith("/break"):
        agent = get_chat_agent()
        if agent._busy:
            agent.request_break()
            send_with_budget(chat_id, "\u23f9 Break: sent stop signal to current task.")
        else:
            send_with_budget(chat_id, "\u2705 No task is running.")
        return True

    if lowered.startswith("/budget"):
        from supervisor.state import check_openrouter_ground_truth, budget_breakdown
        ground_truth = check_openrouter_ground_truth()
        st2 = load_state()
        if ground_truth is not None:
            st2["openrouter_total_usd"] = ground_truth["total_usd"]
            st2["openrouter_daily_usd"] = ground_truth["daily_usd"]
            if "limit" in ground_truth:
                st2["openrouter_limit"] = ground_truth["limit"]
            if "limit_remaining" in ground_truth:
                st2["openrouter_limit_remaining"] = ground_truth["limit_remaining"]
            save_state(st2)
        or_remaining = st2.get("openrouter_limit_remaining")
        or_limit = st2.get("openrouter_limit")
        spent_tracked = float(st2.get("spent_usd") or 0.0)
        lines = ["\U0001f4b0 Budget:"]
        if or_remaining is not None:
            lines.append(f"  OpenRouter remaining: ${float(or_remaining):.2f}")
        if or_limit is not None:
            lines.append(f"  OpenRouter limit: ${float(or_limit):.2f}")
        lines.append(f"  Session spent (tracked): ${spent_tracked:.2f}")
        breakdown = budget_breakdown(st2)
        if breakdown:
            sorted_cats = sorted(breakdown.items(), key=lambda x: x[1], reverse=True)
            breakdown_parts = [f"{cat}=${cost:.2f}" for cat, cost in sorted_cats if cost > 0]
            if breakdown_parts:
                lines.append(f"  Breakdown: {', '.join(breakdown_parts)}")
        send_with_budget(chat_id, "\n".join(lines))
        return True

    if lowered.startswith("/rollback"):
        send_with_budget(chat_id, "\u267b\ufe0f Rolling back to latest stable...")
        st2 = load_state()
        st2["no_approve_mode"] = False
        st2["tg_offset"] = tg_offset
        save_state(st2)
        ok, msg = safe_restart(reason="owner_rollback", unsynced_policy="rescue_and_reset")
        if not ok:
            send_with_budget(chat_id, f"\u26a0\ufe0f Rollback failed: {msg}")
            return True
        kill_workers()
        sys.exit(1)

    if lowered.startswith("/no-approve") or lowered.startswith("/noapprove"):
        st2 = load_state()
        current = bool(st2.get("no_approve_mode"))
        st2["no_approve_mode"] = not current
        save_state(st2)
        state_str = "ON" if st2["no_approve_mode"] else "OFF"
        send_with_budget(chat_id, f"\U0001f527 No-approve mode: {state_str}")
        return True

    return ""
