"""Agent tools for managing scheduled (cron) tasks."""
from __future__ import annotations

import logging
from typing import List

from ouro.tools.registry import ToolContext, ToolEntry

log = logging.getLogger(__name__)


def _get_scheduler(ctx: ToolContext):
    from supervisor.cron import CronScheduler
    return CronScheduler(ctx.drive_root)


def _schedule_cron(ctx: ToolContext, description: str, schedule: str) -> str:
    """
    Add a new recurring scheduled task.

    description: what the agent should do each time (task text)
    schedule: human-friendly expression, e.g. "every monday at 09:00",
              "daily at 08:00", "every 2 hours", "every 30 minutes"
    """
    chat_id = ctx.current_chat_id
    if chat_id is None:
        try:
            from supervisor.state import load_state
            st = load_state()
            chat_id = int(st.get("owner_chat_id") or 0) or None
        except Exception:
            pass
    if chat_id is None:
        return "ERROR: cannot determine chat_id — schedule_cron requires an active chat context."

    try:
        scheduler = _get_scheduler(ctx)
        job = scheduler.add_job(description=description, schedule_expr=schedule, chat_id=chat_id)
    except ValueError as e:
        return f"ERROR: {e}"

    return (
        f"Scheduled recurring task.\n"
        f"  ID: {job['id']}\n"
        f"  Schedule: {job['human_desc']}\n"
        f"  Next run: {job['next_run_utc']}\n"
        f"  Task: {job['description']}"
    )


def _list_cron(ctx: ToolContext) -> str:
    """List all scheduled recurring tasks."""
    scheduler = _get_scheduler(ctx)
    jobs = scheduler.list_jobs()
    if not jobs:
        return "No recurring tasks scheduled."
    lines = [f"Recurring tasks ({len(jobs)}):"]
    for j in jobs:
        last = j.get("last_run_utc") or "never"
        lines.append(
            f"  [{j['id'][:8]}] {j['human_desc']}\n"
            f"    Task: {j['description']}\n"
            f"    Next: {j['next_run_utc']}  Last: {last}"
        )
    return "\n".join(lines)


def _delete_cron(ctx: ToolContext, job_id: str) -> str:
    """Delete a scheduled recurring task by ID (full or prefix match)."""
    scheduler = _get_scheduler(ctx)
    # Support prefix matching so user can pass first 8 chars shown in list
    jobs = scheduler.list_jobs()
    matched = [j for j in jobs if j["id"] == job_id or j["id"].startswith(job_id)]
    if not matched:
        return f"No cron job found with id (or prefix): {job_id!r}"
    if len(matched) > 1:
        ids = ", ".join(j["id"][:8] for j in matched)
        return f"Ambiguous prefix {job_id!r} matches multiple jobs: {ids}. Use more characters."
    full_id = matched[0]["id"]
    scheduler.delete_job(full_id)
    return f"Deleted cron job {full_id[:8]} ({matched[0]['human_desc']}): {matched[0]['description']}"


def get_tools() -> List[ToolEntry]:
    return [
        ToolEntry(
            name="schedule_cron",
            schema={
                "name": "schedule_cron",
                "description": (
                    "Schedule a recurring task that runs automatically on a schedule. "
                    "The task text will be enqueued as a new agent task each time it fires."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "description": {
                            "type": "string",
                            "description": "What the agent should do each time (task text sent to the queue).",
                        },
                        "schedule": {
                            "type": "string",
                            "description": (
                                "Human-friendly schedule. Examples: "
                                "'every monday at 09:00', 'daily at 08:00', "
                                "'every 2 hours', 'every 30 minutes', 'every 3 days at 10:00'."
                            ),
                        },
                    },
                    "required": ["description", "schedule"],
                },
            },
            handler=_schedule_cron,
        ),
        ToolEntry(
            name="list_cron",
            schema={
                "name": "list_cron",
                "description": "List all scheduled recurring (cron) tasks.",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
            handler=_list_cron,
        ),
        ToolEntry(
            name="delete_cron",
            schema={
                "name": "delete_cron",
                "description": "Delete a scheduled recurring task by its ID (or ID prefix).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "job_id": {
                            "type": "string",
                            "description": "Full job ID or the first 8 characters shown in list_cron.",
                        },
                    },
                    "required": ["job_id"],
                },
            },
            handler=_delete_cron,
        ),
    ]
