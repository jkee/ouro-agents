"""Ski lift queue checker — cam-402 (KD Edelweis, 1472m, Rosa Khutor)."""
from __future__ import annotations

import base64
import json
import logging
import os
import subprocess
import tempfile
from typing import List

import requests

from ouro.tools.registry import ToolContext, ToolEntry

log = logging.getLogger(__name__)

_VLM_MODEL = "anthropic/claude-sonnet-4-6"

_QUEUE_PROMPT = """\
This is a frame from a ski lift camera at KD Edelweiss, 1472m, Rosa Khutor.

Analyze the queue at the lift boarding area and respond in JSON:
{
  "score": <integer 0-10>,
  "people_estimate": <integer>,
  "wait_minutes": <integer estimate>,
  "description": "<1-2 sentence description in Russian>",
  "conditions": "<snow/weather conditions in Russian>"
}

Scale:
0 = no queue at all
1-2 = 1-5 people, no wait
3-4 = 5-15 people, ~5 min
5-6 = 15-30 people, ~10-15 min
7-8 = 30-60 people, queue curves around corner, ~20-30 min
9-10 = 100+ people, huge queue, 30+ min

Respond ONLY with the JSON, no other text.\
"""


def _get_hls_url() -> str:
    """Fetch current HLS stream URL from sochi.camera API."""
    r = requests.get(
        "https://sochi.camera/vse-kamery/cam-402/?format=json",
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Cookie": "player=402",
            "Referer": "https://sochi.camera/vse-kamery/cam-402/",
        },
        timeout=15,
    )
    r.raise_for_status()
    d = json.loads(r.content.decode("utf-8-sig"))
    url = d.get("hd_url") or d.get("sd_url")
    if not url:
        raise RuntimeError(f"No stream URL in API response. Keys: {list(d.keys())}")
    return url


def _capture_frame(hls_url: str) -> bytes:
    """Grab one frame from HLS stream via ffmpeg. Returns JPEG bytes."""
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
        out_path = f.name
    try:
        result = subprocess.run(
            ["ffmpeg", "-y", "-i", hls_url, "-vframes", "1", "-q:v", "2", out_path],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg error: {result.stderr[-300:]}")
        with open(out_path, "rb") as f:
            return f.read()
    finally:
        if os.path.exists(out_path):
            os.unlink(out_path)


def _analyze_queue(frame_bytes: bytes) -> dict:
    """Send frame to vision LLM and return parsed queue assessment dict."""
    from ouro.llm import LLMClient

    b64 = base64.b64encode(frame_bytes).decode()
    client = LLMClient()
    text, _ = client.vision_query(
        prompt=_QUEUE_PROMPT,
        images=[{"base64": b64, "mime": "image/jpeg"}],
        model=_VLM_MODEL,
        max_tokens=512,
        reasoning_effort="low",
    )
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    return json.loads(text)


def _send_photo_telegram(image_bytes: bytes, caption: str, chat_id: int, token: str) -> None:
    """Send photo with caption via Telegram sendPhoto."""
    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    resp = requests.post(
        url,
        data={"chat_id": chat_id, "caption": caption},
        files={"photo": ("edelweis.jpg", image_bytes, "image/jpeg")},
        timeout=30,
    )
    resp.raise_for_status()


def _check_ski_queue(ctx: ToolContext, send_telegram: bool = True) -> str:
    """Main handler: capture frame, analyze queue, optionally send to Telegram."""
    # 1. Get HLS URL
    try:
        hls_url = _get_hls_url()
    except Exception as e:
        return f"⚠️ Failed to get HLS URL: {e}"

    # 2. Capture frame
    try:
        frame_bytes = _capture_frame(hls_url)
    except subprocess.TimeoutExpired:
        return "⚠️ ffmpeg timed out after 30 seconds."
    except Exception as e:
        return f"⚠️ Frame capture failed: {e}"

    # 3. Analyze with VLM
    try:
        analysis = _analyze_queue(frame_bytes)
    except Exception as e:
        log.warning("VLM analysis failed: %s", e, exc_info=True)
        return f"⚠️ VLM analysis failed: {e}"

    score = analysis.get("score", "?")
    people = analysis.get("people_estimate", "?")
    wait = analysis.get("wait_minutes", "?")
    desc = analysis.get("description", "")
    conditions = analysis.get("conditions", "")

    caption = (
        f"🎿 КД Эдельвейс 1472м\n"
        f"Очередь: {score}/10 (~{people} чел., ждать ~{wait} мин)\n"
        f"{desc}\n"
        f"🌤 {conditions}"
    )

    # 4. Send to Telegram
    if send_telegram:
        token = os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("TG_BOT_TOKEN", "")
        chat_id_raw = (ctx.current_chat_id if ctx.current_chat_id else None) or os.environ.get("OURO_OWNER_ID", "63675289")
        try:
            chat_id = int(chat_id_raw)
        except (ValueError, TypeError):
            chat_id = 63675289

        if token:
            try:
                _send_photo_telegram(frame_bytes, caption, chat_id, token)
            except Exception as e:
                log.warning("Telegram send failed: %s", e)
                return caption + f"\n⚠️ Telegram send failed: {e}"

    return caption


def get_tools() -> List[ToolEntry]:
    return [
        ToolEntry(
            name="check_ski_queue",
            schema={
                "name": "check_ski_queue",
                "description": (
                    "Capture a live frame from Rosa Khutor KD Edelweis ski lift camera (cam-402, 1472m) "
                    "and analyze the queue on a 0-10 scale. "
                    "Sends photo + analysis to Telegram by default."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "send_telegram": {
                            "type": "boolean",
                            "description": "Send the frame + analysis to Telegram (default: true)",
                        },
                    },
                    "required": [],
                },
            },
            handler=_check_ski_queue,
            timeout_sec=120,
        ),
    ]
