"""Image generation tool via OpenRouter image models."""

from __future__ import annotations

import logging
import os
from typing import List

import httpx

from ouro.tools.registry import ToolContext, ToolEntry

log = logging.getLogger(__name__)

_DEFAULT_MODEL = "black-forest-labs/flux.2-klein-4b"


def _generate_image(ctx: ToolContext, prompt: str, model: str = _DEFAULT_MODEL) -> str:
    if not ctx.current_chat_id:
        return "⚠️ No active chat — cannot deliver image."

    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        return "⚠️ OPENROUTER_API_KEY not set; generate_image unavailable."

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        resp = httpx.post(
            "https://openrouter.ai/api/v1/chat/completions",
            json=payload,
            headers=headers,
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPStatusError as e:
        return f"⚠️ OpenRouter API error {e.response.status_code}: {e.response.text[:200]}"
    except Exception as e:
        return f"⚠️ Network error calling OpenRouter: {e}"

    try:
        url = data["choices"][0]["message"]["images"][0]["image_url"]["url"]
    except (KeyError, IndexError, TypeError):
        return f"⚠️ No image in response. Response keys: {list(data.keys())}"

    # url is "data:image/png;base64,<b64data>"
    if "," in url:
        b64_data = url.split(",", 1)[1]
    else:
        b64_data = url

    ctx.pending_events.append({
        "type": "send_photo",
        "chat_id": ctx.current_chat_id,
        "image_base64": b64_data,
        "caption": prompt[:100],
    })
    return f"OK: image generated with {model} and queued for delivery."


def get_tools() -> List[ToolEntry]:
    return [
        ToolEntry("generate_image", {
            "name": "generate_image",
            "description": (
                "Generate an image from a text prompt using an OpenRouter image model "
                "and send it to the current chat. "
                "Available models: black-forest-labs/flux.2-klein-4b (fast/cheap), "
                "black-forest-labs/flux.2-pro (high quality), "
                "google/gemini-2.5-flash-image (creative)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "Text description of the image to generate",
                    },
                    "model": {
                        "type": "string",
                        "description": (
                            "OpenRouter image model ID. "
                            "Options: black-forest-labs/flux.2-klein-4b (fast/cheap), "
                            "black-forest-labs/flux.2-pro (high quality), "
                            "google/gemini-2.5-flash-image (creative)"
                        ),
                        "default": _DEFAULT_MODEL,
                    },
                },
                "required": ["prompt"],
            },
        }, _generate_image),
    ]
