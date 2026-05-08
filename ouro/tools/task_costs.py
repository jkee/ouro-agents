"""Tool for querying cost-per-task metrics."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ouro.tools.registry import ToolEntry


def _get_task_costs(
    max_tasks: int = 20,
    tail_mb: float = 2.0,
    task_type: str = "",
) -> dict[str, Any]:
    """Return cost breakdown per task from recent events log.

    Args:
        max_tasks: Maximum number of tasks to return (default 20, max 100)
        tail_mb: How many MB of events.jsonl to scan from the end (default 2.0)
        task_type: Filter by task type (e.g. "task", "evolution", "cron"). Empty = all types.

    Returns:
        Dict with:
        - tasks: list of task cost records sorted by cost desc
        - total_cost: total cost across returned tasks
        - total_rounds: total LLM rounds
        - summary: human-readable summary string
    """
    from supervisor.state import per_task_cost_summary

    max_tasks = min(int(max_tasks), 100)
    tail_bytes = int(tail_mb * 1024 * 1024)

    tasks = per_task_cost_summary(max_tasks=max_tasks * 3, tail_bytes=tail_bytes)

    # Filter by task_type if requested
    if task_type:
        tasks = [t for t in tasks if t.get("task_type", "") == task_type]

    tasks = tasks[:max_tasks]

    total_cost = sum(t["cost"] for t in tasks)
    total_rounds = sum(t.get("rounds", 0) for t in tasks)

    # Build summary
    if not tasks:
        summary = "No task cost data available."
    else:
        top = tasks[0]
        top_tid = top["task_id"][:8]
        top_cost = top["cost"]
        summary = (
            f"{len(tasks)} tasks | total ${total_cost:.3f} | "
            f"most expensive: {top_tid} (${top_cost:.3f})"
        )

    return {
        "tasks": tasks,
        "total_cost": round(total_cost, 6),
        "total_rounds": total_rounds,
        "summary": summary,
    }


def get_tools() -> list:
    return [
        ToolEntry(
            name="get_task_costs",
            schema={
                "name": "get_task_costs",
                "description": (
                    "Query cost-per-task metrics from the events log. "
                    "Returns a breakdown of LLM costs per task_id, sorted by cost descending. "
                    "Useful for identifying expensive tasks and budget analysis."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "max_tasks": {
                            "type": "integer",
                            "description": "Maximum number of tasks to return (default 20, max 100)",
                            "default": 20,
                        },
                        "tail_mb": {
                            "type": "number",
                            "description": "How many MB of events log to scan from the end (default 2.0)",
                            "default": 2.0,
                        },
                        "task_type": {
                            "type": "string",
                            "description": "Filter by task type: 'task', 'evolution', 'cron', 'chat'. Empty = all.",
                            "default": "",
                        },
                    },
                    "required": [],
                },
            },
            handler=lambda ctx, **kw: _get_task_costs(**kw),
            timeout_sec=30,
        )
    ]
