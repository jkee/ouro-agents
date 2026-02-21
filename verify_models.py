#!/usr/bin/env python3
"""
verify_models.py — Query OpenRouter API and report model availability + pricing.

Usage:
    python verify_models.py

Checks for specific model families and prints matching models with pricing.
"""
import json
import urllib.request
import os

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/models"

# Model prefixes/substrings to search for
SEARCH_PATTERNS = [
    "anthropic/claude-4",
    "anthropic/claude-3.7",
    "google/gemini-3",
    "google/gemini-2.5",
    "openai/o3",
    "openai/gpt-4",
]


def fetch_models(api_key: str | None = None) -> list[dict]:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(OPENROUTER_API_URL, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode())
    return data.get("data", [])


def format_price(price_str: str) -> str:
    """Convert per-token price string to per-1M-tokens USD."""
    try:
        per_token = float(price_str)
        per_million = per_token * 1_000_000
        return f"${per_million:.2f}"
    except (ValueError, TypeError):
        return "N/A"


def main():
    api_key = os.environ.get("OPENROUTER_API_KEY")
    print(f"Fetching models from {OPENROUTER_API_URL}...")
    models = fetch_models(api_key)
    print(f"Total models available: {len(models)}\n")

    print("=" * 70)
    print("TARGETED MODEL SEARCH")
    print("=" * 70)

    found: dict[str, list[dict]] = {p: [] for p in SEARCH_PATTERNS}

    for model in models:
        mid = model.get("id", "")
        for pattern in SEARCH_PATTERNS:
            if pattern.lower() in mid.lower():
                found[pattern].append(model)

    for pattern, matches in found.items():
        if not matches:
            print(f"\n[{pattern}] → ❌ NOT FOUND on OpenRouter")
        else:
            print(f"\n[{pattern}] → ✅ {len(matches)} model(s) found:")
            for m in matches:
                mid = m["id"]
                name = m.get("name", "")
                ctx = m.get("context_length", "?")
                pricing = m.get("pricing", {})
                prompt_price = format_price(pricing.get("prompt", "0"))
                completion_price = format_price(pricing.get("completion", "0"))
                cache_read = format_price(pricing.get("input_cache_read", "0"))
                print(f"  ID:      {mid}")
                print(f"  Name:    {name}")
                print(f"  Context: {ctx:,} tokens" if isinstance(ctx, int) else f"  Context: {ctx}")
                print(f"  Pricing: {prompt_price} input / {completion_price} output / {cache_read} cached (per 1M tokens)")
                slug = m.get("canonical_slug", "")
                if slug and slug != mid:
                    print(f"  Slug:    {slug}")
                print()

    print("=" * 70)
    print("RECOMMENDED MODEL IDs FOR .env")
    print("=" * 70)
    print()

    # Extract top recommendations
    recs = {}
    for pattern, matches in found.items():
        if matches:
            # Sort by creation date (newest first)
            matches.sort(key=lambda m: m.get("created", 0), reverse=True)
            recs[pattern] = matches[0]["id"]

    if recs:
        print("# Add to your .env or supervisor config:")
        for pattern, model_id in recs.items():
            env_key = "OUROBOROS_MODEL" if "claude" in pattern else "REVIEW_MODEL"
            print(f"{env_key}={model_id}  # {pattern}")
    else:
        print("No target models found.")

    print()
    print("Done.")


if __name__ == "__main__":
    main()
