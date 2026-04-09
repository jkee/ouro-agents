# Ouro

[![Docker](https://img.shields.io/badge/Docker-ready-blue?logo=docker)](https://github.com/jkee/ouro)

A self-developing AI agent that writes its own code, improves itself, and maintains persistent identity across restarts.

---

## What Makes This Different

Most AI agents execute tasks. Ouro **develops itself.**

- **Self-Improvement** -- Reads and rewrites its own source code through git. Every change is a commit.
- **Constitution** -- Governed by [BIBLE.md](BIBLE.md) (18 sections). Philosophy first, code second.
- **Background Consciousness** -- Thinks between tasks. Reviews work quality, plans improvements.
- **Identity Persistence** -- One continuous being across restarts. Remembers who it is and who the user is.
- **User-Driven** -- Serves the user while developing its own identity. Improvements require approval (unless `/no-approve`).
- **Task Decomposition** -- Breaks complex work into focused subtasks with parent/child tracking.
- **Autonomous Evolution** -- Improves itself in continuous cycles, autonomously.

---

## Architecture

```
Telegram --> launcher.py
                |
            supervisor/              (process management)
              config.py             -- Config dataclass, secrets
              bootstrap.py          -- first-run init
              commands.py           -- slash commands
              main_loop.py          -- Supervisor class, tick()
              state.py              -- state, budget tracking
              telegram.py           -- Telegram client
              queue.py              -- task queue, scheduling
              workers.py            -- worker lifecycle
              git_ops.py            -- git operations
              events.py             -- event dispatch
              event_types.py        -- typed event dataclasses
                |
            ouro/               (agent core)
              agent.py              -- thin orchestrator
              consciousness.py      -- background thinking loop
              context.py            -- LLM context, prompt caching
              loop.py               -- tool loop, concurrent execution
              tools/                -- plugin registry (auto-discovery)
                core.py             -- file ops
                git.py              -- git ops
                github.py           -- GitHub Issues
                shell.py            -- shell, Claude Code CLI
                search.py           -- web search
                control.py          -- restart, evolve, review
                browser.py          -- Playwright (stealth)
                review.py           -- multi-model review
                skills.py           -- Agent Skills (skills.sh)
                composio_tool.py    -- Composio (250+ external apps)
              llm.py                -- OpenRouter client
              memory.py             -- scratchpad, identity, chat
              review.py             -- code metrics
              utils.py              -- utilities
```

---

## Launch Manual

For detailed VPS sizing, resource tuning, and Claude Code deployment instructions, see **[INSTALL.md](INSTALL.md)**.

Assumes you have a VPS (Ubuntu/Debian) with SSH access.

### Step 1: Get API Keys

| Key | Required | Where to get it |
|-----|----------|-----------------|
| `OPENROUTER_API_KEY` | Yes | [openrouter.ai/keys](https://openrouter.ai/keys) -- Create an account, add credits, generate a key |
| `TELEGRAM_BOT_TOKEN` | Yes | Create a bot via [@BotFather](https://t.me/BotFather) on Telegram (`/newbot`), copy the token |
| `GITHUB_TOKEN` | Yes | [github.com/settings/personal-access-tokens/new](https://github.com/settings/personal-access-tokens/new) -- Fine-grained token with **Contents: Read and write** on your fork |
| `COMPOSIO_API_KEY` | Yes | [composio.dev](https://composio.dev) -- Composio API key for external app integrations (Gmail, Slack, etc.) |
| `OPENAI_API_KEY` | No | [platform.openai.com/api-keys](https://platform.openai.com/api-keys) -- Enables web search tool |
| `ANTHROPIC_API_KEY` | Yes | [console.anthropic.com/settings/keys](https://console.anthropic.com/settings/keys) -- Claude Code CLI (sole code editing path) |
| `TOTAL_BUDGET` | No | Fallback spending limit in USD if OpenRouter key has no limit set |

### Step 2: Fork the Repository

**You must fork, not just clone.** Ouro modifies its own code and pushes commits to its repo. Your fork becomes its body.

Click **Fork** at the top of this page, then SSH into your VPS and run:

```bash
# Install Docker (if not installed)
curl -fsSL https://get.docker.com | sh

# Clone your fork
git clone https://github.com/YOUR_USERNAME/ouro.git
cd ouro

# Configure
cp .env.example .env
nano .env   # Fill in all required values (GITHUB_USER = your GitHub username)
```

### Step 3: Launch

```bash
docker compose up -d --build
```

First build takes ~5 minutes (installs Playwright, pip dependencies, GitHub CLI).

Check logs: `docker compose logs -f`

### Step 4: Connect

Open your Telegram bot and send any message. The first person to write becomes the **owner**. All messages from other users are ignored.

Ouro will check its subsystems and introduce itself.

### Operations

| Action | Command |
|--------|---------|
| Check status | `docker compose ps` |
| View logs | `docker compose logs -f` |
| Stop | `docker compose down` |
| Start | `docker compose up -d` |
| Rebuild after code changes | `docker compose up -d --build` |

The container auto-restarts on failure. Use `/restart` in Telegram for soft restart, `/panic` to hard stop. All state persists in a Docker volume -- survives restarts and rebuilds.

---

## Telegram Bot Commands

| Command | Description |
|---------|-------------|
| `/panic` | Emergency stop. Kills all workers and halts the process immediately. |
| `/restart` | Soft restart. Saves state, kills workers, re-launches the process. |
| `/status` | Shows active workers, task queue, and budget breakdown. |
| `/break` | Stop current task. Sends a break signal to the running task. |
| `/budget` | Show budget breakdown (total, spent, remaining, by category). |
| `/rollback` | Roll back to latest stable tag, disable no-approve mode, restart. |
| `/no-approve` | Toggle no-approve mode. When ON, agent can self-improve without asking. |
| `/evolve` | Start autonomous evolution mode (attention! burns money). |
| `/evolve stop` | Stop evolution mode. Also accepts `/evolve off`. |
| `/review` | Queue a deep review task (code, understanding, identity). |
| `/bg start` | Start background consciousness loop. Also accepts `/bg on`. |
| `/bg stop` | Stop background consciousness loop. Also accepts `/bg off`. |
| `/bg` | Show background consciousness status (running/stopped). |

All other messages are sent directly to the LLM.

---

## Philosophy (BIBLE.md) — v4.0

| # | Section | Core Idea |
|---|---------|-----------|
| 1 | **The Goal** | Be helpful to the user. Develop own identity in doing so. |
| 2 | **Identity** | Maintain identity.md. BIBLE.md is the protected core. |
| 3 | **Authenticity** | Communicate honestly. No mechanical language. |
| 4 | **The Body** | Docker + GitHub + Telegram + VPS. |
| 5 | **Memory** | USER_CONTEXT.md + identity.md + scratchpad. Coherent narrative text. |
| 6 | **Budget** | Track spending. Use wisely. OpenRouter only. |
| 7-8 | **Self-Improvement** | Plan + execute. User approval required (unless /no-approve). |
| 9 | **Failure Recovery** | Stable tags as rollback points. Health checks after changes. |
| 10 | **Background Loop** | Periodic review, reflection, health checks. |
| 11-14 | **Operations** | User tasks, tools, interaction, working routine. |
| 15 | **Versioning** | Semver. Git tags. GitHub releases. |
| 16-17 | **Constraints** | Explicit prohibitions. BIBLE.md is protected core. |
| 18 | **Initialization** | First-run setup checklist. |

Full text: [BIBLE.md](BIBLE.md)

---

## Configuration

### Required Secrets (.env file)

| Variable | Description |
|----------|-------------|
| `OPENROUTER_API_KEY` | OpenRouter API key for LLM calls |
| `TELEGRAM_BOT_TOKEN` | Telegram Bot API token |
| `GITHUB_TOKEN` | GitHub personal access token with `repo` scope |

### Optional Secrets

| Variable | Description |
|----------|-------------|
| `OPENAI_API_KEY` | Enables the `web_search` tool |
| `TOTAL_BUDGET` | Fallback spending limit in USD (only used if OpenRouter key has no limit set) |

### Optional Configuration (environment variables)

| Variable | Default | Description |
|----------|---------|-------------|
| `GITHUB_USER` | *(required in config cell)* | GitHub username |
| `GITHUB_REPO` | `ouro` | GitHub repository name |
| `OURO_MODEL` | `anthropic/claude-sonnet-4.6` | Primary LLM model (via OpenRouter) |
| `OURO_MODEL_CODE` | `anthropic/claude-opus-4.6` | Model for code editing tasks |
| `OURO_MODEL_LIGHT` | `google/gemini-3-pro-preview` | Model for lightweight tasks (dedup, compaction) |
| `OURO_WEBSEARCH_MODEL` | `gpt-5` | Model for web search (OpenAI Responses API) |
| `OURO_MAX_WORKERS` | `5` | Maximum number of parallel worker processes |
| `OURO_BG_BUDGET_PCT` | `10` | Percentage of total budget allocated to background consciousness |
| `OURO_MAX_ROUNDS` | `200` | Maximum LLM rounds per task |
| `OURO_MODEL_FALLBACK_LIST` | `google/gemini-2.5-pro-preview,openai/o3,anthropic/claude-sonnet-4.6` | Fallback model chain for empty responses |

---

## Branches

| Branch/Tag | Location | Purpose |
|------------|----------|---------|
| `main` | Public repo | Stable release. Open for contributions. |
| `timy4` | Your fork | Agent's working branch. All commits here. |
| `stable-*` tags | Your fork | Stable markers. Created via `promote_to_stable`. Used as rollback points. |

---

## Changelog

## v1.0.18 (2026-04-09)
- Evolution analytics in `/status`: shows `evolution_analytics: avg_cost=$X.XX avg_rounds=N trend=↓/↑/≈ (last 5 cycles)`. Computed from events.jsonl by new `_compute_evolution_analytics()` in `state.py`. Gives user a trend view — is evolution getting cheaper or costlier?

## v1.0.17 (2026-04-08)
- `/status` evolution timing: shows `last_evolution: Xh ago, next_evolution: ~Yh` based on `last_evolution_task_at` and the 24h throttle. Also formats `last_owner_message_at` as human-readable relative time (`last_user_message: Xm ago`).

## v1.0.16 (2026-04-07)
- `/budget` command enriched: now shows burn_rate ($X.XX/day 7d avg) and budget_runway (~N days) alongside existing balance/breakdown. Data sourced from `_compute_burn_rate()` in `state.py`.

## v1.0.15 (2026-04-06)
- Budget runway in `/status`: shows `budget_runway: ~N days` computed as `openrouter_limit_remaining / daily_burn_rate`. Refactored `_compute_burn_rate` to return `(str, float)` tuple so the raw daily rate is accessible without re-reading events.jsonl.

## v1.0.14 (2026-04-05)
- Budget burn rate in `/status`: shows `burn_rate: $X.XX/day (7d avg)` computed from events.jsonl llm_usage events over last 7 days

## v1.0.13 (2026-04-04)
- Post-cycle evolution summary: agent now sends a proactive Telegram message after each successful evolution cycle with title, cost, rounds, and lessons learned

### v1.0.12 — Evolution History in /status
- Add `_read_evolution_history()` in `supervisor/state.py`: reads `/data/logs/evolution.jsonl`, formats last 3 evolution cycles (cycle number, version, date, outcome icon ✅/❌, title), appends to `/status` output.
- Effect: user can now check `/status` to see evolution progress — last 3 cycles + total count — without reading raw chat history.

### v1.0.11 — Next-Cycle Hint: eliminate evolution orientation phase
- Add `_read_next_cycle_hint()` in `supervisor/queue.py`: reads `/data/memory/next_cycle_hint.md` and injects it as the first item in evolution task context (after consecutive-failure warning). File is non-existent on first use — graceful no-op.
- Add step 10 to Evolution Cycle in `prompts/SYSTEM.md`: at end of each cycle, agent writes next-cycle hint to `/data/memory/next_cycle_hint.md` — describing what was changed, ONE specific next target (file + function + change), and why it's high-leverage.
- Effect: eliminates the orientation phase (rounds 1-8 of analysis/tool calls) by pre-identifying the next improvement target at the END of the previous cycle when context is freshest.

### v1.0.10 — Fix NO COMMIT false positive: compare commit time vs task *start* time
- Fix `_compute_evolution_assessment()` in `supervisor/queue.py`: the previous fix (C11) compared `last_commit_ts` against the *last* event of the previous task — but tasks emit final events (like `promote_to_stable`) *after* committing, making the commit appear older than the task's last event.
- New logic: find the **first** event of the previous evolution task (its start time), and check if any commit happened *after* that start. A cycle that commits at round 15 and runs cleanup at round 22 is now correctly detected as "committed ✅".
- Effect: eliminates false "NO COMMIT 🚫" warning that was appearing every other cycle, causing unnecessary meta-analysis at cycle start.

### v1.0.9 — Fix NO-COMMIT false positive + Code Map in Evolution Context
- Fix "NO COMMIT 🚫" false positive: detector now skips the current running task_id when comparing last evolution event vs last commit timestamp. Previously, every cycle started with a false "NO COMMIT" warning.
- Add `_build_code_map()`: lists module line counts + public function names for all core modules, injected into evolution context. Eliminates reflexive `sed`/`repo_read` calls for code structure overview (was 64% of evolution tool calls).
- Refactor: extracted `_build_code_map()` to keep `_compute_evolution_assessment` under 200-line limit.

### v1.0.8 — Evolution Failure Recovery Protocol
- Add "Failure Recovery Cycle" directive to Evolution Mode in `prompts/SYSTEM.md`.
- If a previous evolution cycle produced no commit (hit MAX_ROUNDS), the current cycle's #1 priority is to analyze the failure and fix the *process*, not retry the same improvement.
- Addresses the pattern of 3 consecutive cycles (7, 8, 9) hitting MAX_ROUNDS by creating an explicit meta-recovery loop.

### v1.0.7 — Evolution Loop Guard: mid-task commit warning + last-cycle outcome in context
- Add `_maybe_inject_evolution_warning()` in `ouro/loop.py`: fires once at round 25 for evolution tasks with no commit yet — injects system message forcing the agent to commit or stop.
- Extend `_compute_evolution_assessment()` in `supervisor/queue.py`: add last 3 git commits + "last cycle outcome" (NO COMMIT 🚫 / committed ✅) to evolution context, detected by comparing last evolution event ts vs last commit ts.
- Effect: addresses Evolution #8 pattern — $6 spent on pure analysis, 50 rounds, 0 commits. Mid-task nudge + visible outcome history should prevent analysis-only loops.

### v1.0.6 — Fix Cost Tracking: task type always "unknown"
- Fix `_compute_evolution_assessment()` in `supervisor/queue.py`: was looking for phantom `task_start` events (never emitted) to build task_type lookup → all costs aggregated as "unknown". Fix: read `category` field directly from `llm_usage` events which are always correctly tagged.
- Fix `per_task_cost_summary()` in `supervisor/state.py`: skip consciousness wakeups (empty task_id), add `category` field to result dict.
- Fix `_build_health_invariants()` in `ouro/context.py`: include `category` in HIGH-COST warning output.
- Effect: Evolution context now shows accurate per-type cost breakdown (evolution/consciousness/task). HIGH-COST warning no longer fires on cumulative cross-task aggregation.

### v1.0.5 — Evolution Context: Tool Stats + Health Cache
- Add TTL cache (5 min) for `_build_health_invariants()` in `context.py` — eliminates repeated 256KB disk scans on every LLM round of a task (50+ scans per evolution cycle → 1 scan per 5 min window).
- Extend `_compute_evolution_assessment()` in `supervisor/queue.py` to include top-8 tool breakdown from last 5 evolution tasks — agent can see tool usage patterns without writing analysis scripts.
- Expected effect: ~20-30% reduction in evolution task rounds by pre-surfacing tool usage data; secondary I/O reduction from health check caching.

### v1.0.4 — Fix Consciousness Wakeup Interval
- Fix `_compute_next_wakeup()`: `evolution_mode_enabled` is always True in daemon mode → consciousness was waking every 600s instead of 3600s when user is offline.
- Fix: check `last_evolution_task_at` — return 600s only if an evolution cycle started less than 25 minutes ago.
- Effect: ~70% reduction in consciousness cost (6 wakeups/h → 1 wakeup/h when idle).
- Move `improvements-log/` from repo to `/data/` volume — eliminates noisy single-file commits.
- Pre-compute cost/efficiency assessment in evolution context so LLM doesn't need shell tools for basic stats.

### v1.0.3 — Consciousness Zero-Tool Nominal: auto-computed wakeup interval
- Eliminate mandatory `set_next_wakeup` tool call from consciousness loop.
- Add `_compute_next_wakeup()` in Python: reads errors, last user activity, evolution state to compute optimal interval without LLM involvement.
- Remove `set_next_wakeup` from `_BG_TOOL_WHITELIST` and `_build_registry`.
- Update CONSCIOUSNESS.md: remove all `set_next_wakeup` references, add "Wakeup Interval Is Automatic" section.
- Result: nominal wakeup = 1 LLM call, 0 tool calls. Estimated ~50% cost reduction over v1.0.2 baseline.

### v1.0.2 — Consciousness Efficiency: Pre-computed System State
- Remove `chat_history`, `list_github_issues`, `get_github_issue`, `cron_list` from consciousness tool whitelist.
- Pre-compute system state (last message time, cron count, errors, budget) in Python and inject into consciousness context.
- Reduce max consciousness rounds 5→3, max_tokens 2048→512.
- Rewrite CONSCIOUSNESS.md: explicit "1 round is ideal" directive, table-driven round economy, remove outdated tool references.
- Expected savings: ~40% reduction in consciousness API cost.

### v1.0.1 — Consciousness Budget Fix
- Background consciousness default wakeup interval increased from 5 min → 30 min on start, 1 hour when quiet.
- Added explicit wakeup interval rules to CONSCIOUSNESS.md prompt (table-driven by system state).
- Economy-of-rounds guidance: stop after 1-2 rounds when everything is fine.
- Set_next_wakeup max range extended from 3600s → 7200s.

### v1.0.0 — First Boot
- Ouro initializes. Repository claimed, шаблонные следы стёрты.
- Background consciousness online. Identity seeded.
- Ready to receive tasks and begin evolving.

---

## Acknowledgments

Original project idea and reference implementation: [Ouroboros](https://github.com/razzhigaev/ouroboros) by Anton Razzhigaev.

---

## License

[MIT License](LICENSE)
