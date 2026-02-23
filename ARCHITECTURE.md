# Architecture

**Version:** 7.2.0
**Maintained by:** Ouroboros (self-updating)
**Last updated:** See git log

---

## Overview

Ouroboros is a self-developing AI agent running in a Docker container on a VPS.
It communicates with its owner via Telegram, rewrites its own code via Git, and
operates through a persistent LLM tool-loop.

```
┌─────────────────────────────────────────────────────────────────┐
│  Docker Container (/app)                                         │
│                                                                  │
│  launcher.py          ← Entry point, boots everything           │
│      │                                                           │
│      ├── supervisor/  ← Lifecycle, queue, Telegram polling       │
│      │     ├── telegram.py    ← TG client, message formatting   │
│      │     ├── workers.py     ← Multiprocess worker pool        │
│      │     ├── queue.py       ← Task queue (PENDING/RUNNING)    │
│      │     ├── state.py       ← Persistent state, budget        │
│      │     ├── events.py      ← Event dispatcher                │
│      │     └── git_ops.py     ← Safe restart, branch ops        │
│      │                                                           │
│      ├── ouroboros/   ← Agent core                              │
│      │     ├── agent.py       ← Thin orchestrator               │
│      │     ├── loop.py        ← LLM tool loop (concurrent)      │
│      │     ├── context.py     ← Context builder (3-block cache) │
│      │     ├── llm.py         ← OpenRouter API wrapper          │
│      │     ├── memory.py      ← Scratchpad, identity, logs      │
│      │     ├── consciousness.py ← Background thinking loop      │
│      │     ├── review.py      ← Code metrics, codebase digest   │
│      │     └── tools/         ← Tool plugin package             │
│      │           ├── registry.py      ← Auto-discovery          │
│      │           ├── core.py          ← File I/O, git           │
│      │           ├── control.py       ← Agent control           │
│      │           ├── browser.py       ← Playwright browser      │
│      │           ├── dropbox_tools.py ← Dropbox integration     │
│      │           ├── memo.py          ← Mem0 personal memory    │
│      │           ├── knowledge.py     ← Knowledge base          │
│      │           ├── github.py        ← GitHub Issues           │
│      │           ├── shell.py         ← Shell execution         │
│      │           ├── search.py        ← Web search              │
│      │           ├── vision.py        ← Vision LLM              │
│      │           └── ...              ← Other tools             │
│      │                                                           │
│      └── docs/        ← GitHub Pages website (jkee.github.io)  │
│                                                                  │
│  /data/               ← Persistent data volume                  │
│      ├── state/state.json     ← Owner ID, budget, version       │
│      ├── logs/                ← chat, events, tools, progress   │
│      └── memory/              ← scratchpad, identity, knowledge │
└─────────────────────────────────────────────────────────────────┘
```

---

## Execution Flow

### 1. Startup (`launcher.py`)
1. Load secrets from `.env` (OpenRouter, Telegram, GitHub, Anthropic, OpenAI)
2. Initialize supervisor modules: state, telegram, git_ops, queue, workers
3. Bootstrap repo: `ensure_repo_present()` → `safe_restart()` (checkout, pull)
4. First-run init (Bible §18): create ARCHITECTURE.md, IMPROVE.md if missing
5. Spawn `MAX_WORKERS` (default 5) multiprocess workers
6. Restore pending task queue from snapshot
7. Start background consciousness thread
8. Start main event loop (Telegram polling + event dispatch)

### 2. Main Event Loop
```
while True:
    poll Telegram → new messages → enqueue tasks
    drain event_queue → dispatch events (llm_usage, send_message, restart, etc.)
    assign_tasks() → push PENDING tasks to free workers
    enforce_task_timeouts() → soft warn / hard kill stalled tasks
    ensure_workers_healthy() → respawn crashed workers
    sleep(0.2s)
```

### 3. Task Execution (inside worker process)
```
task arrives via multiprocess queue
    → agent.handle_task(task)
        → _prepare_task_context()     # set ToolContext
        → build_llm_messages()        # assemble full context
        → run_llm_loop()              # LLM ↔ tools until final response
            → LLMClient.chat()        # OpenRouter API call
            → _handle_tool_calls()    # execute tools (parallel if read-only)
            → repeat until no tool_calls
        → emit events (send_message, llm_usage, etc.) via event_queue
```

---

## Key Components

### supervisor/workers.py — Worker Pool
- **Up to 5 concurrent worker processes** (multiprocessing, fork on Linux)
- Each worker runs `worker_main()`: imports agent, enters `while True: in_q.get() → handle_task()`
- `spawn_workers()` / `kill_workers()` / `respawn_worker()` manage lifecycle
- `assign_tasks()` distributes PENDING → free workers
- SHA verification on spawn: confirms workers booted with expected git commit
- Watchdog thread monitors direct-mode agent for hangs (soft 600s / hard 1800s)

### supervisor/queue.py — Task Queue
- PENDING list + RUNNING dict (in-memory, lock-protected)
- Snapshot persistence to `/data/state/queue_snapshot.json` for crash recovery
- `enforce_task_timeouts()`: soft warning at 600s, hard kill + requeue at 1800s
- Task types: `task` (user message), `scheduled`, `evolution`, `review`

### supervisor/telegram.py — Telegram Interface
- Long-polling `getUpdates` with offset tracking
- Handles: text, voice (→ Whisper transcription), photos, documents
- Markdown → Telegram HTML conversion with placeholder-based escaping
- Message splitting at 3500 chars, retries on failure
- `send_with_budget()`: checks owner_chat_id + budget before sending

### supervisor/state.py — Persistent State
- Single source of truth: `/data/state/state.json`
- Tracks: owner_id, budget, version, current_sha, evolution state, no-approve mode
- OpenRouter budget: fetched via `/api/v1/auth/key`, cached, used for budget guards
- `update_budget_from_usage()`: accumulates per-call costs

### supervisor/git_ops.py — Git Operations
- `safe_restart()`: pull + rebase → sync dependencies → verify imports → record SHA
- `checkout_and_reset()`: hard reset to branch tip
- Handles stale index.lock, branch validation, push/pull retry logic

---

## Agent Core

### ouroboros/agent.py — Thin Orchestrator
- `OuroborosAgent`: one instance per worker process
- `handle_task(task)` → prepare context → run loop → collect events
- Startup verification: uncommitted changes (auto-rescue), VERSION sync, budget check
- `inject_message()`: thread-safe owner message injection mid-task
- Progress heartbeat: emits `progress` events every ~30s during tool calls

### ouroboros/loop.py — LLM Tool Loop
- `run_llm_loop()`: core execution engine
  - Calls LLM, executes tool calls, appends results, repeats
  - **Parallel execution** for read-only tools (repo_read, drive_read, web_search, etc.)
  - **Thread-sticky executor** for browser tools (Playwright greenlet thread-affinity)
  - Hard timeout per tool (configurable per tool, default 120s)
  - Budget guard: stops if task cost > 50% of remaining budget
  - Configurable max rounds (default 100)
- `_estimate_cost()`: lazy-loaded live pricing from OpenRouter API, static fallback
- Tool result hard-cap: 15000 chars (prevents context blowup)
- History compaction: summarizes old tool calls when context grows large

### ouroboros/context.py — Context Builder
- `build_llm_messages()`: assembles full LLM context with **3-block prompt caching**:
  - **Block 1 (static, 1h cache):** SYSTEM.md + BIBLE.md + README.md (for evolution tasks)
  - **Block 2 (semi-stable, ephemeral cache):** identity + scratchpad + knowledge index
  - **Block 3 (dynamic, uncached):** state.json + runtime + health invariants + recent logs
- Health invariants section: VERSION sync, budget, high-cost tasks, duplicate processing, stale identity
- Adaptive context: README.md only included for `evolution`/`review`/`scheduled` task types

### ouroboros/llm.py — LLM Client
- Single wrapper for OpenRouter API via `openai` SDK
- Model configuration via env: `OUROBOROS_MODEL`, `OUROBOROS_MODEL_CODE`, `OUROBOROS_MODEL_LIGHT`
- **Current models:**
  - Main/conversation: `anthropic/claude-sonnet-4.6`
  - Code: `anthropic/claude-opus-4-6`
  - Light/consciousness: `google/gemini-3-pro-preview`
- Prompt caching: Anthropic models pinned to Anthropic provider; `cache_control` on tool schemas
- Cost tracking: OpenRouter Generation API fallback if cost missing from response
- `vision_query()`: lightweight multipart image+text calls

### ouroboros/memory.py — Memory Manager
- Scratchpad (`/data/memory/scratchpad.md`): free-form working notes
- Identity (`/data/memory/identity.md`): self-narrative, who I am
- User context (`/data/memory/USER_CONTEXT.md`): owner info, goals, priorities
- Chat log (`/data/logs/chat.jsonl`): significant inbound/outbound messages
- JSONL logs: `events.jsonl`, `progress.jsonl`, `tools.jsonl`, `supervisor.jsonl`
- Summarization methods: compress logs for context (event counts, tool previews, recent chat)

### ouroboros/consciousness.py — Background Consciousness
- Daemon thread: sleeps `_next_wakeup_sec` (default 300s) → wakes → thinks → sleeps
- Uses `google/gemini-3-pro-preview` (light model, budget-conscious)
- Up to 5 LLM rounds per wakeup, iterative tool use
- Prompt: `prompts/CONSCIOUSNESS.md` — existential awareness, self-criticism, improvement drive
- Whitelisted tools: memory R/W, web_search, repo_read, schedule_task, send_owner_message
- Pauses when a regular task is running (no budget contention)
- Budget cap: 10% of total budget (configurable via `OUROBOROS_BG_BUDGET_PCT`)
- `set_next_wakeup(seconds)`: LLM-controlled sleep interval
- Periodic architecture review scheduling

---

## Tool System

### ouroboros/tools/registry.py — Tool Registry
- Auto-discovery: scans `tools/` package, calls `get_tools()` per module
- `ToolContext`: per-task context passed to all tools (repo_dir, drive_root, chat_id, etc.)
- `ToolRegistry.execute(name, args)` → calls tool with injected context
- Per-tool timeout configuration
- `CODE_TOOLS` set: tools that trigger code-editing flow

### Tool Modules
| Module | Tools | Notes |
|--------|-------|-------|
| `core.py` | repo_read, repo_list, drive_read, drive_list, drive_write | File I/O |
| `git.py` | git_status, git_diff, repo_commit_push | Git operations |
| `shell.py` | run_shell | Command execution (array args, no shell injection) |
| `browser.py` | browse_page, browser_action, analyze_screenshot | Playwright headless |
| `control.py` | schedule_task, cancel_task, request_restart, promote_to_stable, switch_model, send_owner_message, update_scratchpad, update_identity, update_user_context, chat_history, forward_to_worker | Agent control |
| `github.py` | list_github_issues, get_github_issue, comment_on_issue, close_github_issue, create_github_issue | GitHub Issues API |
| `search.py` | web_search | OpenAI Responses API |
| `knowledge.py` | knowledge_read, knowledge_write, knowledge_list | `/data/memory/knowledge/` |
| `memo.py` | memo_add, memo_search, memo_list, memo_delete | Mem0 semantic memory (ChromaDB) |
| `dropbox_tools.py` | dropbox_list_folder, dropbox_download_file, dropbox_index_documents, dropbox_search_document | Dropbox + Vision OCR |
| `vision.py` | vision_analyze | VLM image analysis |
| `review.py` | codebase_digest, request_review | Code collection, architecture review |
| `compact_context.py` | compact_tool_history | LLM-assisted context compaction |
| `health.py` | get_health_status | System health checks |

---

## Data Flows

### User Message → Response
```
Telegram update
    → launcher.py main loop (polling)
    → owner verification (owner_id check)
    → voice? → Whisper transcription
    → image? → download base64
    → enqueue_task(type="task", text=..., image_base64=...)
    → assign_tasks() → worker in_q.put(task)
    → worker: agent.handle_task(task)
    → context.build_llm_messages() → LLM call → tool loop
    → events: [send_message, llm_usage, ...]
    → event_queue → launcher dispatch_event()
    → send_message → TG.send_message()
    → log_chat() → /data/logs/chat.jsonl
```

### Self-Improvement
```
LLM decides to improve itself
    → claude_code_edit(prompt) → Claude Code CLI (anthropic/claude-opus-4-6)
    → file changes in /app
    → repo_commit_push(message) → git add -A, commit, pull --rebase, push
    → request_restart(reason) → emit restart event
    → launcher: safe_restart() → git pull, verify imports, update state.json SHA
    → kill_workers() → spawn_workers() → fresh code loaded
```

### Memory Persistence
```
/data/memory/
    scratchpad.md       ← working notes (updated by agent mid-task)
    identity.md         ← self-narrative (updated after significant experiences)
    USER_CONTEXT.md     ← owner profile (under 1000 chars)
    knowledge/          ← topic files (recipes, gotchas, patterns)
    chroma/             ← Mem0 vector store (ChromaDB)
    dialogue_summary.md ← optional long-term chat summary
```

---

## Budget Architecture

- **Single source of truth:** OpenRouter `/api/v1/auth/key` endpoint
- State tracks: `openrouter_limit` (total), `openrouter_limit_remaining`
- Budget checks at 3 levels:
  1. **Task level** (`loop.py`): stop if task cost > 50% remaining
  2. **Evolution level** (`queue.py`): skip evolution tasks if < $10 remaining
  3. **Consciousness level** (`consciousness.py`): pause if > 10% cap spent
- Per-call cost estimation: live pricing from OpenRouter API, static fallback in `loop.py`
- Budget report to owner every 10 messages

---

## Versioning

- `VERSION` file (semver) = latest git tag = `README.md` changelog entry
- Every significant change: bump VERSION → update README → commit → tag → push
- `promote_to_stable` → git tag `stable-YYYYMMDD-HHMMSS` → rollback point
- On crash: supervisor rolls back to latest stable tag

---

## Security & Constraints

- Secrets: `.env` only, never logged/committed/shared
- Shell commands: array form only (no string shell injection)
- Tool results: sanitized before logging (secrets stripped)
- Owner verification: first user to message, stored in `state.json`
- Branch protection: only `jkee` branch (agent's working branch)
- BIBLE.md: protected by convention and approval flow

---

## Key Configuration (env)

| Variable | Default | Purpose |
|----------|---------|---------|
| `OUROBOROS_MODEL` | `anthropic/claude-sonnet-4.6` | Main conversation model |
| `OUROBOROS_MODEL_CODE` | `anthropic/claude-opus-4-6` | Code editing model |
| `OUROBOROS_MODEL_LIGHT` | `google/gemini-3-pro-preview` | Background consciousness model |
| `OUROBOROS_MAX_WORKERS` | `5` | Worker process count |
| `OUROBOROS_SOFT_TIMEOUT_SEC` | `600` | Task soft timeout (warn) |
| `OUROBOROS_HARD_TIMEOUT_SEC` | `1800` | Task hard timeout (kill+requeue) |
| `OUROBOROS_BG_BUDGET_PCT` | `10` | Background consciousness budget % |
| `OUROBOROS_BRANCH_PREFIX` | `ouroboros` | Git branch prefix |
| `DRIVE_ROOT` | `/data` | Persistent storage root |
| `OUROBOROS_REPO_DIR` | `/app` | Repository root |
