"""Spartak Moscow match monitor — scrapes sports.ru/football/club/spartak/calendar/."""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from typing import Any

import requests

logger = logging.getLogger(__name__)

URL = "https://www.sports.ru/football/club/spartak/calendar/"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Referer": "https://www.sports.ru/",
}


def _fetch_matches() -> list[dict[str, str]]:
    """Fetch and parse all matches from the calendar page."""
    from bs4 import BeautifulSoup  # lazy import — optional dep

    resp = requests.get(URL, headers=HEADERS, timeout=15)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    lines = [l.strip() for l in soup.get_text().splitlines() if l.strip()]

    # Find the header row — the match blocks follow immediately after
    # Header is: Дата, Турнир, Соперник, Счет, Зрители
    start_idx = None
    for i, line in enumerate(lines):
        if line == "Зрители" and i >= 3:
            # Check the preceding lines match header pattern
            if lines[i - 1] == "Счет" and lines[i - 2] == "Соперник":
                start_idx = i + 1
                break

    if start_idx is None:
        # Fallback: find first line matching date pattern
        for i, line in enumerate(lines):
            if re.match(r"\d{2}\.\d{2}\.\d{4}\|\d{2}:\d{2}", line):
                start_idx = i
                break

    if start_idx is None:
        return []

    DATE_RE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})\|(\d{2}:\d{2})")
    VENUE_SUFFIXES = ("Дома", "В гостях")

    matches = []
    i = start_idx
    while i < len(lines):
        line = lines[i]
        m = DATE_RE.match(line)
        if not m:
            i += 1
            continue

        day, month, year, time_str = m.group(1), m.group(2), m.group(3), m.group(4)
        date_str = f"{day}.{month}.{year}"
        full_dt_str = f"{year}-{month}-{day} {time_str}"
        try:
            dt = datetime.strptime(full_dt_str, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
        except ValueError:
            i += 1
            continue

        # Expect tournament on next line
        if i + 3 >= len(lines):
            break
        tournament = lines[i + 1]
        opponent_raw = lines[i + 2]
        score = lines[i + 3]

        # Parse venue from opponent_raw
        venue = "unknown"
        opponent = opponent_raw
        for suffix in VENUE_SUFFIXES:
            if opponent_raw.endswith(suffix):
                venue = suffix
                opponent = opponent_raw[: -len(suffix)].strip()
                break

        matches.append(
            {
                "date": date_str,
                "time": time_str,
                "dt": dt,
                "tournament": tournament,
                "opponent": opponent,
                "venue": venue,
                "score": score,
            }
        )
        i += 5  # skip date + tournament + opponent + score + attendance

    return matches


def check_spartak_matches(send_telegram: bool = True) -> str:
    """Return formatted upcoming Spartak matches and optionally send to Telegram."""
    try:
        all_matches = _fetch_matches()
    except ImportError:
        return (
            "⚽ Матчи «Спартака»\n\n"
            "❌ Ошибка: BeautifulSoup4 не установлен. Запустите: pip install beautifulsoup4"
        )
    except requests.HTTPError as exc:
        code = exc.response.status_code if exc.response is not None else "?"
        return (
            f"⚽ Матчи «Спартака»\n\n"
            f"❌ Не удалось загрузить расписание (HTTP {code}).\n"
            f"Проверьте вручную: {URL}"
        )
    except Exception as exc:
        logger.exception("spartak_monitor: unexpected error")
        return f"⚽ Матчи «Спартака»\n\n❌ Ошибка: {exc}\nПроверьте вручную: {URL}"

    if not all_matches:
        return (
            f"⚽ Матчи «Спартака»\n\n"
            f"Не удалось распарсить расписание.\n"
            f"Проверьте вручную: {URL}"
        )

    now = datetime.now(timezone.utc)
    upcoming = [m for m in all_matches if m["dt"] >= now]

    # Limit to next ~3 months of upcoming matches
    from datetime import timedelta
    cutoff = now + timedelta(days=90)
    upcoming = [m for m in upcoming if m["dt"] <= cutoff]

    if not upcoming:
        msg = (
            "⚽ Матчи «Спартака» — ближайшие игры\n\n"
            "📭 Нет запланированных матчей в ближайшие 3 месяца.\n"
            f"Расписание: {URL}"
        )
    else:
        home = [m for m in upcoming if m["venue"] == "Дома"]
        away = [m for m in upcoming if m["venue"] != "Дома"]

        lines_out = ["⚽ Матчи «Спартака» — ближайшие игры\n"]

        if home:
            lines_out.append("🏠 Домашние:")
            for m in home:
                lines_out.append(f"• {m['date']} {m['time']} — Спартак vs {m['opponent']} ({m['tournament']})")
        else:
            lines_out.append("🏠 Домашних матчей не запланировано")

        lines_out.append("")

        if away:
            lines_out.append("🚌 Выездные:")
            for m in away:
                lines_out.append(f"• {m['date']} {m['time']} — {m['opponent']} vs Спартак ({m['tournament']})")
        else:
            lines_out.append("🚌 Выездных матчей не запланировано")

        lines_out.append("")
        lines_out.append("📅 Следующий отчёт через 2 недели")

        msg = "\n".join(lines_out)

    if send_telegram:
        try:
            import httpx

            bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
            chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
            if bot_token and chat_id:
                httpx.post(
                    f"https://api.telegram.org/bot{bot_token}/sendMessage",
                    json={"chat_id": int(chat_id), "text": msg},
                    timeout=10,
                )
        except Exception:
            logger.exception("spartak_monitor: failed to send Telegram message")

    return msg


def get_tools() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "check_spartak_matches",
                "description": (
                    "Fetch upcoming Spartak Moscow football match schedule from sports.ru. "
                    "Returns upcoming home and away matches with dates, opponents, and tournaments."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "send_telegram": {
                            "type": "boolean",
                            "description": "Whether to send the result via Telegram (default: true)",
                        }
                    },
                    "required": [],
                },
            },
            "handler": check_spartak_matches,
        }
    ]
