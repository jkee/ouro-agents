# Fix image generation — OpenRouter modalities parameter

**Cycle:** 1 | **Version:** 1.1.0 | **Date:** 2026-03-19
**Category:** bugfix | **Outcome:** success
**Git:** 5978ce8298 on timy4

## Motivation

User requested image generation (duck), it failed. Investigated OpenRouter API — found root cause: missing `modalities` parameter. User asked to investigate why images don't work on OpenRouter.

## Changes

- Rewrote ouro/tools/image_gen.py with correct modalities parameter
- FLUX models: modalities=['image'], Google image models: modalities=['text','image']
- Default model: black-forest-labs/flux.2-klein-4b (cheapest, confirmed working)
- Response parsing: handles both data:image URLs and raw base64
- Images auto-sent via ctx.pending_events send_photo event (Telegram-ready)
- VERSION 1.0.0 → 1.1.0

**Files changed:**

- `ouro/tools/image_gen.py`
- `VERSION`
- `README.md`
- `tests/test_smoke.py`

## Lessons Learned

OpenRouter image generation requires explicit `modalities` parameter in the request. Without it, image models return empty/null responses. Always test API calls with curl before writing tool code — found the fix in 2 minutes once I hit the actual API.
