"""
Gmail flight scanner — hourly dedup scan for flight/hotel/train bookings.

SILENT: no progress messages, no reasoning output, no notifications unless
a new booking was actually found and appended. Designed to run as a cron task.
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
from datetime import datetime, timezone
from typing import Any, Optional

from ouro.tools.registry import ToolContext, ToolEntry
from ouro.tools.email_parser import _parse_email_dates
from ouro.tools.control import _send_owner_message
from ouro.utils import read_text, write_text

log = logging.getLogger(__name__)

DATA_DIR = pathlib.Path("/data")
FLIGHTS_JSON = DATA_DIR / "flights.json"
FLIGHTS_MD = DATA_DIR / "flights.md"

GMAIL_QUERY = (
    "(flight OR билет OR booking OR eticket OR itinerary OR посадочный OR boarding "
    "OR hotel OR гостиница OR reservation OR check-in) newer_than:3h"
)


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

def _load_flights() -> list[dict]:
    try:
        data = json.loads(read_text(FLIGHTS_JSON))
        return data if isinstance(data, list) else []
    except FileNotFoundError:
        return []
    except Exception:
        return []


def _save_flights(records: list[dict]) -> None:
    write_text(FLIGHTS_JSON, json.dumps(records, ensure_ascii=False, indent=2))


def _save_markdown(records: list[dict]) -> None:
    lines = ["# Flight & Hotel Bookings\n"]
    if not records:
        lines.append("_No bookings recorded yet._\n")
    else:
        flights = [r for r in records if r.get("type") == "flight"]
        hotels = [r for r in records if r.get("type") == "hotel"]
        trains = [r for r in records if r.get("type") == "train"]
        others = [r for r in records if r.get("type") not in ("flight", "hotel", "train")]

        def _section(title: str, items: list[dict]) -> None:
            if not items:
                return
            lines.append(f"## {title}\n")
            for r in items:
                lines.append(_format_record(r))
                lines.append("")

        _section("Flights", flights)
        _section("Hotels", hotels)
        _section("Trains", trains)
        _section("Other", others)

    write_text(FLIGHTS_MD, "\n".join(lines))


def _format_record(r: dict) -> str:
    t = r.get("type", "?")
    name = r.get("airline") or r.get("hotel_name") or r.get("carrier") or "?"
    route = r.get("route") or r.get("location") or "?"
    ref = r.get("booking_reference") or r.get("pnr") or ""
    passengers = r.get("passengers") or r.get("guests") or []
    if isinstance(passengers, list):
        pax = ", ".join(passengers) if passengers else "?"
    else:
        pax = str(passengers)

    if t == "flight":
        dep = r.get("departure") or "?"
        arr = r.get("arrival") or "?"
        ref_str = f" · Ref: {ref}" if ref else ""
        return f"**{name}** {route} · Dep: {dep} · Arr: {arr} · Pax: {pax}{ref_str}"
    elif t == "hotel":
        ci = r.get("checkin") or "?"
        co = r.get("checkout") or "?"
        ref_str = f" · Ref: {ref}" if ref else ""
        return f"**{name}** {route} · Check-in: {ci} · Check-out: {co} · Guests: {pax}{ref_str}"
    elif t == "train":
        dep = r.get("departure") or "?"
        arr = r.get("arrival") or "?"
        ref_str = f" · Ref: {ref}" if ref else ""
        return f"**{name}** {route} · Dep: {dep} · Arr: {arr} · Pax: {pax}{ref_str}"
    else:
        ref_str = f" · Ref: {ref}" if ref else ""
        return f"**{name}** {route}{ref_str}"


# ---------------------------------------------------------------------------
# Gmail fetch via Composio
# ---------------------------------------------------------------------------

def _fetch_emails_via_composio() -> list[dict]:
    """Call GMAIL_FETCH_EMAILS via Composio. Returns list of email dicts."""
    try:
        from composio import ComposioToolSet, Action
        api_key = os.environ.get("COMPOSIO_API_KEY", "")
        if not api_key:
            log.warning("gmail_flight_scanner: COMPOSIO_API_KEY not set")
            return []
        toolset = ComposioToolSet(api_key=api_key)
        import random as _rand
        import time as _time

        result = None
        last_exc = None
        for attempt in range(1, 4):
            try:
                result = toolset.execute_action(
                    action=Action("GMAIL_FETCH_EMAILS"),
                    params={"query": GMAIL_QUERY, "max_results": 50},
                    entity_id="default",
                )
                break
            except Exception as exc:
                last_exc = exc
                exc_str = str(exc).lower()
                # Don't retry auth errors
                if any(k in exc_str for k in ("auth", "unauthorized", "403", "invalid api")):
                    raise
                if attempt < 3:
                    delay = (2 ** attempt) * (0.8 + _rand.random() * 0.4)
                    log.warning("gmail_flight_scanner: composio attempt %d/3 failed: %s — retrying in %.1fs", attempt, exc, delay)
                    _time.sleep(delay)
                else:
                    raise last_exc
        if isinstance(result, dict):
            # Check result["data"] first — Composio wraps payload there
            data_val = result.get("data")
            if isinstance(data_val, list):
                return data_val
            if isinstance(data_val, dict):
                for key in ("messages", "emails", "data"):
                    val = data_val.get(key)
                    if isinstance(val, list):
                        return val
            # Fall back to other top-level keys
            for key in ("messages", "emails", "result"):
                val = result.get(key)
                if isinstance(val, list):
                    return val
        log.debug(
            "gmail_flight_scanner: unexpected response shape — top-level keys: %s, data keys: %s",
            list(result.keys()) if isinstance(result, dict) else type(result),
            list(result["data"].keys()) if isinstance(result, dict) and isinstance(result.get("data"), dict) else None,
        )
        return []
    except Exception as exc:
        log.warning("gmail_flight_scanner: fetch error: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Email parsing
# ---------------------------------------------------------------------------

def _get_body(email: dict) -> str:
    """Extract best available body text from an email dict."""
    for key in ("body", "html_body", "text_body", "messageText", "snippet", "htmlBody", "textBody", "plain"):
        val = email.get(key)
        if val and isinstance(val, str) and len(val) > 20:
            return val
    # Try nested payload
    payload = email.get("payload") or email.get("messagePayload") or {}
    if isinstance(payload, dict):
        for key in ("body", "html", "text"):
            val = payload.get(key)
            if val and isinstance(val, str):
                return val
    return ""


def _classify_type(email: dict, body: str) -> str:
    """Heuristic type detection from subject + body."""
    text = ((email.get("subject") or "") + " " + body).lower()
    if any(k in text for k in ("flight", "авиа", "авиабилет", "boarding", "eticket", "itinerary", "посадочный")):
        return "flight"
    if any(k in text for k in ("hotel", "гостиница", "check-in", "check-out", "checkin", "checkout", "ночь")):
        return "hotel"
    if any(k in text for k in ("train", "поезд", "жд", "ж/д", "rzd", "ржд")):
        return "train"
    return "other"


def _extract_passengers(body: str) -> list[str]:
    """Very simple name extraction — looks for common label patterns."""
    import re
    names: list[str] = []
    # Patterns like "Passenger: John Doe" / "Пассажир: Иван Иванов" / "Guest: ..."
    pax_re = re.compile(
        r"(?:passenger|пассажир|guest|гость|travell?er|name)[:\s]+([A-ZА-ЯЁ][a-zа-яё]+(?:\s+[A-ZА-ЯЁ][a-zа-яё]+){1,3})",
        re.IGNORECASE,
    )
    for m in pax_re.finditer(body):
        name = m.group(1).strip()
        if name not in names:
            names.append(name)
    return names[:10]  # cap at 10


def _extract_pnr(body: str) -> Optional[str]:
    """Extract PNR/booking reference."""
    import re
    # Common patterns: "PNR: ABC123", "Booking ref: XY12345", "Reference: ..."
    pnr_re = re.compile(
        r"(?:pnr|booking\s*(?:ref(?:erence)?|code|number)|reservation\s*(?:number|code)|бронь|номер\s*бронирования)[:\s#]*([A-Z0-9]{5,10})",
        re.IGNORECASE,
    )
    m = pnr_re.search(body)
    return m.group(1).strip() if m else None


def _extract_route_or_location(email: dict, body: str, booking_type: str) -> str:
    """Extract route (A → B) for flights/trains or location/city for hotels."""
    import re
    subject = email.get("subject") or ""

    if booking_type == "hotel":
        # Try to find city from subject or common body patterns
        city_re = re.compile(
            r"(?:in|в|at|г\.)\s+([A-ZА-ЯЁ][a-zа-яёA-ZА-ЯЁ\s\-]{2,30})(?:[,\.]|$)",
            re.IGNORECASE,
        )
        m = city_re.search(subject) or city_re.search(body[:500])
        return m.group(1).strip() if m else ""

    # Flight/train: look for IATA codes or city pairs
    # Collect all pairs to support multi-segment (Trip.com) itineraries
    iata_re = re.compile(r"\b([A-Z]{3})\s*[-→–]\s*([A-Z]{3})\b")
    all_pairs = iata_re.findall(subject + " " + body[:1000])
    if all_pairs:
        # Build a chain: A→B, B→C becomes A→B→C (deduplicating the join point)
        segments: list[str] = [all_pairs[0][0]]
        for _, dst in all_pairs:
            if dst != segments[-1]:
                segments.append(dst)
        return " → ".join(segments)

    # City names separated by arrow or dash in subject
    route_re = re.compile(r"([A-ZА-ЯЁ][a-zа-яё]{2,})\s*[-→–—]\s*([A-ZА-ЯЁ][a-zа-яё]{2,})")
    m = route_re.search(subject)
    if m:
        return f"{m.group(1)} → {m.group(2)}"

    return ""


def _extract_airline_or_hotel(email: dict, body: str, booking_type: str) -> str:
    """Best-effort airline/hotel name from sender or subject."""
    sender = email.get("from") or email.get("sender") or email.get("From") or ""
    subject = email.get("subject") or ""

    # Strip email address, keep display name
    import re
    m = re.match(r"^(.+?)\s*<", sender)
    display_name = m.group(1).strip() if m else sender.split("<")[0].strip()

    if display_name and len(display_name) > 2:
        return display_name
    # Fall back to first word(s) of subject
    words = subject.split()
    return " ".join(words[:3]) if words else "Unknown"


def _parse_email(email: dict) -> dict:
    """Parse a single email into a booking record."""
    body = _get_body(email)
    subject = email.get("subject") or ""
    message_id = email.get("messageId") or email.get("id") or email.get("message_id") or ""

    # Date extraction — ONLY from parse_email_dates, body only (no subject)
    # Dates not returned by parse_email_dates are recorded as null — no inference.
    date_result = _parse_email_dates(body)
    departure = date_result.get("departure")
    arrival = date_result.get("arrival")
    checkin = date_result.get("checkin")
    checkout = date_result.get("checkout")
    parse_warnings: list[str] = date_result.get("warnings") or []

    booking_type = _classify_type(email, body)
    passengers = _extract_passengers(body)
    pnr = _extract_pnr(body)
    route = _extract_route_or_location(email, body, booking_type)
    name = _extract_airline_or_hotel(email, body, booking_type)

    # Date parse validation
    if booking_type in ("flight", "train"):
        date_parse_ok = departure is not None
        if not date_parse_ok:
            parse_warnings.append("departure date not found")
    elif booking_type == "hotel":
        date_parse_ok = checkin is not None
        if not date_parse_ok:
            parse_warnings.append("checkin date not found")
    else:
        date_parse_ok = True

    record: dict[str, Any] = {
        "messageId": message_id,
        "type": booking_type,
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "subject": subject,
        "parse_warnings": parse_warnings,
        "date_parse_ok": date_parse_ok,
    }

    if booking_type == "flight":
        record["airline"] = name
        record["route"] = route
        record["departure"] = departure
        record["arrival"] = arrival
        record["passengers"] = passengers
        record["booking_reference"] = pnr
    elif booking_type == "hotel":
        record["hotel_name"] = name
        record["location"] = route
        record["checkin"] = checkin
        record["checkout"] = checkout
        record["guests"] = passengers
        record["booking_reference"] = pnr
    elif booking_type == "train":
        record["carrier"] = name
        record["route"] = route
        record["departure"] = departure
        record["arrival"] = arrival
        record["passengers"] = passengers
        record["booking_reference"] = pnr
    else:
        record["airline"] = name
        record["route"] = route
        record["departure"] = departure
        record["arrival"] = arrival
        record["passengers"] = passengers
        record["booking_reference"] = pnr

    return record


# ---------------------------------------------------------------------------
# Notification helper
# ---------------------------------------------------------------------------

def _format_notification(record: dict) -> str:
    t = record.get("type", "other")
    icons = {"flight": "✈️", "hotel": "🏨", "train": "🚆", "other": "📋"}
    icon = icons.get(t, "📋")
    name = record.get("airline") or record.get("hotel_name") or record.get("carrier") or "Unknown"
    route = record.get("route") or record.get("location") or ""

    if t == "flight":
        dep = record.get("departure") or "?"
        arr = record.get("arrival") or "?"
        return f"{icon} New flight booking found: {name} {route} {dep} - {arr}"
    elif t == "hotel":
        ci = record.get("checkin") or "?"
        co = record.get("checkout") or "?"
        return f"{icon} New hotel booking found: {name} {route} {ci} - {co}"
    elif t == "train":
        dep = record.get("departure") or "?"
        arr = record.get("arrival") or "?"
        return f"{icon} New train booking found: {name} {route} {dep} - {arr}"
    else:
        return f"{icon} New booking found: {name} {route}"


# ---------------------------------------------------------------------------
# Main scan logic
# ---------------------------------------------------------------------------

def scan_gmail_flights(ctx: Optional["ToolContext"] = None) -> str:
    """
    Hourly dedup scan of Gmail for flight/hotel/train bookings.
    SILENT: no progress messages, no output. Only calls send_owner_message
    if new bookings were found. Always returns empty string.
    """
    # STEP 1: Load existing records + build dedup set
    existing_records = _load_flights()
    existing_ids: set[str] = {r["messageId"] for r in existing_records if r.get("messageId")}

    # STEP 2: Fetch emails from Gmail
    emails = _fetch_emails_via_composio()
    if not emails:
        return ""

    # STEP 3 + 4: Dedup + parse new emails only
    new_records: list[dict] = []
    for email in emails:
        msg_id = email.get("messageId") or email.get("id") or email.get("message_id") or ""
        if msg_id and msg_id in existing_ids:
            continue  # already seen — skip entirely
        record = _parse_email(email)
        if not record.get("messageId"):
            continue  # can't track without ID — skip
        new_records.append(record)

    if not new_records:
        return ""

    # STEP 5: Append new records + regenerate markdown
    all_records = existing_records + new_records
    _save_flights(all_records)
    _save_markdown(all_records)

    # STEP 6: Send ONE consolidated notification if new bookings found
    if ctx is not None:
        lines = [_format_notification(r) for r in new_records]
        msg = "\n".join(lines)
        _send_owner_message(ctx, msg, reason="new booking detected")

    return ""


# ---------------------------------------------------------------------------
# Tool handler
# ---------------------------------------------------------------------------

def _scan_gmail_flights_handler(ctx: ToolContext) -> str:
    return scan_gmail_flights(ctx)


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------

def get_tools() -> list[ToolEntry]:
    return [
        ToolEntry(
            name="scan_gmail_flights",
            schema={
                "name": "scan_gmail_flights",
                "description": (
                    "SILENT hourly Gmail dedup scan for flight, hotel, and train bookings. "
                    "Fetches emails matching travel keywords from the last 3 hours, "
                    "skips already-seen messageIds, parses new ones using parse_email_dates "
                    "for all date fields, appends to /data/flights.json, regenerates "
                    "/data/flights.md, and calls send_owner_message only if new bookings "
                    "were found. Produces NO output, NO progress messages, NO reasoning "
                    "unless a new booking was appended. Run via cron every hour."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
            handler=_scan_gmail_flights_handler,
            timeout_sec=120,
        ),
    ]
