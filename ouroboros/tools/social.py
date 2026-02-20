"""Social media tools: instagram_profile."""

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
    """Fetch public Instagram profile info by username."""
    try:
        import requests
    except ImportError:
        return "⚠️ 'requests' not installed. Run: pip install requests"

    username = username.strip().lstrip("@")
    url = _ENDPOINT.format(username=username)

    try:
        resp = requests.get(url, headers=_INSTAGRAM_HEADERS, timeout=15)
    except requests.exceptions.Timeout:
        return f"⚠️ Request timed out for @{username}"
    except requests.exceptions.RequestException as e:
        return f"⚠️ Network error: {e}"

    if resp.status_code == 404:
        return f"⚠️ Account @{username} not found (404)"
    if resp.status_code == 429:
        return f"⚠️ Rate limited by Instagram (429). Try again later."
    if resp.status_code == 401:
        return f"⚠️ Instagram returned 401 — account may be private or endpoint blocked."
    if resp.status_code != 200:
        return f"⚠️ Instagram returned HTTP {resp.status_code} for @{username}"

    try:
        data = resp.json()
    except ValueError:
        return f"⚠️ Instagram returned non-JSON response (likely bot detection or login wall)"

    user = data.get("data", {}).get("user")
    if not user:
        return f"⚠️ No user data in response for @{username}. Account may be private or deleted."

    followers = user.get("edge_followed_by", {}).get("count", "?")
    following = user.get("edge_follow", {}).get("count", "?")
    posts = user.get("edge_owner_to_timeline_media", {}).get("count", "?")
    full_name = user.get("full_name", "")
    bio = user.get("biography", "")
    is_private = user.get("is_private", False)
    is_verified = user.get("is_verified", False)
    is_professional = user.get("is_professional_account", False)
    profile_pic = user.get("profile_pic_url", "")
    external_url = user.get("external_url", "")
    category = user.get("category_name", "")

    result = {
        "username": username,
        "full_name": full_name,
        "followers": followers,
        "following": following,
        "posts": posts,
        "bio": bio,
        "is_private": is_private,
        "is_verified": is_verified,
        "is_professional": is_professional,
        "category": category,
        "external_url": external_url,
        "profile_pic_url": profile_pic,
    }

    return json.dumps(result, ensure_ascii=False, indent=2)


def get_tools() -> List[ToolEntry]:
    return [
        ToolEntry("instagram_profile", {
            "name": "instagram_profile",
            "description": (
                "Fetch public Instagram profile stats by username. "
                "Returns followers, following, posts count, bio, and account flags "
                "(private, verified, professional). Works without login for public accounts."
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
