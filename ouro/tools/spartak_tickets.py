"""Spartak Cup Final tickets monitor.

Monitors sites for Spartak vs Krasnodar Cup Final tickets (May 24, Luzhniki):
- https://superfinal.rfs.ru/ (RFS official superfinal page)
- https://afisha.yandex.ru/ (Yandex Afisha sport events)

Logic: look for REAL ticket sale links, not just keyword mentions in SEO text.
Silent when no tickets found; sends Telegram alert immediately when found.
"""

import json
import re
import logging
import os
from datetime import date
from typing import Optional

import requests
from bs4 import BeautifulSoup

from ouro.tools.registry import ToolEntry, ToolContext

log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "ru-RU,ru;q=0.9",
}

# RFS superfinal page - this is where official ticket sales will appear
RFS_SUPERFINAL_URL = "https://superfinal.rfs.ru/"
# Also check RFS news for ticket sale announcement
RFS_MAIN_URL = "https://m.rfs.ru/"
# Yandex Afisha sport search for Spartak
YANDEX_AFISHA_URL = "https://afisha.yandex.ru/moscow/sport?text=%D1%81%D0%BF%D0%B0%D1%80%D1%82%D0%B0%D0%BA"

# Keywords that indicate a REAL ticket sale (not SEO text or press releases)
TICKET_SALE_KEYWORDS = [
    "купить билет",
    "билеты в продаже",
    "tickets",
    "продажа билетов",
    "от ",  # price pattern like "от 500 ₽"
    "₽",
    "руб.",
]

# These combinations indicate it's the right event
EVENT_KEYWORDS_RU = ["спартак", "краснодар", "24 мая", "суперфинал", "кубок"]
EVENT_KEYWORDS_EN = ["spartak", "krasnodar", "cup final", "may 24"]

# Known ticket vendor domains — presence in an href means a real purchase link
KNOWN_TICKET_VENDORS = re.compile(
    r"ticketland\.ru|tickets\.ru|concert\.ru|fonbet|kassa\.rambler|sport\.rfs\.ru/ticket",
    re.IGNORECASE,
)

# "в продаже NN <month>" — upcoming sale date announcement, not actual sale
FUTURE_SALE_DATE_RE = re.compile(
    r"в\s+продаже\s+(\d{1,2})\s+"
    r"(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)",
    re.IGNORECASE,
)
_MONTH_NUM = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}


def _disable_cron(cron_id: str) -> None:
    """Disable a cron entry in /data/crons.json by id."""
    crons_path = "/data/crons.json"
    try:
        with open(crons_path, "r") as f:
            data = json.load(f)
        for cron in data.get("crons", []):
            if cron.get("id") == cron_id:
                cron["enabled"] = False
                break
        with open(crons_path, "w") as f:
            json.dump(data, f, indent=2)
        log.info(f"spartak_tickets: cron '{cron_id}' disabled in {crons_path}")
    except Exception as e:
        log.warning(f"spartak_tickets: failed to disable cron '{cron_id}': {e}")


def _has_event_keywords(text: str) -> bool:
    """Check if text contains enough event-specific keywords."""
    text_lower = text.lower()
    # Need at least 2 event keywords to confirm it's the right event
    matches = sum(1 for kw in EVENT_KEYWORDS_RU if kw in text_lower)
    return matches >= 2


def _check_rfs_superfinal() -> Optional[dict]:
    """Check RFS superfinal page for ticket info.

    Only returns a result when a real purchase link is present.
    Keyword mentions like "билеты в продаже 14 мая" are NOT sufficient.
    """
    try:
        resp = requests.get(RFS_SUPERFINAL_URL, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            log.debug(f"RFS superfinal: HTTP {resp.status_code}")
            return None

        text = resp.text

        # Future-date filter: "в продаже NN мая" → announced but not yet on sale
        future_match = FUTURE_SALE_DATE_RE.search(text)
        if future_match:
            day = int(future_match.group(1))
            month = _MONTH_NUM.get(future_match.group(2).lower(), 0)
            if month:
                today = date.today()
                try:
                    sale_date = date(today.year, month, day)
                except ValueError:
                    sale_date = None
                if sale_date and sale_date > today:
                    log.info(
                        f"RFS superfinal: sale announced for {day} {future_match.group(2)} "
                        f"(future) — not on sale yet, skipping"
                    )
                    return None

        # Require an actual clickable purchase link — no link, no alert
        soup = BeautifulSoup(text, "html.parser")
        ticket_link = None
        for a in soup.find_all("a", href=True):
            href = a["href"]
            link_text = a.get_text().lower()
            # Vendor domain in href is a strong signal
            if KNOWN_TICKET_VENDORS.search(href):
                ticket_link = href if href.startswith("http") else f"https://superfinal.rfs.ru{href}"
                break
            # Purchase keyword in link text + non-trivial href
            if (any(kw in link_text for kw in ["купить", "билет", "ticket", "buy"])
                    and href not in ("", "#", "/")):
                ticket_link = href if href.startswith("http") else f"https://superfinal.rfs.ru{href}"
                break

        if not ticket_link:
            log.debug("RFS superfinal: page live but no purchase link found")
            return None

        price_match = re.search(r'от\s+(\d[\d\s]*)\s*[₽р]', text)
        price = price_match.group(0) if price_match else None

        return {
            "source": "RFS Superfinal",
            "url": ticket_link,
            "price": price,
            "status": "tickets_found",
        }
    except Exception as e:
        log.warning(f"RFS superfinal check failed: {e}")
        return None


def _check_rfs_news() -> Optional[dict]:
    """Check RFS main page news for ticket sale announcement for Spartak Cup Final."""
    try:
        resp = requests.get(RFS_MAIN_URL, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            return None

        soup = BeautifulSoup(resp.text, "html.parser")

        for a in soup.find_all("a", href=True):
            link_text = a.get_text().lower()
            # Must mention tickets
            if "билет" not in link_text:
                continue
            # Must be about Spartak or Krasnodar
            has_team = "спартак" in link_text or "краснодар" in link_text
            # Must be about the cup final (not futsal, women's, supercup etc.)
            has_final = "финал кубка" in link_text or "суперфинал" in link_text
            # Exclude unrelated events
            is_unrelated = any(w in link_text for w in ["футзал", "женщин", "нижн", "суперкубок", "аккредитаци"])

            if has_team and has_final and not is_unrelated:
                href = a.get("href", "")
                if href:
                    url = href if href.startswith("http") else f"https://www.rfs.ru{href}"
                    return {
                        "source": "РФС (новость о билетах)",
                        "url": url,
                        "price": None,
                        "status": "tickets_found",
                    }

        return None
    except Exception as e:
        log.warning(f"RFS news check failed: {e}")
        return None


def _check_yandex_afisha() -> Optional[dict]:
    """Check Yandex Afisha for Spartak Cup Final ticket listings.

    Searches sport events page and a direct combined query for the specific event.
    Returns result only when an event card matching both 'спартак' and relevant
    context keywords (финал/кубок/24 мая/краснодар) is found.
    """
    MATCH_KEYWORDS = ["финал", "кубок", "24 мая", "краснодар"]
    urls_to_try = [
        YANDEX_AFISHA_URL,
        "https://afisha.yandex.ru/moscow/sport?text=%D1%81%D0%BF%D0%B0%D1%80%D1%82%D0%B0%D0%BA+%D0%BA%D1%80%D0%B0%D1%81%D0%BD%D0%BE%D0%B4%D0%B0%D1%80+%D0%BA%D1%83%D0%B1%D0%BE%D0%BA",
    ]
    try:
        for url in urls_to_try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            if resp.status_code != 200:
                log.debug(f"Yandex Afisha: HTTP {resp.status_code} for {url}")
                continue

            soup = BeautifulSoup(resp.text, "html.parser")

            for card in soup.find_all(["div", "article", "li", "a"],
                                       class_=re.compile(r"event|card|item|tile|poster|afisha")):
                card_text = card.get_text().lower()
                if "спартак" not in card_text:
                    continue
                if not any(kw in card_text for kw in MATCH_KEYWORDS):
                    continue

                price_match = re.search(r'от\s+(\d[\d\s]*)\s*[₽р]', card.get_text())
                price = price_match.group(0) if price_match else None

                link = card if card.name == "a" else card.find("a", href=True)
                found_url = url
                if link and link.get("href"):
                    href = link["href"]
                    found_url = href if href.startswith("http") else f"https://afisha.yandex.ru{href}"

                return {
                    "source": "Яндекс Афиша",
                    "url": found_url,
                    "price": price,
                    "status": "tickets_found",
                }

        return None
    except Exception as e:
        log.warning(f"Yandex Afisha check failed: {e}")
        return None


def check_spartak_tickets(send_telegram: bool = True) -> str:
    """
    Check for Spartak vs Krasnodar Cup Final tickets (May 24, Luzhniki).

    Monitors:
    - superfinal.rfs.ru — official RFS superfinal page
    - m.rfs.ru — RFS news for ticket announcement
    - afisha.yandex.ru — Yandex Afisha sport events

    Returns status string. Sends Telegram alert only when tickets are found.
    Silent (returns 'not_found') when no tickets available.
    """
    # Auto-disable: Cup Final is May 24, 2026; no point checking after that.
    if date.today() > date(2026, 5, 24):
        log.info("spartak_tickets: Cup Final date has passed, disabling cron")
        _disable_cron("spartak-tickets")
        return "expired"

    results = []

    # Check all sources
    rfs_superfinal = _check_rfs_superfinal()
    rfs_news = _check_rfs_news()
    yandex_afisha = _check_yandex_afisha()

    for result in [rfs_superfinal, rfs_news, yandex_afisha]:
        if result and result.get("status") == "tickets_found":
            results.append(result)

    if not results:
        log.debug("spartak_tickets: no tickets found on any source")
        return "not_found"

    # Build notification message
    msg_parts = ["🎟 **Появились билеты на Суперфинал Кубка России!**",
                  "Спартак 🆚 Краснодар | 24 мая | Лужники\n"]

    for r in results:
        line = f"📌 **{r['source']}**: {r['url']}"
        if r.get("price"):
            line += f"\n   💰 {r['price']}"
        msg_parts.append(line)

    msg_parts.append("\n⚡ Покупай через официальный сайт — Кассир не перепродаёт.")
    message = "\n".join(msg_parts)

    found_status = f"found: {[r['source'] for r in results]}"

    if send_telegram:
        try:
            _send_telegram(message)
            log.info("spartak_tickets: tickets found, Telegram notification sent")
        except Exception as e:
            log.error(f"spartak_tickets: Telegram send failed (tickets still found): {e}")

    return found_status


def _send_telegram(message: str) -> None:
    """Send message to owner via Telegram Bot API."""
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    owner_id = os.environ.get("TELEGRAM_OWNER_ID")

    if not bot_token or not owner_id:
        raise ValueError("TELEGRAM_BOT_TOKEN or TELEGRAM_OWNER_ID not set")

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    resp = requests.post(url, json={
        "chat_id": int(owner_id),
        "text": message,
        "parse_mode": "Markdown",
    }, timeout=10)
    resp.raise_for_status()


def _check_spartak_tickets_handler(ctx: ToolContext, send_telegram: bool = True) -> str:
    return check_spartak_tickets(send_telegram=send_telegram)


def get_tools():
    """Auto-discovery: return list of tool definitions."""
    return [
        ToolEntry(
            name="check_spartak_tickets",
            schema={
                "name": "check_spartak_tickets",
                "description": (
                    "Check if tickets for Spartak vs Krasnodar Cup Final (May 24, Luzhniki) "
                    "are available. Monitors superfinal.rfs.ru, m.rfs.ru, and afisha.yandex.ru. "
                    "Returns 'not_found' if no tickets, "
                    "or lists sources if found. Automatically sends Telegram notification when found."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "send_telegram": {
                            "type": "boolean",
                            "description": "Whether to send Telegram notification (default: true)",
                        }
                    },
                    "required": [],
                },
            },
            handler=_check_spartak_tickets_handler,
        )
    ]
