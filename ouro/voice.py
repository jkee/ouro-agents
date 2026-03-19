"""Voice message transcription via OpenAI Whisper API."""

import io
import logging
import os
from typing import Optional

log = logging.getLogger(__name__)


def transcribe_voice(audio_bytes: bytes, file_ext: str = "oga") -> Optional[str]:
    """
    Transcribe audio bytes using OpenAI Whisper API.

    Args:
        audio_bytes: Raw audio file bytes
        file_ext: File extension (oga, ogg, mp3, mp4, wav, etc.)

    Returns:
        Transcribed text or None on failure.
    """
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        log.warning("OPENAI_API_KEY not set — cannot transcribe voice")
        return None

    try:
        import requests

        # Whisper accepts: mp3, mp4, mpeg, mpga, m4a, wav, webm, ogg, oga
        # Telegram voice messages come as .oga (Ogg Opus)
        filename = f"voice.{file_ext}"

        response = requests.post(
            "https://api.openai.com/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {api_key}"},
            data={"model": "whisper-1"},
            files={"file": (filename, io.BytesIO(audio_bytes), "audio/ogg")},
            timeout=60,
        )
        response.raise_for_status()
        result = response.json()
        text = result.get("text", "").strip()
        log.info("Voice transcribed: %d chars", len(text))
        return text if text else None
    except Exception:
        log.warning("Voice transcription failed", exc_info=True)
        return None
