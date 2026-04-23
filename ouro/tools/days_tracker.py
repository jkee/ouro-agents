"""
Days Tracker — visa/tax day counter for Russia and Schengen.

Tracks:
- Russia: days in calendar year (limit 183 for tax residency)
- Schengen: days in rolling 180-day window (limit 90)
"""

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from ouro.tools.registry import ToolEntry

FLIGHTS_PATH = Path("/data/flights.json")
CONFIG_PATH = Path("/data/country_days.json")

# Airport → ISO country code
AIRPORT_COUNTRY = {
    "SVO": "RU", "DME": "RU", "VKO": "RU", "AER": "RU",
    "IST": "TR", "SAW": "TR",
    "LHR": "GB", "LGW": "GB", "STN": "GB",
    "DUB": "IE",
    "LIS": "PT",
    "AMS": "NL",
    "BEG": "RS",
    "GVA": "CH",  # Geneva = Switzerland = Schengen
    "TBS": "GE",  # Tbilisi = Georgia (not Schengen)
    "CDG": "FR", "ORY": "FR",
    "FRA": "DE", "MUC": "DE", "TXL": "DE", "BER": "DE",
    "BCN": "ES", "MAD": "ES",
    "FCO": "IT", "MXP": "IT",
    "VIE": "AT",
    "BRU": "BE",
    "ZRH": "CH",
    "ARN": "SE",
    "CPH": "DK",
    "HEL": "FI",
    "OSL": "NO",
    "WAW": "PL",
    "PRG": "CZ",
    "BUD": "HU",
    "ATH": "GR",
}

DEFAULT_SCHENGEN = {
    "AT","BE","CZ","DK","EE","FI","FR","DE","GR","HU",
    "IS","IT","LV","LI","LT","LU","MT","NL","NO","PL",
    "PT","SK","SI","ES","SE","CH"
}


def _extract_iata(s: str) -> str:
    """Extract IATA airport code from strings like 'Dublin (DUB) Terminal 2'."""
    import re
    m = re.search(r'\(([A-Z]{3})\)', s)
    return m.group(1) if m else s.strip()


def _load_config() -> dict:
    """Load config from /data/country_days.json, returning defaults if missing."""
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text())
    return {
        "russia_start_date": "2026-01-01",
        "russia_days_before_flights": 92,
        "manual_stays": [],
        "schengen_countries": sorted(DEFAULT_SCHENGEN),
    }


def _save_config(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))


def _load_flights() -> list:
    """Load flights from /data/flights.json. Returns [] if missing."""
    if not FLIGHTS_PATH.exists():
        return []
    data = json.loads(FLIGHTS_PATH.read_text())
    return [f for f in data if f.get("route") and f.get("departure")]


def _parse_date(s: str) -> date:
    return date.fromisoformat(s[:10])


def _country_zone(country: str, schengen: set) -> str:
    """Return 'RU', 'SCHENGEN', or 'OTHER'."""
    if country == "RU":
        return "RU"
    if country in schengen:
        return "SCHENGEN"
    return "OTHER"


def _build_timeline(flights: list, cfg: dict) -> list[tuple[date, date, str]]:
    """
    Build list of (from_date, to_date_exclusive, country_code) segments
    by parsing flight departures/arrivals chronologically.

    Returns segments sorted by start date.
    """
    # Collect flight events: (date, from_airport, to_airport)
    events = []
    for f in flights:
        if not f.get("departure") or not f.get("route"):
            continue
        dep_date = _parse_date(f["departure"])
        route = f["route"]
        # Handle multi-segment flights (segments list)
        if "segments" in f:
            for seg in f["segments"]:
                sd = _parse_date(seg["departure"])
                fa = _extract_iata(seg["route"]["from"])
                ta = _extract_iata(seg["route"]["to"])
                events.append((sd, fa, ta))
        else:
            fa = _extract_iata(route["from"])
            ta = _extract_iata(route["to"])
            events.append((dep_date, fa, ta))

    # Sort by date
    events.sort(key=lambda x: x[0])

    # Deduplicate: keep only events that represent actual country changes
    # Start from configured starting point
    russia_start = _parse_date(cfg.get("russia_start_date", "2026-01-01"))
    russia_pre_days = cfg.get("russia_days_before_flights", 92)

    segments = []

    # Seed: Russia from year start until first flight
    if events:
        first_flight_date = events[0][0]
        if first_flight_date > russia_start:
            segments.append((russia_start, first_flight_date, "RU"))

    # Now walk through flights
    current_country = "RU"  # assume started in Russia
    current_from = russia_start

    # Track position after each flight
    pos = russia_start
    i = 0
    n = len(events)

    # We need to track where we ARE after each flight leg
    # Each event is a departure: we leave origin, arrive at destination
    location_history = []  # (date_arrived, country)
    location_history.append((russia_start, "RU"))

    for (dep_date, from_iata, to_iata) in events:
        dest_country = AIRPORT_COUNTRY.get(to_iata, "OTHER")
        location_history.append((dep_date, dest_country))

    # Now convert location_history into date segments
    segments = []
    for idx, (arr_date, country) in enumerate(location_history):
        if idx + 1 < len(location_history):
            next_arr_date = location_history[idx + 1][0]
            end_date = next_arr_date  # exclusive
        else:
            end_date = None  # ongoing

        if end_date is None or arr_date < end_date:
            segments.append((arr_date, end_date, country))

    # Add manual stays from config
    for stay in cfg.get("manual_stays", []):
        fc = stay["country_code"].upper()
        fd = _parse_date(stay["from_date"])
        td = _parse_date(stay["to_date"])
        segments.append((fd, td, fc))

    return segments


def _count_russia_days(segments: list, year: int, cfg: dict) -> int:
    """Count days in Russia for a given calendar year."""
    year_start = date(year, 1, 1)
    year_end = date(year, 12, 31)
    today = date.today()
    total = 0

    # Add pre-flight days from config (already counted as Russia in segments, but
    # the segments include the seeded Russia block from russia_start_date)
    for (fd, td, country) in segments:
        if country != "RU":
            continue
        seg_end = td if td is not None else today
        # Clamp to year
        start = max(fd, year_start)
        end = min(seg_end, year_end + timedelta(days=1))
        if start < end:
            total += (end - start).days

    return total


def _count_schengen_days(segments: list, schengen: set, window_days: int = 180) -> int:
    """Count Schengen days in the last `window_days` rolling window from today."""
    today = date.today()
    window_start = today - timedelta(days=window_days - 1)
    total = 0

    for (fd, td, country) in segments:
        if country not in schengen:
            continue
        seg_end = td if td is not None else today
        start = max(fd, window_start)
        end = min(seg_end, today + timedelta(days=1))
        if start < end:
            total += (end - start).days

    return total


def _current_country(segments: list) -> str:
    """Return current country code based on latest segment."""
    today = date.today()
    # Find segment where end is None or end > today
    current = "UNKNOWN"
    for (fd, td, country) in segments:
        if fd <= today and (td is None or td > today):
            current = country
    return current


def days_tracker_status() -> str:
    """
    Return full status: Russia days, Schengen days, current location, warnings.
    Formatted as clean Russian text for Telegram.
    """
    cfg = _load_config()
    flights = _load_flights()
    schengen = set(cfg.get("schengen_countries", sorted(DEFAULT_SCHENGEN)))

    segments = _build_timeline(flights, cfg)
    today = date.today()
    year = today.year

    russia_days = _count_russia_days(segments, year, cfg)
    russia_limit = 183
    russia_remaining = russia_limit - russia_days - 1  # stay under 183
    russia_warn = russia_remaining < 30

    schengen_days = _count_schengen_days(segments, schengen)
    schengen_limit = 90
    schengen_remaining = schengen_limit - schengen_days
    schengen_warn = schengen_remaining < 15

    current = _current_country(segments)
    current_label = {
        "RU": "🇷🇺 Россия",
        "GB": "🇬🇧 Великобритания",
        "IE": "🇮🇪 Ирландия",
        "PT": "🇵🇹 Португалия (Шенген)",
        "NL": "🇳🇱 Нидерланды (Шенген)",
        "TR": "🇹🇷 Турция",
        "RS": "🇷🇸 Сербия",
        "GE": "🇬🇪 Грузия",
        "CH": "🇨🇭 Швейцария (Шенген)",
    }.get(current, f"🌍 {current}")

    lines = [
        f"📍 Сейчас: {current_label}",
        f"📅 Сегодня: {today.strftime('%d.%m.%Y')}",
        "",
        "🇷🇺 **Россия (налоговое резидентство 2026)**",
        f"  Дней в России: {russia_days} / 183",
        f"  {'⚠️' if russia_warn else '✅'} Осталось безопасных дней: {russia_remaining}",
        f"  {'⚠️ ВНИМАНИЕ: < 30 дней до налогового резидентства!' if russia_warn else ''}",
        "",
        "🇪🇺 **Шенген (правило 90/180)**",
        f"  Дней в Шенгене (последние 180д): {schengen_days} / 90",
        f"  {'⚠️' if schengen_warn else '✅'} Осталось: {schengen_remaining} дней",
        f"  {'⚠️ ВНИМАНИЕ: < 15 дней остатка по Шенгену!' if schengen_warn else ''}",
    ]

    # Remove empty warning lines
    lines = [l for l in lines if l != "  "]
    # Strip trailing empty warning lines
    cleaned = []
    for l in lines:
        if l.strip():
            cleaned.append(l)
        else:
            cleaned.append(l)

    return "\n".join(lines).strip()


def days_tracker_add_stay(
    country_code: str,
    from_date: str,
    to_date: str,
    note: str = "",
) -> str:
    """
    Manually add a stay in a country.

    Args:
        country_code: ISO 2-letter code (e.g. "PT", "RU", "GB")
        from_date: Start date YYYY-MM-DD (inclusive)
        to_date: End date YYYY-MM-DD (exclusive)
        note: Optional description

    Returns:
        Confirmation string.
    """
    cfg = _load_config()
    stay = {
        "country_code": country_code.upper(),
        "from_date": from_date,
        "to_date": to_date,
    }
    if note:
        stay["note"] = note
    cfg.setdefault("manual_stays", []).append(stay)
    _save_config(cfg)
    return f"✅ Добавлено пребывание: {country_code.upper()} с {from_date} по {to_date}"


def get_tools() -> list:
    return [
        ToolEntry(
            name="days_tracker_status",
            schema={
                "name": "days_tracker_status",
                "description": (
                    "Show how many days the user has spent in Russia (2026 calendar year) "
                    "and in Schengen (last 180-day window), with remaining days and warnings. "
                    "Use when asked: сколько дней в России, остаток по Шенгену, визовый статус, etc."
                ),
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
            handler=lambda ctx, **kw: days_tracker_status(**kw),
            timeout_sec=30,
        ),
        ToolEntry(
            name="days_tracker_add_stay",
            schema={
                "name": "days_tracker_add_stay",
                "description": "Manually add a country stay to the tracker (for trips not in flights.json).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "country_code": {
                            "type": "string",
                            "description": "ISO 2-letter country code, e.g. PT, RU, GB",
                        },
                        "from_date": {
                            "type": "string",
                            "description": "Start date YYYY-MM-DD (inclusive)",
                        },
                        "to_date": {
                            "type": "string",
                            "description": "End date YYYY-MM-DD (exclusive, day of departure)",
                        },
                        "note": {
                            "type": "string",
                            "description": "Optional description",
                        },
                    },
                    "required": ["country_code", "from_date", "to_date"],
                },
            },
            handler=lambda ctx, **kw: days_tracker_add_stay(**kw),
            timeout_sec=30,
        ),
    ]
