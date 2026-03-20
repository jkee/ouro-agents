"""
Supervisor — Bootstrap and first-run initialization.

Repo setup, first-run init (Bible §18), stale file cleanup.
Extracted from launcher.py.
"""

from __future__ import annotations

import logging
import pathlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from supervisor.config import Config

log = logging.getLogger(__name__)


def clean_stale_owner_mailbox(drive_root: pathlib.Path) -> None:
    """Clear stale owner mailbox files from previous session."""
    try:
        from ouro.owner_inject import get_pending_path
        stale_inject = get_pending_path(drive_root)
        if stale_inject.exists():
            stale_inject.unlink(missing_ok=True)
        mailbox_dir = drive_root / "memory" / "owner_mailbox"
        if mailbox_dir.exists():
            for f in mailbox_dir.iterdir():
                f.unlink(missing_ok=True)
    except Exception:
        pass


def first_run_init(cfg: "Config") -> None:
    """First-run initialization (Bible section 18).

    Creates improvements-log/, installs find-skills skill,
    commits and pushes init files.
    """
    import subprocess as sp
    from supervisor.state import load_state, save_state

    st = load_state()
    if st.get("initialized"):
        return

    log.info("First-run initialization (Bible section 18)")

    # Ensure improvements-log/ directory exists
    implog_dir = cfg.repo_dir / "improvements-log"
    implog_dir.mkdir(parents=True, exist_ok=True)
    (implog_dir / ".gitkeep").touch(exist_ok=True)

    # Pre-install find-skills skill (Agent Skills format)
    skills_dir = cfg.repo_dir / ".agents" / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    if not (skills_dir / "find-skills").exists():
        try:
            sp.run(
                ["npx", "-y", "skills", "add",
                 "https://github.com/vercel-labs/skills", "--skill", "find-skills"],
                cwd=str(cfg.repo_dir), timeout=60,
                capture_output=True, text=True,
            )
            log.info("Pre-installed find-skills skill")
        except Exception:
            log.warning("Failed to pre-install find-skills skill", exc_info=True)

    # Commit and push init files so workers don't see untracked files
    try:
        sp.run(["git", "add", "improvements-log/", ".agents/"],
                cwd=str(cfg.repo_dir), timeout=10, check=True)
        diff = sp.run(["git", "diff", "--cached", "--quiet"],
                      cwd=str(cfg.repo_dir), timeout=10)
        if diff.returncode != 0:
            sp.run(["git", "commit", "-m", "init: add improvements-log, agent skills"],
                    cwd=str(cfg.repo_dir), timeout=30, check=True)
            sp.run(["git", "push", "origin", cfg.branch_dev],
                    cwd=str(cfg.repo_dir), timeout=60, check=True)
            log.info("First-run init files committed and pushed")
        else:
            log.info("First-run init files already present, nothing to commit")
    except Exception:
        log.warning("Failed to commit/push init files (will be picked up later)", exc_info=True)

    st["initialized"] = True
    save_state(st)
    log.info("First-run initialization complete")
