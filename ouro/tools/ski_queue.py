"""
Ski queue analyzer — Rosa Khutor KD Edelweis gondola station.

Grabs a live frame via HLS stream from sochi.camera, analyzes the queue with VLM,
and optionally sends the photo + analysis to Telegram.
"""

from __future__ import annotations

import base64
import logging
import os
import subprocess
import tempfile
import time
from typing import List

import requests

from ouro.tools.registry import ToolContext, ToolEntry

log = logging.getLogger(__name__)

_CAM_API_URL = "https://sochi.camera/api/getCamStream?id=402"
_CAM_HEADERS = {
    "Cookie": "player=402",
    "Referer": "https://sochi.camera/cam-402/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}

_VLM_MODEL = "anthropic/claude-sonnet-4-6"

_QUEUE_PROMPT = """\
This is a live camera frame from a ski gondola/chairlift station "KD Edelweis" at Rosa Khutor ski resort, 1472m altitude.

Analyze the queue at the lift:
1. How many people are visible waiting in queue?
2. Rate the queue on a scale 1-10:
   - 1-2: Empty or 1-5 people, no wait
   - 3-4: Small queue, 5-20 people, 5 min wait
   - 5-6: Moderate queue, 20-40 people, 10-15 min wait
   - 7-8: Long queue, 40-70 people, 20-30 min wait
   - 9-10: MAXIMUM queue, 70-100+ people, queue bends around the corner
3. Brief description of current conditions (weather, visibility, snow)

Respond in Russian. Format:
🚡 Очередь: X/10
👥 Людей: ~N
⏱️ Ожидание: ~X мин
🌤 Условия: [описание]\
"""


def _get_hls_url() -> str:
    """Call sochi.camera API and return the HLS stream URL."""
    resp = requests.get(_CAM_API_URL, headers=_CAM_HEADERS, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    url = data.get("src") or data.get("url")
    if not url:
        raise ValueError(f"HLS URL not found in API response — keys present: {list(data.keys())}")
    return url


def _analyze_image(image_b64: str) -> str:
    """Run VLM analysis on the image."""
    from ouro.llm import LLMClient

    client = LLMClient()
    text, _ = client.vision_query(
        prompt=_QUEUE_PROMPT,
        images=[{"base64": image_b64, "mime": "image/jpeg"}],
        model=_VLM_MODEL,
        max_tokens=512,
        reasoning_effort="low",
    )
    return text or "(нет ответа от VLM)"


def _send_telegram(image_bytes: bytes, caption: str, chat_id: int, token: str) -> None:
    """Send photo with caption via Telegram sendPhoto."""
    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    resp = requests.post(
        url,
        data={"chat_id": chat_id, "caption": caption},
        files={"photo": ("edelweis.jpg", image_bytes, "image/jpeg")},
        timeout=30,
    )
    resp.raise_for_status()


def _check_ski_queue(ctx: ToolContext, silent: bool = False) -> str:
    # 1. Get HLS URL from API
    try:
        hls_url = _get_hls_url()
    except Exception as e:
        return f"⚠️ Failed to get HLS URL: {e}"

    # 2. Grab one frame with ffmpeg
    timestamp = int(time.time())
    tmp_path = f"/tmp/ski_frame_{timestamp}.jpg"
    try:
        result = subprocess.run(
            ["ffmpeg", "-i", hls_url, "-vframes", "1", "-q:v", "2", tmp_path],
            capture_output=True,
            timeout=30,
        )
        if result.returncode != 0:
            err = result.stderr.decode(errors="replace").strip()
            return f"⚠️ ffmpeg failed (exit {result.returncode}): {err}"
    except subprocess.TimeoutExpired:
        return "⚠️ ffmpeg timed out after 30 seconds."
    except Exception as e:
        return f"⚠️ ffmpeg error: {e}"

    # 3. Read the frame
    try:
        with open(tmp_path, "rb") as f:
            image_bytes = f.read()
    except Exception as e:
        return f"⚠️ Failed to read captured frame: {e}"
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    image_b64 = base64.b64encode(image_bytes).decode()

    # 4. Analyze with VLM
    try:
        analysis = _analyze_image(image_b64)
    except Exception as e:
        log.warning("VLM analysis failed: %s", e, exc_info=True)
        analysis = f"(анализ недоступен: {e})"

    # 5. Send to Telegram unless silent
    if not silent:
        token = os.environ.get("TG_BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN", "")
        chat_id_str = os.environ.get("OURO_OWNER_ID", "63675289")
        try:
            chat_id = int(chat_id_str)
        except ValueError:
            chat_id = 63675289

        if token:
            caption = analysis or "Кадр с камеры KD Edelweis"
            try:
                _send_telegram(image_bytes, caption, chat_id, token)
            except Exception as e:
                log.warning("Telegram send failed: %s", e)
                return analysis + f"\n⚠️ Telegram send failed: {e}"

    return analysis or "(нет анализа)"


def get_tools() -> List[ToolEntry]:
    return [
        ToolEntry(
            name="check_ski_queue",
            schema={
                "name": "check_ski_queue",
                "description": (
                    "Capture a live frame from Rosa Khutor KD Edelweis ski lift camera "
                    "and analyze the queue (1-10 scale). "
                    "Sends photo + analysis to Telegram by default."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "silent": {
                            "type": "boolean",
                            "description": "If true, skip sending to Telegram and just return analysis (default: false)",
                        },
                    },
                    "required": [],
                },
            },
            handler=_check_ski_queue,
            timeout_sec=120,
        ),
    ]
