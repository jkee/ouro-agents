"""
Social media tools: instagram_profile with automatic fallback via mirror scrapers.
"""

from __future__ import annotations

import json
import logging
from typing import List

from ouroboros.tools.registry import ToolContext, ToolEntry

log = logging.getLogger(__name__)

_INSTAGRAM_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36 "
        "Instagram/219.0.0.12.117 Android"
    ),
    "X-IG-App-ID": "936619743392459",
    "Host": "www.instagram.com",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}

_ENDPOINT = "https://www.instagram.com/api/v1/users/web_profile_info/?username={username}"


def _instagram_profile(ctx: ToolContext, username: str) -> str:
    """
    Fetch public Instagram profile info by username.
    Tries the official private API first; automatically falls back to
    public viewer mirror sites (insta-stories-viewer.com, imginn.com)
    if the direct API is blocked or rate-limited.
    """
    try:
        import requests
    except ImportError:
        return "Instagram profile fetch requires the 'requests' library."

    username = username.strip().lstrip("@")
    url = _ENDPOINT.format(username=username)

    # --- Attempt 1: direct Instagram private API ---
    direct_error: str | None = None
    resp = None
    try:
        resp = requests.get(url, headers=_INSTAGRAM_HEADERS, timeout=15)
    except requests.exceptions.Timeout:
        direct_error = f"Request timed out for @{username}"
    except requests.exceptions.RequestException as e:
        direct_error = f"Network error: {e}"

    if resp is not None and direct_error is None:
        if resp.status_code == 404:
            return f"Account @{username} not found (404)"
        if resp.status_code in (401, 403, 429):
            direct_error = f"Instagram API blocked (HTTP {resp.status_code})"
        elif resp.status_code != 200:
            direct_error = f"Instagram returned HTTP {resp.status_code} for @{username}"

    if direct_error is None and resp is not None:
        try:
            data = resp.json()
        except ValueError:
            direct_error = "Non-JSON response from Instagram (bot detection or login wall)"
            data = None
    else:
        data = None

    if data is not None:
        user = data.get("data", {}).get("user")
        if not user:
            direct_error = f"No user data for @{username} (account may be private or deleted)"
        else:
            result = {
                "username": username,
                "full_name": user.get("full_name", ""),
                "followers": user.get("edge_followed_by", {}).get("count", "?"),
                "following": user.get("edge_follow", {}).get("count", "?"),
                "posts": user.get("edge_owner_to_timeline_media", {}).get("count", "?"),
                "bio": user.get("biography", ""),
                "is_private": user.get("is_private", False),
                "is_verified": user.get("is_verified", False),
                "is_professional": user.get("is_professional_account", False),
                "category": user.get("category_name", ""),
                "external_url": user.get("external_url", ""),
                "profile_pic_url": user.get("profile_pic_url", ""),
                "source": "instagram_api",
            }
            return json.dumps(result, ensure_ascii=False, indent=2)

    # --- Attempt 2: mirror fallback ---
    log.info("Direct Instagram API failed (%s), trying mirror fallback", direct_error)
    try:
        from ouroboros.tools.social_mirror import instagram_profile_mirror
        mirror_result = instagram_profile_mirror(username)
        if mirror_result:
            mirror_result["_fallback_reason"] = direct_error
            return json.dumps(mirror_result, ensure_ascii=False, indent=2)
    except Exception as exc:
        log.warning("Mirror fallback raised: %s", exc)

    return f"{direct_error}. Mirror fallback also failed — try again later."


def get_tools() -> List[ToolEntry]:
    return [
        ToolEntry("instagram_profile", {
            "name": "instagram_profile",
            "description": (
                "Fetch public Instagram profile stats by username. "
                "Returns followers, following, posts count, bio, and account flags "
                "(private, verified, professional). Tries the Instagram private API "
                "first; automatically falls back to public viewer mirror sites if "
                "the direct API is rate-limited or blocked."
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
        }, _instagram_profile),
    ]