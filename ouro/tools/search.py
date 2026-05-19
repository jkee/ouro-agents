"""Web search tool."""

from __future__ import annotations

import json
import logging
import os
import random
import time
from typing import Any, Dict, List

from ouro.tools.registry import ToolContext, ToolEntry

_log = logging.getLogger(__name__)


def _web_search(ctx: ToolContext, query: str) -> str:
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        return json.dumps({"error": "OPENAI_API_KEY not set; web_search unavailable."})

    max_attempts = 3
    last_exc = None

    for attempt in range(1, max_attempts + 1):
        try:
            from openai import OpenAI
            client = OpenAI(api_key=api_key)
            resp = client.responses.create(
                model=os.environ.get("OURO_WEBSEARCH_MODEL", "gpt-5.4-mini"),
                tools=[{"type": "web_search"}],
                tool_choice="auto",
                input=query,
            )
            d = resp.model_dump()
            text = ""
            for item in d.get("output", []) or []:
                if item.get("type") == "message":
                    for block in item.get("content", []) or []:
                        if block.get("type") in ("output_text", "text"):
                            text += block.get("text", "")
            return json.dumps({"answer": text or "(no answer)"}, ensure_ascii=False, indent=2)
        except Exception as e:
            exc_str = str(e).lower()
            is_retryable = any(k in exc_str for k in ("429", "500", "502", "503", "504", "rate limit", "timeout", "connection", "overloaded"))
            if not is_retryable or attempt == max_attempts:
                return json.dumps({"error": repr(e)}, ensure_ascii=False)
            last_exc = e
            delay = min(2.0 * (2 ** (attempt - 1)), 30.0) * (0.8 + random.random() * 0.4)
            _log.warning(
                "web_search attempt %d/%d failed: %s — retrying in %.1fs",
                attempt, max_attempts, type(e).__name__, delay,
            )
            time.sleep(delay)

    return json.dumps({"error": repr(last_exc)}, ensure_ascii=False)


def get_tools() -> List[ToolEntry]:
    return [
        ToolEntry("web_search", {
            "name": "web_search",
            "description": "Search the web via OpenAI Responses API. Returns JSON with answer + sources.",
            "parameters": {"type": "object", "properties": {
                "query": {"type": "string"},
            }, "required": ["query"]},
        }, _web_search),
    ]
