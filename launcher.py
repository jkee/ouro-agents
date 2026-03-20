# ============================
# Ouro — Runtime launcher (entry point for Docker VPS)
# ============================
# Thin orchestrator: config, bootstrap, main loop.
# Heavy logic lives in supervisor/ package.

import datetime
import logging
import os
import sys
import threading
import time
import types

from typing import Optional

log = logging.getLogger(__name__)

# ----------------------------
# 0) Load .env file + patches
# ----------------------------
from dotenv import load_dotenv
load_dotenv()

from ouro.apply_patch import install as install_apply_patch
install_apply_patch()

# ----------------------------
# 1) Config
# ----------------------------
from supervisor.config import Config

cfg = Config.from_env()
cfg.export_to_env()
cfg.ensure_directories()

# ----------------------------
# 2) Clean stale files
# ----------------------------
from supervisor.bootstrap import clean_stale_owner_mailbox
clean_stale_owner_mailbox(cfg.drive_root)

# ----------------------------
# 3) Initialize supervisor modules
# ----------------------------
from supervisor.state import (
    init as state_init, load_state, save_state, append_jsonl,
    update_budget_from_usage, status_text, rotate_chat_log_if_needed,
    init_state, openrouter_budget_remaining,
)
state_init(cfg.drive_root)
init_state()

from supervisor.telegram import (
    init as telegram_init, TelegramClient, send_with_budget, log_chat,
)
TG = TelegramClient(str(cfg.telegram_bot_token))
telegram_init(
    drive_root=cfg.drive_root,
    budget_report_every=cfg.budget_report_every_messages,
    tg_client=TG,
)

from supervisor.git_ops import (
    init as git_ops_init, ensure_repo_present, safe_restart,
)
git_ops_init(
    repo_dir=cfg.repo_dir, drive_root=cfg.drive_root, remote_url=cfg.remote_url,
    branch_dev=cfg.branch_dev, branch_stable=cfg.branch_stable,
)

from supervisor.queue import (
    enqueue_task, persist_queue_snapshot, restore_pending_from_snapshot,
    cancel_task_by_id, queue_review_task, sort_pending, _queue_lock,
)

from supervisor.workers import (
    init as workers_init, get_event_q, WORKERS, PENDING, RUNNING,
    spawn_workers, kill_workers, _get_chat_agent, auto_resume_after_restart,
)
workers_init(
    repo_dir=cfg.repo_dir, drive_root=cfg.drive_root, max_workers=cfg.max_workers,
    soft_timeout=cfg.soft_timeout_sec, hard_timeout=cfg.hard_timeout_sec,
    branch_dev=cfg.branch_dev, branch_stable=cfg.branch_stable,
)

from supervisor.cron import init as cron_init
cron_init(cfg.drive_root)

# ----------------------------
# 4) Bootstrap repo
# ----------------------------
ensure_repo_present()
ok, msg = safe_restart(reason="bootstrap", unsynced_policy="rescue_and_reset")
assert ok, f"Bootstrap failed: {msg}"

# ----------------------------
# 5) First-run initialization
# ----------------------------
from supervisor.bootstrap import first_run_init
first_run_init(cfg)

# ----------------------------
# 6) Record launch time & start workers
# ----------------------------
_launch_st = load_state()
_launch_st["launched_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
save_state(_launch_st)

kill_workers()
spawn_workers(cfg.max_workers)
restored_pending = restore_pending_from_snapshot()
persist_queue_snapshot(reason="startup")
if restored_pending > 0:
    st_boot = load_state()
    if st_boot.get("owner_chat_id"):
        send_with_budget(int(st_boot["owner_chat_id"]),
                         f"\u267b\ufe0f Restored pending queue from snapshot: {restored_pending} tasks.")

append_jsonl(cfg.drive_root / "logs" / "supervisor.jsonl", {
    "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "type": "launcher_start",
    "branch": load_state().get("current_branch"),
    "sha": load_state().get("current_sha"),
    "max_workers": cfg.max_workers,
    "model_default": cfg.model_main, "model_code": cfg.model_code, "model_light": cfg.model_light,
    "soft_timeout_sec": cfg.soft_timeout_sec, "hard_timeout_sec": cfg.hard_timeout_sec,
    "worker_start_method": str(os.environ.get("OURO_WORKER_START_METHOD") or ""),
    "diag_heartbeat_sec": cfg.diag_heartbeat_sec,
    "diag_slow_cycle_sec": cfg.diag_slow_cycle_sec,
})

# ----------------------------
# 6.1) Auto-resume after restart
# ----------------------------
auto_resume_after_restart()

# ----------------------------
# 6.2) Direct-mode watchdog
# ----------------------------
def _chat_watchdog_loop():
    """Monitor direct-mode chat agent for hangs. Runs as daemon thread."""
    soft_warned = False
    while True:
        time.sleep(30)
        try:
            agent = _get_chat_agent()
            if not agent._busy:
                soft_warned = False
                continue

            now = time.time()
            idle_sec = now - agent._last_progress_ts
            total_sec = now - agent._task_started_ts

            if idle_sec >= cfg.hard_timeout_sec:
                st = load_state()
                if st.get("owner_chat_id"):
                    send_with_budget(
                        int(st["owner_chat_id"]),
                        f"\u26a0\ufe0f Task stuck ({int(total_sec)}s without progress). "
                        f"Restarting agent.",
                    )
                reset_chat_agent()
                soft_warned = False
                continue

            if idle_sec >= cfg.soft_timeout_sec and not soft_warned:
                soft_warned = True
                st = load_state()
                if st.get("owner_chat_id"):
                    send_with_budget(
                        int(st["owner_chat_id"]),
                        f"\u23f1\ufe0f Task running for {int(total_sec)}s, "
                        f"last progress {int(idle_sec)}s ago. Continuing.",
                    )
        except Exception:
            log.debug("Failed to check/notify chat watchdog", exc_info=True)


def reset_chat_agent():
    """Reset the direct-mode chat agent (called by watchdog on hangs)."""
    import supervisor.workers as _w
    _w._chat_agent = None


_watchdog_thread = threading.Thread(target=_chat_watchdog_loop, daemon=True)
_watchdog_thread.start()

# ----------------------------
# 6.3) Background consciousness
# ----------------------------
from ouro.consciousness import BackgroundConsciousness


def _get_owner_chat_id() -> Optional[int]:
    try:
        st = load_state()
        cid = st.get("owner_chat_id")
        return int(cid) if cid else None
    except Exception:
        return None


_consciousness = BackgroundConsciousness(
    drive_root=cfg.drive_root,
    repo_dir=cfg.repo_dir,
    event_queue=get_event_q(),
    owner_chat_id_fn=_get_owner_chat_id,
)

# ----------------------------
# 7) Build event context & run main loop
# ----------------------------
_event_ctx = types.SimpleNamespace(
    DRIVE_ROOT=cfg.drive_root,
    REPO_DIR=cfg.repo_dir,
    BRANCH_DEV=cfg.branch_dev,
    BRANCH_STABLE=cfg.branch_stable,
    TG=TG,
    WORKERS=WORKERS,
    PENDING=PENDING,
    RUNNING=RUNNING,
    MAX_WORKERS=cfg.max_workers,
    send_with_budget=send_with_budget,
    load_state=load_state,
    save_state=save_state,
    update_budget_from_usage=update_budget_from_usage,
    append_jsonl=append_jsonl,
    enqueue_task=enqueue_task,
    cancel_task_by_id=cancel_task_by_id,
    queue_review_task=queue_review_task,
    persist_queue_snapshot=persist_queue_snapshot,
    safe_restart=safe_restart,
    kill_workers=kill_workers,
    spawn_workers=spawn_workers,
    sort_pending=sort_pending,
    consciousness=_consciousness,
    # Used by commands.py
    get_chat_agent=_get_chat_agent,
    reset_chat_agent=reset_chat_agent,
    status_text=status_text,
)

# Auto-start background consciousness (default: always on)
try:
    _consciousness.start()
    log.info("\U0001f9e0 Background consciousness auto-started (default: always on)")
except Exception as e:
    log.warning("consciousness auto-start failed: %s", e)

# Run the main loop
from supervisor.main_loop import Supervisor

supervisor = Supervisor(
    cfg=cfg,
    tg=TG,
    consciousness=_consciousness,
    event_ctx=_event_ctx,
)
supervisor.load_offset()
supervisor.run()
