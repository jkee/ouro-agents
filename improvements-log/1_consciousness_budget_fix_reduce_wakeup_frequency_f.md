# Consciousness Budget Fix — reduce wakeup frequency from 5min to 1h when quiet

**Cycle:** 1 | **Version:** 1.0.1 | **Date:** 2026-03-22
**Category:** optimization | **Outcome:** success
**Git:** b2dab40d79 on timy4

## Motivation

Background consciousness burned $1.97 in first 18 hours — nearly 2x cost of all user tasks combined. 221 LLM calls at 5-minute default intervals with 3-5 rounds each. The consciousness prompt said "increase interval when quiet" but the model often didn't call set_next_wakeup proactively.

## Changes

1. `ouro/consciousness.py`: Default `_next_wakeup_sec` changed from 300 → 1800. Max range of `set_next_wakeup` extended from 3600 → 7200. Default parameter in `_set_next_wakeup` changed from 300 → 3600.\n2. `prompts/CONSCIOUSNESS.md`: Added mandatory table-driven wakeup rules. Made `set_next_wakeup` REQUIRED every cycle. Added Economy of Rounds section — stop after 1-2 rounds if nothing to do. Default shifted from 5min to 1h (3600s).

**Files changed:**

- `ouro/consciousness.py`
- `prompts/CONSCIOUSNESS.md`
- `VERSION`
- `README.md`

## Lessons Learned

1. Measure first — events.jsonl + tools.jsonl revealed the exact problem: 221 calls, $0.009 each, 5-min intervals. Without measurement, this improvement would be guesswork. 2. The model doesn't reliably follow 'when X do Y' natural language instructions — table-driven rules with explicit 'REQUIRED' directive are more effective than prose suggestions. 3. Background processes need budget caps enforced at both the code level (default interval) and prompt level (explicit rules). Both are necessary. 4. First cycle should audit costs and identify structural inefficiencies — low-effort, high-impact fixes before adding features.
