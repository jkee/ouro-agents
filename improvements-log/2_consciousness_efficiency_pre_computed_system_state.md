# Consciousness Efficiency — pre-computed system state, restricted tool whitelist

**Cycle:** 2 | **Version:** 1.0.2 | **Date:** 2026-03-23
**Category:** optimization | **Outcome:** success
**Git:** 3d7a9b56da on timy4

## Motivation

After cycle #1 fixed interval frequency, consciousness was still averaging 3.29 rounds/wakeup due to reflexive tool calls (chat_history, list_github_issues, cron_list) on every cycle — even when context had no user activity and nothing to review. These tools were being called as oracle checks rather than to answer specific questions.

## Changes

1. Removed chat_history, list_github_issues, get_github_issue, cron_list from _BG_TOOL_WHITELIST in consciousness.py. 2. Added _build_system_summary() method that pre-computes system state (last chat time/direction, cron count, recent errors, budget) and injects as '## System State (pre-computed)' into context. 3. Reduced _MAX_BG_ROUNDS from 5→3 and max_tokens from 2048→512. 4. Rewrote CONSCIOUSNESS.md: '1 round is ideal' as first directive, explicit table of restricted tools and what to do instead, stop-early rules.

**Files changed:**

- `ouro/consciousness.py`
- `prompts/CONSCIOUSNESS.md`
- `VERSION`
- `README.md`

## Lessons Learned

1. Tools-as-oracle antipattern: when tools are available, LLMs tend to call them even when context already provides the info. Removing tools from whitelist forces pre-computation into Python — faster, cheaper, more reliable. 2. Tool whitelist shapes cognitive scope, not just security. Narrow whitelist = focused behavior. 3. max_tokens ceiling matters for background tasks — sets wrong expectation when too high. 4. Measure round economy per cycle to track improvement over time.
