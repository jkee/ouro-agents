"""Spartak Moscow match monitor — scrapes sports.ru/football/club/spartak/calendar/ for upcoming matches."""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone, timedelta
from typing import List, Optional

import requests
from bs4 import BeautifulSoup

from ouro.tools.registry import ToolContext, ToolEntry

log = logging.getLogger(__name__)

CALENDAR_URL = "https://www.sports.ru/football/club/spartak/calendar/"
MSK = timedelta(hours=3)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://www.sports.ru/",
}


def _fetch_calendar_html() -> Optional[str]:
    """Fetch the calendar page HTML. Returns None on error."""
    try:
        r = requests.get(CALENDAR_URL, headers=_HEADERS, timeout=20)
        if r.status_code == 403:
            log.warning("sports.ru returned 403 — blocked")
            return None
        if r.status_code == 429:
            log.warning("sports.ru returned 429 — rate limited")
            return None
        r.raise_for_status()
        return r.text
    except requests.RequestException as e:
        log.warning("Failed to fetch sports.ru calendar: %s", e)
        return None


def _parse_matches(html: str) -> List[dict]:
    """Parse all matches from the calendar HTML. Returns list of match dicts."""
    soup = BeautifulSoup(html, "html.parser")
    matches = []

    # The calendar table rows look like:
    # <td class="date">06.07.2025|17:00</td>
    # <td class="tournament">...</td>
    # <td class="opponent">...</td>
    # <td class="home">Дома / В гостях</td>
    # <td class="score">2 : 2 / превью</td>

    # Find all table rows with match data
    # The text content of the page uses "|" to separate date and time
    # We'll parse the main calendar table

    table = soup.find("table", class_=re.compile(r"stat|calendar|games", re.I))
    if table is None:
        # Try to find any table with date-like content
        tables = soup.find_all("table")
        for t in tables:
            text = t.get_text()
            if "Дома" in text or "В гостях" in text:
                table = t
                break

    if table is None:
        log.warning("Could not find match table in sports.ru HTML")
        return []

    rows = table.find_all("tr")
    for row in rows:
        cells = row.find_all("td")
        if len(cells) < 4:
            continue

        # Extract text from each cell, strip whitespace
        cell_texts = [c.get_text(separator=" ", strip=True) for c in cells]

        # Find the date cell (contains "|" separator between date and time)
        date_str = None
        time_str = None
        tournament = None
        opponent = None
        venue = None  # "Дома" or "В гостях"
        score = None

        for i, text in enumerate(cell_texts):
            # Date+time cell: "06.07.2025|17:00" or "06.07.2025 17:00"
            date_match = re.match(r"(\d{2}\.\d{2}\.\d{4})[|\s](\d{2}:\d{2})", text)
            if date_match:
                date_str = date_match.group(1)
                time_str = date_match.group(2)
                # Following cells: tournament, opponent, venue, score
                if i + 1 < len(cell_texts):
                    tournament = cell_texts[i + 1].strip()
                if i + 2 < len(cell_texts):
                    opponent = cell_texts[i + 2].strip()
                if i + 3 < len(cell_texts):
                    venue = cell_texts[i + 3].strip()
                if i + 4 < len(cell_texts):
                    score = cell_texts[i + 4].strip()
                break

        if not date_str or not opponent:
            continue

        # Clean up opponent name (remove extra whitespace/icons)
        opponent = re.sub(r"\s+", " ", opponent).strip()

        # Parse the datetime (MSK)
        try:
            dt_str = f"{date_str} {time_str or '00:00'}"
            dt_msk = datetime.strptime(dt_str, "%d.%m.%Y %H:%M").replace(
                tzinfo=timezone(MSK)
            )
        except ValueError:
            continue

        matches.append(
            {
                "dt_msk": dt_msk,
                "tournament": tournament or "",
                "opponent": opponent,
                "venue": venue or "",
                "score": score or "",
            }
        )

    return matches


def check_spartak_matches(ctx: ToolContext = None, send_telegram: bool = True) -> str:
    """
    Fetch upcoming Spartak Moscow matches from sports.ru and format a message.
    Returns the formatted message string. If send_telegram=True, also sends to Telegram.
    """
    html = _fetch_calendar_html()
    if html is None:
        return (
            "⚠️ Не удалось получить расписание «Спартака» — sports.ru недоступен или заблокировал запрос.\n"
            "Проверьте вручную: https://www.sports.ru/football/club/spartak/calendar/"
        )

    matches = _parse_matches(html)
    if not matches:
        return (
            "⚽ Матчи «Спартака» — не удалось распарсить расписание.\n"
            "Проверьте вручную: https://www.sports.ru/football/club/spartak/calendar/"
        )

    now_msk = datetime.now(timezone(MSK))

    # Filter upcoming matches only (future dates)
    upcoming = [m for m in matches if m["dt_msk"] > now_msk]

    if not upcoming:
        return (
            "⚽ Матчи «Спартака» — ближайшие игры\n\n"
            "Нет запланированных матчей. Новый сезон скоро начнётся!\n"
            "📅 Следующий отчёт через 2 недели"
        )

    home_matches = [m for m in upcoming if "дома" in m["venue"].lower()]
    away_matches = [m for m in upcoming if "гост" in m["venue"].lower()]

    lines = ["⚽ Матчи «Спартака» — ближайшие игры\n"]

    if home_matches:
        lines.append("🏠 *Домашние:*")
        for m in home_matches[:8]:  # cap at 8
            dt_fmt = m["dt_msk"].strftime("%d.%m %H:%M")
            # Filter out non-essential tournaments for readability
            tournament = m["tournament"]
            # Shorten tournament names
            tournament = (
                tournament
                .replace("Россия. Премьер-лига", "РПЛ")
                .replace("Россия. FONBET Кубок России", "Кубок России")
                .replace("Товарищеские матчи (клубы)", "Товарищеский")
            )
            lines.append(f"• {dt_fmt} — Спартак vs {m['opponent']} ({tournament})")
    else:
        lines.append("🏠 *Домашние:* нет запланированных")

    lines.append("")

    if away_matches:
        lines.append("🚌 *Выездные:*")
        for m in away_matches[:8]:  # cap at 8
            dt_fmt = m["dt_msk"].strftime("%d.%m %H:%M")
            tournament = m["tournament"]
            tournament = (
                tournament
                .replace("Россия. Премьер-лига", "РПЛ")
                .replace("Россия. FONBET Кубок России", "Кубок России")
                .replace("Товарищеские матчи (клубы)", "Товарищеский")
            )
            lines.append(f"• {dt_fmt} — {m['opponent']} vs Спартак ({tournament})")
    else:
        lines.append("🚌 *Выездные:* нет запланированных")

    lines.append("\n📅 Следующий отчёт через 2 недели")

    message = "\n".join(lines)

    if send_telegram:
        token = os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("TG_BOT_TOKEN", "")
        owner_id = os.environ.get("OURO_OWNER_ID", "63675289")
        if ctx and ctx.current_chat_id:
            chat_id = int(ctx.current_chat_id)
        else:
            try:
                chat_id = int(owner_id)
            except (ValueError, TypeError):
                chat_id = 63675289

        if token:
            try:
                url = f"https://api.telegram.org/bot{token}/sendMessage"
                resp = requests.post(
                    url,
                    json={
                        "chat_id": chat_id,
                        "text": message,
                        "parse_mode": "Markdown",
                    },
                    timeout=15,
                )
                resp.raise_for_status()
                return f"SENT: {message}"
            except Exception as e:
                log.warning("Telegram send failed: %s", e)
                return message + f"\n⚠️ Telegram send failed: {e}"

    return message


def _check_spartak_matches_handler(ctx: ToolContext, send_telegram: bool = True) -> str:
    return check_spartak_matches(ctx=ctx, send_telegram=send_telegram)


def get_tools() -> List[ToolEntry]:
    return [
        ToolEntry(
            name="check_spartak_matches",
            schema={
                "name": "check_spartak_matches",
                "description": (
                    "Fetch upcoming Spartak Moscow football matches from sports.ru and format a schedule message. "
                    "Shows home and away matches separately. By default sends the message to Telegram. "
                    "Returns 'SENT: ...' when message was delivered — do NOT repeat content to user in that case."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "send_telegram": {
                            "type": "boolean",
                            "description": "Send the schedule to Telegram (default: true)",
                        },
                    },
                    "required": [],
                },
            },
            handler=_check_spartak_matches_handler,
            timeout_sec=60,
        ),
    ]
