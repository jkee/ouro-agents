"""Social media mirror tools: Instagram profile via public viewer sites (fallback)."""

from __future__ import annotations

import json
import logging
import re
from typing import List, Optional

from ouroboros.tools.registry import ToolContext, ToolEntry

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Individual scrapers (tried in order)
# ---------------------------------------------------------------------------

def _scrape_insta_stories_viewer(username: str) -> Optional[dict]:
    """Scrape insta-stories-viewer.com — works reliably without CAPTCHA."""
    try:
        import requests
        from bs4 import BeautifulSoup
    except ImportError:
        log.warning("requests or beautifulsoup4 not installed")
        return None

    url = f"https://insta-stories-viewer.com/{username}/"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Referer": "https://insta-stories-viewer.com/",
    }

    try:
        resp = requests.get(url, headers=headers, timeout=20)
    except Exception as e:
        log.warning("insta-stories-viewer request failed: %s", e)
        return None

    if resp.status_code != 200:
        log.warning("insta-stories-viewer returned %s", resp.status_code)
        return None

    try:
        from bs4 import BeautifulSoup
    except ImportError:
        log.warning("beautifulsoup4 not installed")
        return None

    soup = BeautifulSoup(resp.text, "html.parser")

    # Check for "not found" — element has style="display: none;" when hidden
    not_found = soup.select_one(".profile__tabs-not-found")
    if not_found:
        style = not_found.get("style", "")
        if "display: none" not in style:
            log.info("insta-stories-viewer: account not found")
            return None

    # Check for private account
    is_private = False
    private_el = soup.select_one(".profile__tabs-is-private")
    if private_el:
        style = private_el.get("style", "")
        if "display: none" not in style:
            is_private = True

    # Extract stats
    posts_el = soup.select_one(".profile__stats-posts")
    followers_el = soup.select_one(".profile__stats-followers")
    following_el = soup.select_one(".profile__stats-follows")
    bio_el = soup.select_one(".profile__description")
    nick_el = soup.select_one(".profile__nickname")

    if not followers_el:
        # Page loaded but no profile block — might be blocked or layout changed
        return None

    # Clean nickname (remove "(Anonymous profile view)" suffix)
    if nick_el:
        full_nick = nick_el.get_text(separator=" ", strip=True)
        nick_clean = re.sub(r"\(.*?\)", "", full_nick).strip()
    else:
        nick_clean = username

    def _text(el) -> str:
        return el.get_text(strip=True) if el else "?"

    return {
        "username": nick_clean or username,
        "followers": _text(followers_el),
        "following": _text(following_el),
        "posts": _text(posts_el),
        "bio": _text(bio_el),
        "is_private": is_private,
        "source": "insta-stories-viewer.com",
    }


def _scrape_imginn(username: str) -> Optional[dict]:
    """Scrape imginn.com — secondary mirror."""
    try:
        import requests
        from bs4 import BeautifulSoup
    except ImportError:
        return None

    url = f"https://imginn.com/{username}/"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }

    try:
        resp = requests.get(url, headers=headers, timeout=20)
    except Exception as e:
        log.warning("imginn request failed: %s", e)
        return None

    if resp.status_code != 200:
        log.warning("imginn returned %s", resp.status_code)
        return None

    soup = BeautifulSoup(resp.text, "html.parser")

    # imginn layout: .counts spans, .desc for bio
    counts = soup.select(".counts span")
    bio_el = soup.select_one(".desc")

    if len(counts) < 3:
        return None

    def _text(el) -> str:
        return el.get_text(strip=True) if el else "?"

    return {
        "username": username,
        "posts": _text(counts[0]),
        "followers": _text(counts[1]),
        "following": _text(counts[2]),
        "bio": _text(bio_el),
        "is_private": False,
        "source": "imginn.com",
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

# Ordered list of scrapers — first success wins
_SCRAPERS = [
    _scrape_insta_stories_viewer,
    _scrape_imginn,
]


def instagram_profile_mirror(username: str) -> Optional[dict]:
    """
    Try multiple public Instagram viewer mirrors to get profile info.
    Returns a dict on success, None if all scrapers fail.
    This is the shared fallback used by both social_mirror tool and social.py.
    """
    username = username.strip().lstrip("@")
    for scraper in _SCRAPERS:
        try:
            result = scraper(username)
            if result:
                log.info("instagram_profile_mirror: success via %s", result.get("source"))
                return result
        except Exception as e:
            log.warning("Scraper %s failed: %s", scraper.__name__, e)
    return None


def _instagram_profile_mirror_tool(ctx: ToolContext, username: str) -> str:
    """Tool handler: fetch Instagram profile via public viewer mirrors."""
    result = instagram_profile_mirror(username)
    if result is None:
        uname = username.strip().lstrip("@")
        return (
            f"⚠️ All mirror scrapers failed for @{uname}. "
            "Sites may be down, the account may not exist, or the profile is private."
        )
    return json.dumps(result, ensure_ascii=False, indent=2)


def get_tools() -> List[ToolEntry]:
    return [
        ToolEntry("instagram_profile_mirror", {
            "name": "instagram_profile_mirror",
            "description": (
                "Fetch public Instagram profile info via third-party viewer sites "
                "(insta-stories-viewer.com, imginn.com as fallback). "
                "Use when the direct Instagram API (instagram_profile tool) is "
                "rate-limited or blocked. Returns followers, following, posts count, "
                "and bio. Does not require login or API keys."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "username": {
                        "type": "string",
                        "description": "Instagram username (with or without @)",
                    }
                },
                "required": ["username"],
            },
        }, _instagram_profile_mirror_tool),
    ]
