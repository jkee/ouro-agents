"""
Email date parser — deterministic regex extraction of dates from email bodies.

No LLM inference. Only returns dates literally present in the email content.
Stdlib only: re, json, html.
"""

from __future__ import annotations

import html
import json
import re
from typing import Optional

from ouro.tools.registry import ToolEntry

# ---------------------------------------------------------------------------
# HTML stripping + entity normalization
# ---------------------------------------------------------------------------

_HTML_TAG_RE = re.compile(r"<[^>]+>", re.DOTALL)
_ENTITY_MAP = {
    "&ndash;": "-",
    "&mdash;": "-",
    "&amp;": "&",
    "&nbsp;": " ",
    "&lt;": "<",
    "&gt;": ">",
    "&quot;": '"',
    "&#8211;": "-",
    "&#8212;": "-",
    "&#160;": " ",
}
_ENTITY_RE = re.compile("|".join(re.escape(k) for k in _ENTITY_MAP), re.IGNORECASE)


def _clean(text: str) -> str:
    """Strip HTML tags, decode entities, collapse whitespace."""
    # Named entities first (case-insensitive)
    text = _ENTITY_RE.sub(lambda m: _ENTITY_MAP[m.group(0).lower()], text)
    # Strip tags
    text = _HTML_TAG_RE.sub(" ", text)
    # Decode remaining HTML entities (numeric etc.)
    text = html.unescape(text)
    # Collapse runs of whitespace to a single space (preserve newlines for label scanning)
    text = re.sub(r"[^\S\n]+", " ", text)
    return text


# ---------------------------------------------------------------------------
# Month name → number
# ---------------------------------------------------------------------------

MONTHS: dict[str, int] = {
    "january": 1, "jan": 1,
    "february": 2, "feb": 2,
    "march": 3, "mar": 3,
    "april": 4, "apr": 4,
    "may": 5,
    "june": 6, "jun": 6,
    "july": 7, "jul": 7,
    "august": 8, "aug": 8,
    "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10,
    "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}

_MONTH_NAMES = r"(?:January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)"
_WEEKDAY = r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|Mon|Tue|Wed|Thu|Fri|Sat|Sun)"

# ---------------------------------------------------------------------------
# Date patterns (most-specific to least-specific)
# Each pattern must have named groups: year, month (name or number), day
# ---------------------------------------------------------------------------

# "Thursday, May 28, 2026" or "Thu, May 28, 2026"
_PAT_WEEKDAY_MONTH_NAME = re.compile(
    rf"\b{_WEEKDAY},\s+({_MONTH_NAMES})\s+(\d{{1,2}}),\s+(\d{{4}})\b",
    re.IGNORECASE,
)

# "May 28, 2026"
_PAT_MONTH_NAME_DAY_YEAR = re.compile(
    rf"\b({_MONTH_NAMES})\s+(\d{{1,2}}),\s+(\d{{4}})\b",
    re.IGNORECASE,
)

# "28 May 2026"
_PAT_DAY_MONTH_NAME_YEAR = re.compile(
    rf"\b(\d{{1,2}})\s+({_MONTH_NAMES})\s+(\d{{4}})\b",
    re.IGNORECASE,
)

# "2026-05-28" ISO
_PAT_ISO = re.compile(r"\b(\d{4})-(0[1-9]|1[0-2])-(\d{2})\b")

# "28/05/2026" or "05/28/2026" — ambiguous; we treat DD/MM/YYYY when day>12, else MM/DD/YYYY
_PAT_SLASH = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")


def _month_num(name: str) -> Optional[int]:
    return MONTHS.get(name.lower())


def _to_iso(year: int, month: int, day: int) -> Optional[str]:
    if not (1 <= month <= 12 and 1 <= day <= 31 and 1900 <= year <= 2100):
        return None
    return f"{year:04d}-{month:02d}-{day:02d}"


def _parse_all_dates(text: str) -> list[str]:
    """Return a deduplicated list of all ISO dates found in text, in order of first appearance."""
    found: list[str] = []
    seen: set[str] = set()

    def _add(iso: Optional[str]) -> None:
        if iso and iso not in seen:
            seen.add(iso)
            found.append(iso)

    # Track positions already consumed so we don't double-count
    consumed: set[int] = set()

    def _try_add(m: re.Match, iso: Optional[str]) -> None:
        if iso and m.start() not in consumed:
            for i in range(m.start(), m.end()):
                consumed.add(i)
            _add(iso)

    # Weekday + month name: "Thu, May 28, 2026"
    for m in _PAT_WEEKDAY_MONTH_NAME.finditer(text):
        month_name, day_s, year_s = m.group(1), m.group(2), m.group(3)
        mn = _month_num(month_name)
        if mn:
            _try_add(m, _to_iso(int(year_s), mn, int(day_s)))

    # Month name + day: "May 28, 2026"
    for m in _PAT_MONTH_NAME_DAY_YEAR.finditer(text):
        month_name, day_s, year_s = m.group(1), m.group(2), m.group(3)
        mn = _month_num(month_name)
        if mn:
            _try_add(m, _to_iso(int(year_s), mn, int(day_s)))

    # Day + month name: "28 May 2026"
    for m in _PAT_DAY_MONTH_NAME_YEAR.finditer(text):
        day_s, month_name, year_s = m.group(1), m.group(2), m.group(3)
        mn = _month_num(month_name)
        if mn:
            _try_add(m, _to_iso(int(year_s), mn, int(day_s)))

    # ISO: "2026-05-28"
    for m in _PAT_ISO.finditer(text):
        year_s, month_s, day_s = m.group(1), m.group(2), m.group(3)
        _try_add(m, _to_iso(int(year_s), int(month_s), int(day_s)))

    # Slash: "28/05/2026" or "05/28/2026"
    for m in _PAT_SLASH.finditer(text):
        a, b, year_s = int(m.group(1)), int(m.group(2)), int(m.group(3))
        # Heuristic: if first part >12 it must be DD/MM; if second part >12 it must be MM/DD
        if a > 12:
            iso = _to_iso(year_s, b, a)  # DD/MM/YYYY
        elif b > 12:
            iso = _to_iso(year_s, a, b)  # MM/DD/YYYY
        else:
            iso = _to_iso(year_s, b, a)  # default: DD/MM/YYYY
        _try_add(m, iso)

    return found


# ---------------------------------------------------------------------------
# Labeled date extraction
# ---------------------------------------------------------------------------

# Maps label regex → result field name
_LABEL_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"check[\s\-]?in\s*[:：]?\s*", re.IGNORECASE), "checkin"),
    (re.compile(r"check[\s\-]?out\s*[:：]?\s*", re.IGNORECASE), "checkout"),
    (re.compile(r"departure\s*[:：]?\s*", re.IGNORECASE), "departure"),
    (re.compile(r"arrival\s*[:：]?\s*", re.IGNORECASE), "arrival"),
    # "From:" / "To:" only when explicitly labeled (colon required to avoid matching prepositions)
    (re.compile(r"\bfrom\s*[:：]\s*", re.IGNORECASE), "departure"),
    (re.compile(r"\bto\s*[:：]\s*", re.IGNORECASE), "arrival"),
]

# A single date pattern that can follow a label (tries in order)
_DATE_AFTER_LABEL_PATTERNS: list[re.Pattern] = [
    # "Thu, May 28, 2026" / "Thursday, May 28, 2026"
    re.compile(rf"(?:{_WEEKDAY},\s+)?({_MONTH_NAMES})\s+(\d{{1,2}}),\s+(\d{{4}})", re.IGNORECASE),
    # "28 May 2026"
    re.compile(rf"(\d{{1,2}})\s+({_MONTH_NAMES})\s+(\d{{4}})", re.IGNORECASE),
    # ISO
    re.compile(r"(\d{4})-(0[1-9]|1[0-2])-(\d{2})"),
    # Slash
    re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})"),
]


def _extract_date_at(text: str, pos: int) -> Optional[str]:
    """Try to parse a date starting at `pos` in text."""
    snippet = text[pos:pos + 80]

    # "Thu, May 28, 2026" / "May 28, 2026"
    m = re.match(
        rf"(?:{_WEEKDAY},\s+)?({_MONTH_NAMES})\s+(\d{{1,2}}),\s+(\d{{4}})",
        snippet, re.IGNORECASE,
    )
    if m:
        mn = _month_num(m.group(1))
        if mn:
            return _to_iso(int(m.group(3)), mn, int(m.group(2)))

    # "28 May 2026"
    m = re.match(rf"(\d{{1,2}})\s+({_MONTH_NAMES})\s+(\d{{4}})", snippet, re.IGNORECASE)
    if m:
        mn = _month_num(m.group(2))
        if mn:
            return _to_iso(int(m.group(3)), mn, int(m.group(1)))

    # ISO
    m = re.match(r"(\d{4})-(0[1-9]|1[0-2])-(\d{2})", snippet)
    if m:
        return _to_iso(int(m.group(1)), int(m.group(2)), int(m.group(3)))

    # Slash
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", snippet)
    if m:
        a, b, yr = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if a > 12:
            return _to_iso(yr, b, a)
        elif b > 12:
            return _to_iso(yr, a, b)
        else:
            return _to_iso(yr, b, a)

    return None


def _extract_labeled_dates(text: str) -> dict[str, list[str]]:
    """Find label → date associations. Returns dict of field → [iso_dates]."""
    results: dict[str, list[str]] = {
        "checkin": [], "checkout": [], "departure": [], "arrival": [],
    }

    for label_re, field in _LABEL_PATTERNS:
        for lm in label_re.finditer(text):
            iso = _extract_date_at(text, lm.end())
            if iso:
                results[field].append(iso)

    return results


# ---------------------------------------------------------------------------
# Date range extraction: "Thu, May 28 – Sun, May 31, 2026"
# ---------------------------------------------------------------------------

_RANGE_RE = re.compile(
    rf"(?:{_WEEKDAY},?\s+)?({_MONTH_NAMES})\s+(\d{{1,2}})(?:,\s+(\d{{4}}))?"
    r"\s*[-–—]\s*"
    rf"(?:{_WEEKDAY},?\s+)?({_MONTH_NAMES})\s+(\d{{1,2}}),\s+(\d{{4}})",
    re.IGNORECASE,
)


def _extract_ranges(text: str) -> list[tuple[str, str]]:
    """Return list of (start_iso, end_iso) for date ranges found."""
    pairs = []
    for m in _RANGE_RE.finditer(text):
        m1, d1, y1, m2, d2, y2 = m.group(1), m.group(2), m.group(3), m.group(4), m.group(5), m.group(6)
        mn1, mn2 = _month_num(m1), _month_num(m2)
        yr2 = int(y2)
        yr1 = int(y1) if y1 else yr2
        if mn1 and mn2:
            iso1 = _to_iso(yr1, mn1, int(d1))
            iso2 = _to_iso(yr2, mn2, int(d2))
            if iso1 and iso2:
                pairs.append((iso1, iso2))
    return pairs


# ---------------------------------------------------------------------------
# Main implementation
# ---------------------------------------------------------------------------

def _parse_email_dates(content: str, subject: str = "") -> dict:
    warnings: list[str] = []

    text = _clean(content)

    labeled = _extract_labeled_dates(text)
    raw_dates = _parse_all_dates(text)
    ranges = _extract_ranges(text)

    def _first(field: str) -> Optional[str]:
        vals = labeled[field]
        if len(vals) > 1:
            unique = list(dict.fromkeys(vals))
            if len(unique) > 1:
                warnings.append(f"Multiple {field} dates found, using first")
        return vals[0] if vals else None

    checkin = _first("checkin")
    checkout = _first("checkout")
    departure = _first("departure")
    arrival = _first("arrival")

    # If no labeled checkin/checkout found, try to infer from date ranges
    # (only if a single range is present and no labeled dates)
    if ranges and not checkin and not checkout and not departure and not arrival:
        if len(ranges) == 1:
            checkin, checkout = ranges[0]
            warnings.append("Checkin/checkout inferred from date range in body")

    return {
        "checkin": checkin,
        "checkout": checkout,
        "departure": departure,
        "arrival": arrival,
        "raw_dates": raw_dates,
        "source": "body",
        "warnings": warnings,
    }


def _handle_parse_email_dates(args: dict) -> str:
    content = args.get("content", "")
    subject = args.get("subject", "")
    result = _parse_email_dates(content, subject)
    return json.dumps(result, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------

def get_tools() -> list:
    return [
        ToolEntry(
            name="parse_email_dates",
            schema={
                "name": "parse_email_dates",
                "description": (
                    "Extract dates from an email body using deterministic regex parsing. "
                    "Returns checkin, checkout, departure, arrival dates found explicitly in the body text. "
                    "Never infers or assumes dates — only returns what is literally present in the email content."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "content": {
                            "type": "string",
                            "description": "Raw email body content (HTML or plain text)",
                        },
                        "subject": {
                            "type": "string",
                            "description": "Optional email subject line (used only for context, dates are extracted from body only)",
                        },
                    },
                    "required": ["content"],
                },
            },
            handler=lambda ctx, **kw: _handle_parse_email_dates(kw),
            timeout_sec=10,
        ),
    ]
