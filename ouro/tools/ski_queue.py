"""
Ski queue analyzer — Rosa Khutor KD Edelweis gondola station.

Captures a live frame via SSH from the VPS, analyzes the queue with VLM,
and optionally sends the photo + analysis to Telegram.
"""

from __future__ import annotations

import base64
import logging
import os
import subprocess
import tempfile
from typing import List

import requests

from ouro.tools.registry import ToolContext, ToolEntry

log = logging.getLogger(__name__)

_SSH_HOST = "jkee@158.160.81.25"
_SSH_KEY = "/root/.ssh/id_ed25519"
_REMOTE_SCRIPT = "DISPLAY=:99 python3 /home/jkee/grab_embed.py"
_REMOTE_IMAGE = "/home/jkee/edelweis_latest.jpg"
_SSH_TIMEOUT = 90

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


def _ssh_run(cmd: str, timeout: int = _SSH_TIMEOUT) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            "ssh",
            "-i", _SSH_KEY,
            "-o", "StrictHostKeyChecking=no",
            "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=15",
            _SSH_HOST,
            cmd,
        ],
        capture_output=True,
        timeout=timeout,
    )


def _fetch_remote_image() -> bytes:
    """SCP the remote image to a temp file and return raw bytes."""
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        tmp_path = tmp.name

    subprocess.run(
        [
            "scp",
            "-i", _SSH_KEY,
            "-o", "StrictHostKeyChecking=no",
            "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=15",
            f"{_SSH_HOST}:{_REMOTE_IMAGE}",
            tmp_path,
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )

    with open(tmp_path, "rb") as f:
        data = f.read()

    try:
        os.unlink(tmp_path)
    except OSError:
        pass

    return data


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
    # 1. Run grab script on VPS
    try:
        result = _ssh_run(_REMOTE_SCRIPT, timeout=_SSH_TIMEOUT)
        if result.returncode != 0:
            err = result.stderr.decode(errors="replace").strip()
            return f"⚠️ Grab script failed (exit {result.returncode}): {err}"
    except subprocess.TimeoutExpired:
        return "⚠️ Grab script timed out after 90 seconds."
    except Exception as e:
        return f"⚠️ SSH error: {e}"

    # 2. Fetch the image from VPS
    try:
        image_bytes = _fetch_remote_image()
    except Exception as e:
        return f"⚠️ Failed to fetch image: {e}"

    image_b64 = base64.b64encode(image_bytes).decode()

    # 3. Analyze with VLM
    analysis = None
    try:
        analysis = _analyze_image(image_b64)
    except Exception as e:
        log.warning("VLM analysis failed: %s", e, exc_info=True)
        analysis = f"(анализ недоступен: {e})"

    # 4. Send to Telegram unless silent
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
                return (analysis or "") + f"\n⚠️ Telegram send failed: {e}"

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
            timeout_sec=150,
        ),
    ]
