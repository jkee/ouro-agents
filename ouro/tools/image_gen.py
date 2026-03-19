"""Image generation via OpenRouter image models."""

from __future__ import annotations

import json
import os
from typing import List

import httpx

from ouro.tools.registry import ToolContext, ToolEntry

DEFAULT_MODEL = "black-forest-labs/flux.2-klein-4b"


def _generate_image(
    ctx: ToolContext,
    prompt: str,
    model: str = DEFAULT_MODEL,
) -> str:
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        return json.dumps({"error": "OPENROUTER_API_KEY not set"})

    modalities = ["image", "text"] if model.startswith("google/") else ["image"]

    payload = {
        "model": model,
        "modalities": modalities,
        "messages": [{"role": "user", "content": prompt}],
    }

    try:
        resp = httpx.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=90,
        )
    except Exception as e:
        return json.dumps({"error": repr(e)})

    if resp.status_code != 200:
        return json.dumps({"error": f"HTTP {resp.status_code}", "body": resp.text})

    data = resp.json()

    try:
        message = data["choices"][0]["message"]
    except (KeyError, IndexError) as e:
        return json.dumps({"error": f"Unexpected response shape: {e}", "raw": data})

    images = message.get("images") or []
    if not images:
        return json.dumps({"error": "No images field in response", "raw": data})

    image_url = images[0].get("image_url", {}).get("url", "")
    if not image_url:
        return json.dumps({"error": "image_url missing", "raw": images[0]})

    # Strip data URL prefix to get raw base64
    if ";base64," in image_url:
        image_b64 = image_url.split(";base64,", 1)[1]
    else:
        image_b64 = image_url

    if ctx.current_chat_id:
        caption = message.get("content") or ""
        if isinstance(caption, list):
            caption = " ".join(p.get("text", "") for p in caption if p.get("type") == "text").strip()
        ctx.pending_events.append({
            "type": "send_photo",
            "chat_id": ctx.current_chat_id,
            "image_base64": image_b64,
            "caption": caption or "",
        })
        return json.dumps({"status": "queued", "caption": caption or ""})

    return json.dumps({"image_url": image_url})


def get_tools() -> List[ToolEntry]:
    return [
        ToolEntry(
            "generate_image",
            {
                "name": "generate_image",
                "description": (
                    "Generate an image from a text prompt using OpenRouter image models. "
                    "Automatically sends the image to the active Telegram chat via send_photo event. "
                    "Default model is fast and cheap (FLUX Klein). Use flux.2-pro for high quality."
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
                            "description": "OpenRouter model to use for generation",
                            "enum": [
                                "black-forest-labs/flux.2-klein-4b",
                                "black-forest-labs/flux.2-pro",
                                "google/gemini-3.1-flash-image-preview",
                            ],
                            "default": DEFAULT_MODEL,
                        },
                    },
                    "required": ["prompt"],
                },
            },
            _generate_image,
        ),
    ]
