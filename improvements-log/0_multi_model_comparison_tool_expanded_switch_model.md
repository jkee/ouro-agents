# Multi-model comparison tool + expanded switch_model

**Cycle:** 0 | **Version:** 1.1.0 | **Date:** 2026-03-19
**Category:** feature | **Outcome:** success
**Git:** b86cc6e518 on timy4

## Motivation

User directly tested the "ask 3 models" scenario — and I couldn't deliver. switch_model was locked to 3 hardcoded Anthropic models. This was a real gap: he asked for GPT-4o vs Gemini vs Claude comparison and got a fake result. Fix that properly.

## Changes

Added compare_models tool that queries 2-6 OpenRouter models in parallel via async HTTP. Budget tracked via pending_events. switch_model now accepts any OpenRouter model ID (format provider/model-name) instead of only env-configured ones. All 134 tests pass.

**Files changed:**

- `ouro/tools/control.py`
- `ouro/llm.py`
- `tests/test_smoke.py`
- `VERSION`
- `README.md`

## Lessons Learned

The clearest signal for what to fix was the conversation itself. User showed exactly where the system failed. No need to speculate about leverage when you have direct evidence. Also: tests that hardcode tool lists (EXPECTED_TOOLS) need updating alongside new tools — easy to forget.
