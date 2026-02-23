"""Memo tools: personal memory subsystem powered by mem0ai.

Provides semantic memory for the user: store memos, facts, reminders,
and search them by meaning. Backed by Chroma (local SQLite) + OpenRouter LLM.

User: Viktor Tarnavsky (jkee)
Storage: /data/memory/chroma/
"""

from __future__ import annotations

import logging
import os
from typing import List, Optional

from ouroboros.tools.registry import ToolContext, ToolEntry

log = logging.getLogger(__name__)

# Default user ID for all memories
DEFAULT_USER_ID = "jkee"

# Mem0 config: uses OpenRouter as LLM provider via openai-compatible API
def _get_mem0_config(ctx: ToolContext) -> dict:
    chroma_path = str(ctx.drive_root / "memory" / "chroma")
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    return {
        "llm": {
            "provider": "openai",
            "config": {
                "model": "openai/gpt-4o-mini",
                "openai_base_url": "https://openrouter.ai/api/v1",
                "api_key": api_key,
                "max_tokens": 2000,
            }
        },
        "embedder": {
            "provider": "openai",
            "config": {
                "model": "text-embedding-3-small",
                "openai_base_url": "https://openrouter.ai/api/v1",
                "api_key": api_key,
            }
        },
        "vector_store": {
            "provider": "chroma",
            "config": {
                "collection_name": "ouroboros_memo",
                "path": chroma_path,
            }
        },
        "version": "v1.1"
    }


def _get_memory(ctx: ToolContext):
    """Return initialized mem0 Memory instance."""
    try:
        from mem0 import Memory
        config = _get_mem0_config(ctx)
        return Memory.from_config(config)
    except ImportError:
        raise RuntimeError("mem0ai is not installed. Run: pip install mem0ai chromadb")


# --- Tool handlers ---

def _memo_add(ctx: ToolContext, content: str, tags: Optional[str] = None) -> str:
    """Add a memo or fact to personal memory."""
    try:
        mem = _get_memory(ctx)
        metadata = {}
        if tags:
            tag_list = [t.strip() for t in tags.split(",") if t.strip()]
            metadata["tags"] = tag_list

        messages = [{"role": "user", "content": content}]
        result = mem.add(messages, user_id=DEFAULT_USER_ID, metadata=metadata)

        # Count how many memories were added/updated
        if isinstance(result, dict):
            added = len(result.get("results", []))
            # Build summary
            items = result.get("results", [])
            memories_text = "\n".join(
                f"  • {item.get('memory', '')}"
                for item in items
                if item.get("event") in ("ADD", "UPDATE", None)
            )
            return f"✅ Saved {added} memory item(s):\n{memories_text}" if memories_text else f"✅ Saved to memory."
        return "✅ Saved to memory."
    except Exception as e:
        log.warning("memo_add failed", exc_info=True)
        return f"⚠️ Error saving memo: {repr(e)}"


def _memo_search(ctx: ToolContext, query: str, limit: int = 5) -> str:
    """Search personal memory by semantic query."""
    try:
        mem = _get_memory(ctx)
        results = mem.search(query, user_id=DEFAULT_USER_ID, limit=limit)

        if not results:
            return "Nothing found matching that query."

        # Handle both list and dict response formats
        if isinstance(results, dict):
            items = results.get("results", [])
        else:
            items = results

        if not items:
            return "Nothing found matching that query."

        lines = []
        for i, item in enumerate(items, 1):
            memory_text = item.get("memory", str(item))
            score = item.get("score", None)
            meta = item.get("metadata", {})
            tags = meta.get("tags", []) if meta else []
            full_id = item.get("id", "?")

            score_str = f"{score:.2f}" if score is not None else "n/a"
            tags_str = ", ".join(tags) if tags else "—"
            line = (
                f"{i}. {memory_text}\n"
                f"   ID: {full_id} | tags: {tags_str} | relevance: {score_str}"
            )
            lines.append(line)

        return "📋 Found memories:\n" + "\n".join(lines)
    except Exception as e:
        log.warning("memo_search failed", exc_info=True)
        return f"⚠️ Error searching memory: {repr(e)}"


def _memo_list(ctx: ToolContext, limit: int = 20) -> str:
    """List recent memos from personal memory."""
    try:
        mem = _get_memory(ctx)
        results = mem.get_all(user_id=DEFAULT_USER_ID)

        if isinstance(results, dict):
            items = results.get("results", [])
        else:
            items = results or []

        if not items:
            return "Memory is empty. Use memo_add to save something."

        # Sort by created_at if available, take last `limit`
        try:
            items_sorted = sorted(
                items,
                key=lambda x: x.get("created_at", ""),
                reverse=True
            )[:limit]
        except Exception:
            items_sorted = items[:limit]

        lines = []
        for i, item in enumerate(items_sorted, 1):
            memory_text = item.get("memory", str(item))
            created = item.get("created_at", "")
            meta = item.get("metadata", {})
            tags = meta.get("tags", []) if meta else []
            id_short = item.get("id", "?")[:8]

            date_str = created[:10] if created else "—"
            tags_str = ", ".join(tags) if tags else "—"
            line = f"{i}. [{id_short}] {memory_text} [tags: {tags_str}] ({date_str})"
            lines.append(line)

        total = len(items)
        shown = len(lines)
        header = f"📋 Memories ({shown} of {total} total):\n"
        return header + "\n".join(lines)
    except Exception as e:
        log.warning("memo_list failed", exc_info=True)
        return f"⚠️ Error listing memories: {repr(e)}"


def _memo_delete(ctx: ToolContext, memory_id: str) -> str:
    """Delete a specific memory by ID."""
    try:
        mem = _get_memory(ctx)
        mem.delete(memory_id)
        return f"✅ Memory {memory_id} deleted."
    except Exception as e:
        log.warning("memo_delete failed", exc_info=True)
        return f"⚠️ Error deleting memory: {repr(e)}"


# --- Tool registration ---

def get_tools() -> List[ToolEntry]:
    return [
        ToolEntry("memo_add", {
            "name": "memo_add",
            "description": (
                "Add a memo, fact, reminder, or any information to the user's personal semantic memory. "
                "Use when the user says 'remember', 'note', 'save', 'запомни', 'заметка' etc. "
                "Also use implicitly when user shares important personal info worth remembering."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "The text to remember. Can be a fact, note, reminder, or any information."
                    },
                    "tags": {
                        "type": "string",
                        "description": "Optional comma-separated tags (e.g. 'work,important,deadline')"
                    }
                },
                "required": ["content"]
            },
        }, _memo_add),

        ToolEntry("memo_search", {
            "name": "memo_search",
            "description": (
                "Search personal memory by semantic meaning. Use when user asks to recall something, "
                "find a note, or retrieve stored information. E.g. 'что я говорил про X', 'remind me about Y'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural language search query"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results to return (default: 5)",
                        "default": 5
                    }
                },
                "required": ["query"]
            },
        }, _memo_search),

        ToolEntry("memo_list", {
            "name": "memo_list",
            "description": (
                "List all memos and memories stored for the user. Use when user asks to see all notes, "
                "'покажи заметки', 'list my memos', etc."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Max items to show (default: 20)",
                        "default": 20
                    }
                },
                "required": []
            },
        }, _memo_list),

        ToolEntry("memo_delete", {
            "name": "memo_delete",
            "description": "Delete a specific memory entry by its ID. Use when user wants to remove a memo.",
            "parameters": {
                "type": "object",
                "properties": {
                    "memory_id": {
                        "type": "string",
                        "description": "The memory ID to delete (from memo_list output, shown as 8-char prefix like 'abc12345')"
                    }
                },
                "required": ["memory_id"]
            },
        }, _memo_delete),
    ]
