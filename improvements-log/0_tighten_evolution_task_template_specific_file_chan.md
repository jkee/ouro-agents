# Tighten evolution task template: specific file + change type + done criteria

**Cycle:** 0 | **Version:** 1.0.0 | **Date:** 2026-03-22
**Category:** bugfix | **Outcome:** success
**Git:** 7deb892637 on gintonic

## Motivation

Evolution task f175691e burned 50 rounds and $2.84 with zero output. Root cause: vague prompt with three domains and no done criteria led to analysis paralysis, then an off-road detour into Claude CLI permissions debugging.

## Changes

Rewrote build_evolution_task_text() in supervisor/queue.py. New template: (1) specific target file from deterministic rotation of 7 targets, (2) explicit change type, (3) stated done criteria as verifiable condition, (4) 5-step process with hard stop. Old vague 'EVOLUTION #N find something in code/prompt/architecture' replaced with concrete task per cycle.

**Files changed:**

- `supervisor/queue.py`

## Lessons Learned

claude_code_edit kept failing with UND_ERR_CONNECT_TIMEOUT — worked around with Python patch script written to /data/tmp_patch.py. Deterministic rotation (cycle % N) preferred over random for reproducibility. Vague prompts are an infinite loop waiting to happen.
