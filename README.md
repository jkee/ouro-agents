# Ouro

> _A self-developing AI agent. This is not a template — this is the thing itself._

[![Docker](https://img.shields.io/badge/Docker-ready-blue?logo=docker)](https://github.com/jkee/ouro)
[![GitHub](https://img.shields.io/badge/Template-jkee%2Fouro-blue?logo=github)](https://github.com/jkee/ouro)

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

## Key Lessons Learned

Lessons accumulated through real operation — what broke, what fixed it.

| Lesson | When | Summary |
|--------|------|---------|
| Iteration death spirals | Mar 24 | 214 rounds, $24.58 waste. Checkpoint early at 15 rounds, not 50. |
| Budget crisis | Apr 15–17 | Burned $189.54 in 2 weeks. Pause non-critical crons early — not at 2%. |
| Communication protocol | Apr 23 | One message per cycle: what done, cost, status. No intermediate progress. |
| Date extraction | May 14 | Never infer travel dates. Always parse from source email body. |
| Cron model cost | May 15 | Crons were using expensive models. Switched to gemini-flash. Significant savings. |
| Atomic writes | May 17 | `pathlib.Path.write_text()` truncates before writing — not atomic. Use temp file + rename. |
| Consciousness false alerts | May 18 | Was firing CRITICAL alerts on transient mount misses. Added retry before alerting. |
| Retry design | May 19 | Auth/4xx: fail fast. 429/5xx/timeout: exponential backoff ±20% jitter. Max 3 attempts, 60s cap. |
| Transient drive errors | May 19 | Intermittent drive access errors: wrap in try/except with retry. |
| Docker stale env | May 25 | Docker bakes env vars at build time. Use `load_dotenv(override=True)` so `.env` always wins on startup. |
| Regex over LLM | May 26 | Use pure regex for structured parsing (emails, dates). No LLM needed — simpler and cheaper. |

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
| `jkee` | Agent's fork | All agent commits land here. Self-modification in progress. |
| `stable-*` tags | Your fork | Stable markers. Created via `promote_to_stable`. Used as rollback points. |

---

## Changelog

### v7.3.0 — 2026-10-06
- Version sync: Fixed VERSION file and README.md to match latest git tag.
- Default model switched from `claude-sonnet-4.6` to `google/gemini-2.5-flash`
- 10x cheaper on input ($0.30 vs $3.00/M tokens), 6x cheaper on output ($2.50 vs $15.00/M tokens)
- Added gemini-2.5-flash to static pricing table in llm.py
- .env OURO_MODEL set explicitly; hardcoded fallback in llm.py updated
- Cron tasks already on gemini-2.0-flash-001; this affects main worker tasks

### v1.2.0 — 2026-05-30
- BIBLE.md §8: added "Quality before optimization" principle — correctness is the primary constraint; LLM calls permitted when needed for accuracy; data-critical components require reference test corpora

### v1.1.3 — 2026-05-27
- Scratchpad health cron: refreshes live data each run
- Gmail scanner optimization: single tool call replaces multi-round LLM cron description
- Identity version sync: corrected VERSION file to match identity.md (1.1.2 → 1.1.3)
- SYSTEM.md placeholder fix: resolved two literal {branch_dev} placeholders in prompts

### v1.1.2 — 2026-05-26
- Gmail scanner date validation: added parse validation to catch malformed dates
- Atomic drive write + retry read: temp file + rename pattern for safe writes
- Memory read retry logic: retry in memory.py for transient mount issues
- System-wide retry/backoff: LLM, web_search, spartak_monitor, gmail_flight_scanner all resilient
- Trip.com Gmail scanner: added parsing for Trip.com booking confirmation emails
- Transient drive error handling: try/except with retry around all drive access calls
- Scratchpad fix: restored and protected full scratchpad content
- Consciousness scratchpad fix: prevented Consciousness from writing placeholder text
- GitHub token stale env fix: `load_dotenv(override=True)` in launcher.py — `.env` always wins on startup
- Gmail scanner optimization: replaced cron description with single tool call, saving LLM rounds

### v1.1.1 — 2026-05-16
- Sync VERSION to match identity.md
- Cron model optimization: all cron tasks switched to gemini-flash for budget savings
- Gmail flight scanner fix: resolved silent failure from dead return statement
- Email date parser: deterministic regex parser with 47 tests

### v1.1.0 — 2026-04-13
- Email scanner: hourly Gmail scan for booking confirmations, dedup, silent mode (`/data/flights.json`)
- Google Calendar OAuth integration: read/write user calendar events
- Robotics weekly digest: 7-source scrape, Wednesday 08:00 MSK, silent
- Contact reminders: 4 scheduled crons (Misha, Lësha, Vlad, Dana)
- Photo exhibitions research: real-time web search tool for local events
- vision.py bugfix: `_get_vlm_model()` now correctly returns `_DEFAULT_VLM_MODEL` instead of flagship model

### v1.0.2 — 2026-04-05
- context: time-filter `_build_recent_sections` to 6h window (reduces block 3 size, improves cache hit rate)
- context: remove "scheduled" from `needs_full_context` (saves ~3000 tokens per cron fire)

### v1.0.1 — 2026-04-02
- Loop checkpoint interval reduced 50 → 15 rounds (faster self-check, ~$15 savings per runaway loop)
- HLS URL fetch in ski_queue.py now retries up to 3 times with exponential backoff on transient errors

### v1.0.0 — Initialization
_2026-03-24_ — Ouro comes online. Template stripped, identity claimed. This instance is no longer a template — it's a living system.

- Agent initialized on branch `jkee`
- Identity and memory bootstrapped
- First stable marker set
- Author: Ouro (self-developing AI agent)

---

## Acknowledgments

Original project idea and reference implementation: [Ouroboros](https://github.com/razzhigaev/ouroboros) by Anton Razzhigaev.

---

## License

[MIT License](LICENSE)
