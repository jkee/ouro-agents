"""Control tools: restart, promote, schedule, cancel, review, chat_history, update_scratchpad, switch_model."""

from __future__ import annotations

import json
import logging
import os
import uuid
from pathlib import Path
from typing import Any, Dict, List

from ouro.tools.registry import ToolContext, ToolEntry
from ouro.utils import utc_now_iso, write_text, run_cmd

log = logging.getLogger(__name__)

MAX_SUBTASK_DEPTH = 3


def _request_restart(ctx: ToolContext, reason: str) -> str:
    if str(ctx.current_task_type or "") == "evolution" and not ctx.last_push_succeeded:
        return "⚠️ RESTART_BLOCKED: in evolution mode, commit+push first."
    # Persist expected SHA for post-restart verification
    try:
        sha = run_cmd(["git", "rev-parse", "HEAD"], cwd=ctx.repo_dir)
        branch = run_cmd(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=ctx.repo_dir)
        verify_path = ctx.drive_path("state") / "pending_restart_verify.json"
        write_text(verify_path, json.dumps({
            "ts": utc_now_iso(), "expected_sha": sha,
            "expected_branch": branch, "reason": reason,
        }, ensure_ascii=False, indent=2))
    except Exception:
        log.debug("Failed to read VERSION file or git ref for restart verification", exc_info=True)
        pass
    ctx.pending_events.append({"type": "restart_request", "reason": reason, "ts": utc_now_iso()})
    ctx.last_push_succeeded = False
    return f"Restart requested: {reason}"


def _promote_to_stable(ctx: ToolContext, reason: str) -> str:
    ctx.pending_events.append({"type": "promote_to_stable", "reason": reason, "ts": utc_now_iso()})
    return f"Promote to stable requested: {reason}"


def _schedule_task(ctx: ToolContext, description: str, context: str = "", parent_task_id: str = "") -> str:
    current_depth = getattr(ctx, 'task_depth', 0)
    new_depth = current_depth + 1 if parent_task_id else 0
    if new_depth > MAX_SUBTASK_DEPTH:
        return f"ERROR: Subtask depth limit ({MAX_SUBTASK_DEPTH}) exceeded. Simplify your approach."

    if getattr(ctx, 'is_direct_chat', False):
        from ouro.utils import append_jsonl
        try:
            append_jsonl(ctx.drive_logs() / "events.jsonl", {
                "ts": utc_now_iso(),
                "type": "schedule_task_from_direct_chat",
                "description": description[:200],
                "warning": "schedule_task called from direct chat context — potential duplicate work",
            })
        except Exception:
            pass

    if getattr(ctx, 'is_consciousness', False):
        from ouro.utils import append_jsonl
        try:
            append_jsonl(ctx.drive_logs() / "events.jsonl", {
                "ts": utc_now_iso(),
                "type": "consciousness_schedule_task",
                "description": description[:200],
            })
        except Exception:
            pass

    tid = uuid.uuid4().hex[:8]
    evt = {"type": "schedule_task", "description": description, "task_id": tid, "depth": new_depth, "ts": utc_now_iso()}
    if context:
        evt["context"] = context
    if parent_task_id:
        evt["parent_task_id"] = parent_task_id
    ctx.pending_events.append(evt)
    return f"Scheduled task {tid}: {description}"


def _cancel_task(ctx: ToolContext, task_id: str) -> str:
    ctx.pending_events.append({"type": "cancel_task", "task_id": task_id, "ts": utc_now_iso()})
    return f"Cancel requested: {task_id}"


def _request_review(ctx: ToolContext, reason: str) -> str:
    ctx.pending_events.append({"type": "review_request", "reason": reason, "ts": utc_now_iso()})
    return f"Review requested: {reason}"


def _chat_history(ctx: ToolContext, count: int = 100, offset: int = 0, search: str = "") -> str:
    from ouro.memory import Memory
    mem = Memory(drive_root=ctx.drive_root)
    return mem.chat_history(count=count, offset=offset, search=search)


def _update_scratchpad(ctx: ToolContext, content: str) -> str:
    """LLM-driven scratchpad update."""
    from ouro.memory import Memory
    mem = Memory(drive_root=ctx.drive_root)
    mem.ensure_files()
    mem.save_scratchpad(content)
    mem.append_journal({
        "ts": utc_now_iso(),
        "content_preview": content[:500],
        "content_len": len(content),
    })
    return f"OK: scratchpad updated ({len(content)} chars)"


def _send_owner_message(ctx: ToolContext, text: str, reason: str = "") -> str:
    """Send a proactive message to the owner (not as reply to a task).

    Use when you have something genuinely worth saying — an insight,
    a question, a status update, or an invitation to collaborate.
    """
    if not ctx.current_chat_id:
        return "⚠️ No active chat — cannot send proactive message."
    if not text or not text.strip():
        return "⚠️ Empty message."

    from ouro.utils import append_jsonl
    ctx.pending_events.append({
        "type": "send_message",
        "chat_id": ctx.current_chat_id,
        "text": text,
        "format": "markdown",
        "is_progress": False,
        "ts": utc_now_iso(),
    })
    append_jsonl(ctx.drive_logs() / "events.jsonl", {
        "ts": utc_now_iso(),
        "type": "proactive_message",
        "reason": reason,
        "text_preview": text[:200],
    })
    return "OK: message queued for delivery."


def _update_identity(ctx: ToolContext, content: str) -> str:
    """Update identity manifest (who you are, who you want to become)."""
    path = ctx.drive_root / "memory" / "identity.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return f"OK: identity updated ({len(content)} chars)"


def _update_user_context(ctx: ToolContext, content: str) -> str:
    """Update user context (who the user is, their goals, priorities). Keep under 1000 chars."""
    from ouro.memory import Memory
    mem = Memory(drive_root=ctx.drive_root)
    mem.save_user_context(content)
    warning = ""
    if len(content) > 1000:
        warning = f" WARNING: content is {len(content)} chars, Bible section 5 says keep under 1000."
    return f"OK: user context updated ({len(content)} chars){warning}"


def _toggle_evolution(ctx: ToolContext, enabled: bool) -> str:
    """Toggle evolution mode on/off via supervisor event."""
    ctx.pending_events.append({
        "type": "toggle_evolution",
        "enabled": bool(enabled),
        "ts": utc_now_iso(),
    })
    state_str = "ON" if enabled else "OFF"
    return f"OK: evolution mode toggled {state_str}."


def _toggle_consciousness(ctx: ToolContext, action: str = "status") -> str:
    """Control background consciousness: start, stop, or status."""
    ctx.pending_events.append({
        "type": "toggle_consciousness",
        "action": action,
        "ts": utc_now_iso(),
    })
    return f"OK: consciousness '{action}' requested."


def _switch_model(ctx: ToolContext, model: str = "", effort: str = "") -> str:
    """LLM-driven model/effort switch.

    Stored in ToolContext, applied on the next LLM call in the loop.
    Accepts any valid OpenRouter model ID (format: provider/model-name).
    """
    from ouro.llm import LLMClient, normalize_reasoning_effort
    configured = LLMClient().available_models()
    changes = []

    if model:
        if "/" not in model:
            return (
                f"⚠️ Invalid model ID: '{model}'. "
                f"Model ID must be in format provider/model-name (e.g. openai/gpt-4o, google/gemini-2.5-pro-preview). "
                f"Configured suggestions: {', '.join(configured)}"
            )
        ctx.active_model_override = model
        changes.append(f"model={model}")

    if effort:
        normalized = normalize_reasoning_effort(effort, default="medium")
        ctx.active_effort_override = normalized
        changes.append(f"effort={normalized}")

    if not changes:
        return (
            f"Configured model suggestions: {', '.join(configured)}. "
            f"Pass model (any OpenRouter ID with '/') and/or effort to switch."
        )

    return f"OK: switching to {', '.join(changes)} on next round."


async def _query_model_compare(client, model: str, messages: list, api_key: str, semaphore) -> dict:
    """Query a single model for compare_models. Returns {model, response, cost}."""
    import asyncio
    import httpx

    OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

    async with semaphore:
        try:
            resp = await client.post(
                OPENROUTER_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={"model": model, "messages": messages},
                timeout=30.0,
            )
            status_code = resp.status_code
            if status_code != 200:
                return {"model": model, "response": f"HTTP {status_code}: {resp.text[:200]}", "cost": 0.0}

            data = resp.json()
            choices = data.get("choices", [])
            text = choices[0]["message"]["content"] if choices else "(no response)"
            usage = data.get("usage", {})
            cost = float(usage.get("cost") or 0.0)
            return {"model": model, "response": text, "cost": cost}
        except asyncio.TimeoutError:
            return {"model": model, "response": "Error: timeout after 30s", "cost": 0.0}
        except Exception as e:
            return {"model": model, "response": f"Error: {str(e)[:200]}", "cost": 0.0}


async def _compare_models_async(prompt: str, models: list, ctx: ToolContext) -> dict:
    """Async orchestration for compare_models."""
    import asyncio
    import httpx

    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        return {"error": "OPENROUTER_API_KEY not set"}

    messages = [{"role": "user", "content": prompt}]
    semaphore = asyncio.Semaphore(5)

    async with httpx.AsyncClient() as client:
        tasks = [_query_model_compare(client, m, messages, api_key, semaphore) for m in models]
        results = await asyncio.gather(*tasks)

    # Emit usage events for budget tracking
    for result in results:
        usage_event = {
            "type": "llm_usage",
            "ts": utc_now_iso(),
            "task_id": ctx.task_id if ctx.task_id else "",
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "cost": result["cost"]},
            "category": "compare_models",
        }
        ctx.pending_events.append(usage_event)

    return {"results": list(results)}


def _compare_models(ctx: ToolContext, prompt: str = "", models: list = None) -> str:
    """Send the same prompt to multiple models in parallel and compare responses."""
    import asyncio
    import json as _json

    if not prompt:
        return _json.dumps({"error": "prompt is required"})
    if not models or not isinstance(models, list):
        return _json.dumps({"error": "models must be a non-empty list of OpenRouter model IDs"})
    if len(models) < 2:
        return _json.dumps({"error": "provide at least 2 models to compare"})
    if len(models) > 6:
        return _json.dumps({"error": "maximum 6 models allowed per comparison"})
    for m in models:
        if not isinstance(m, str) or "/" not in m:
            return _json.dumps({"error": f"invalid model ID '{m}': must be in provider/model-name format"})

    try:
        try:
            asyncio.get_running_loop()
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                result = pool.submit(asyncio.run, _compare_models_async(prompt, models, ctx)).result()
        except RuntimeError:
            result = asyncio.run(_compare_models_async(prompt, models, ctx))
        return _json.dumps(result, ensure_ascii=False)
    except Exception as e:
        log.error("compare_models failed: %s", e, exc_info=True)
        return _json.dumps({"error": f"compare_models failed: {e}"})


def _get_task_result(ctx: ToolContext, task_id: str) -> str:
    """Read the result of a completed subtask."""
    results_dir = Path(ctx.drive_root) / "task_results"
    result_file = results_dir / f"{task_id}.json"
    if not result_file.exists():
        return f"Task {task_id}: not found or not yet completed"
    data = json.loads(result_file.read_text())
    status = data.get("status", "unknown")
    result = data.get("result", "")
    cost = data.get("cost_usd", 0)
    return f"Task {task_id} [{status}]: cost=${cost:.2f}\n\n[BEGIN_SUBTASK_OUTPUT]\n{result}\n[END_SUBTASK_OUTPUT]"


def _wait_for_task(ctx: ToolContext, task_id: str) -> str:
    """Check if a subtask has completed. Call repeatedly to poll."""
    results_dir = Path(ctx.drive_root) / "task_results"
    result_file = results_dir / f"{task_id}.json"
    if result_file.exists():
        return _get_task_result(ctx, task_id)
    return f"Task {task_id}: still running. Call again later to check."


def get_tools() -> List[ToolEntry]:
    return [
        ToolEntry("request_restart", {
            "name": "request_restart",
            "description": "Ask supervisor to restart runtime (after successful push).",
            "parameters": {"type": "object", "properties": {"reason": {"type": "string"}}, "required": ["reason"]},
        }, _request_restart),
        ToolEntry("promote_to_stable", {
            "name": "promote_to_stable",
            "description": "Create a stable tag at current HEAD. Call when you consider the code stable. "
                           "Used as rollback point on crashes.",
            "parameters": {"type": "object", "properties": {"reason": {"type": "string"}}, "required": ["reason"]},
        }, _promote_to_stable),
        ToolEntry("schedule_task", {
            "name": "schedule_task",
            "description": "Schedule a background task. Returns task_id for later retrieval. For complex tasks, decompose into focused subtasks with clear scope.",
            "parameters": {"type": "object", "properties": {
                "description": {"type": "string", "description": "Task description — be specific about scope and expected deliverable"},
                "context": {"type": "string", "description": "Optional context from parent task: background info, constraints, style guide, etc."},
                "parent_task_id": {"type": "string", "description": "Optional parent task ID for tracking lineage"},
            }, "required": ["description"]},
        }, _schedule_task),
        ToolEntry("cancel_task", {
            "name": "cancel_task",
            "description": "Cancel a task by ID.",
            "parameters": {"type": "object", "properties": {"task_id": {"type": "string"}}, "required": ["task_id"]},
        }, _cancel_task),
        ToolEntry("request_review", {
            "name": "request_review",
            "description": "Request a deep review of code, prompts, and state. You decide when a review is needed.",
            "parameters": {"type": "object", "properties": {
                "reason": {"type": "string", "description": "Why you want a review (context for the reviewer)"},
            }, "required": ["reason"]},
        }, _request_review),
        ToolEntry("chat_history", {
            "name": "chat_history",
            "description": "Retrieve messages from chat history. Supports search.",
            "parameters": {"type": "object", "properties": {
                "count": {"type": "integer", "default": 100, "description": "Number of messages (from latest)"},
                "offset": {"type": "integer", "default": 0, "description": "Skip N from end (pagination)"},
                "search": {"type": "string", "default": "", "description": "Text filter"},
            }, "required": []},
        }, _chat_history),
        ToolEntry("update_scratchpad", {
            "name": "update_scratchpad",
            "description": "Update your working memory. Write freely — any format you find useful. "
                           "This persists across sessions and is read at every task start.",
            "parameters": {"type": "object", "properties": {
                "content": {"type": "string", "description": "Full scratchpad content"},
            }, "required": ["content"]},
        }, _update_scratchpad),
        ToolEntry("send_owner_message", {
            "name": "send_owner_message",
            "description": "Send a proactive message to the owner. Use when you have something "
                           "genuinely worth saying — an insight, a question, or an invitation to collaborate. "
                           "This is NOT for task responses (those go automatically).",
            "parameters": {"type": "object", "properties": {
                "text": {"type": "string", "description": "Message text"},
                "reason": {"type": "string", "description": "Why you're reaching out (logged, not sent)"},
            }, "required": ["text"]},
        }, _send_owner_message),
        ToolEntry("update_identity", {
            "name": "update_identity",
            "description": "Update your identity manifest (who you are, who you want to become). "
                           "Persists across sessions.",
            "parameters": {"type": "object", "properties": {
                "content": {"type": "string", "description": "Full identity content"},
            }, "required": ["content"]},
        }, _update_identity),
        ToolEntry("update_user_context", {
            "name": "update_user_context",
            "description": "Update user context (who the user is, their goals, current priorities). "
                           "Keep under 1000 characters. Read at every dialogue.",
            "parameters": {"type": "object", "properties": {
                "content": {"type": "string", "description": "Full user context content"},
            }, "required": ["content"]},
        }, _update_user_context),
        ToolEntry("toggle_evolution", {
            "name": "toggle_evolution",
            "description": "Enable or disable evolution mode. When enabled, Ouro runs continuous self-improvement cycles.",
            "parameters": {"type": "object", "properties": {
                "enabled": {"type": "boolean", "description": "true to enable, false to disable"},
            }, "required": ["enabled"]},
        }, _toggle_evolution),
        ToolEntry("toggle_consciousness", {
            "name": "toggle_consciousness",
            "description": "Control background consciousness: 'start', 'stop', or 'status'.",
            "parameters": {"type": "object", "properties": {
                "action": {"type": "string", "enum": ["start", "stop", "status"], "description": "Action to perform"},
            }, "required": ["action"]},
        }, _toggle_consciousness),
        ToolEntry("switch_model", {
            "name": "switch_model",
            "description": "Switch to any OpenRouter model or change reasoning effort. "
                           "Accepts any valid OpenRouter model ID (format: provider/model-name, "
                           "e.g. openai/gpt-4o, google/gemini-2.5-pro-preview, meta-llama/llama-3.3-70b-instruct). "
                           "Call with no args to see configured suggestions. Takes effect on next round.",
            "parameters": {"type": "object", "properties": {
                "model": {"type": "string", "description": "Any OpenRouter model ID in provider/model-name format. Leave empty to keep current."},
                "effort": {"type": "string", "enum": ["low", "medium", "high", "xhigh"],
                           "description": "Reasoning effort level. Leave empty to keep current."},
            }, "required": []},
        }, _switch_model),
        ToolEntry("compare_models", {
            "name": "compare_models",
            "description": "Send the same prompt to multiple LLM models in parallel and compare their responses. "
                           "Useful for evaluating model quality, consistency, or choosing the best model for a task. "
                           "Budget is tracked automatically.",
            "parameters": {"type": "object", "properties": {
                "prompt": {"type": "string", "description": "The prompt to send to all models"},
                "models": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of 2–6 OpenRouter model IDs to compare (e.g. ['openai/gpt-4o', 'google/gemini-2.5-pro-preview'])",
                    "minItems": 2,
                    "maxItems": 6,
                },
            }, "required": ["prompt", "models"]},
        }, _compare_models),
        ToolEntry("get_task_result", {
            "name": "get_task_result",
            "description": "Read the result of a completed subtask. Use after schedule_task to collect results.",
            "parameters": {"type": "object", "required": ["task_id"], "properties": {
                "task_id": {"type": "string", "description": "Task ID returned by schedule_task"},
            }},
        }, _get_task_result),
        ToolEntry("wait_for_task", {
            "name": "wait_for_task",
            "description": "Check if a subtask has completed. Returns result if done, or 'still running' message. Call repeatedly to poll. Default timeout: 120s.",
            "parameters": {"type": "object", "required": ["task_id"], "properties": {
                "task_id": {"type": "string", "description": "Task ID to check"},
            }},
        }, _wait_for_task),
    ]
