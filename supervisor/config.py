"""
Supervisor — Configuration and secrets.

Pure config: reads environment, parses values, no side effects beyond env normalization.
Extracted from launcher.py to enable isolated testing.
"""

from __future__ import annotations

import os
import pathlib
from dataclasses import dataclass, field
from typing import Optional


def get_secret(name: str, default: Optional[str] = None, required: bool = False) -> Optional[str]:
    v = os.environ.get(name, default)
    if v is not None and str(v).strip() == "":
        v = default
    if required:
        assert v is not None and str(v).strip() != "", f"Missing required secret: {name}"
    return v


def get_cfg(name: str, default: Optional[str] = None) -> Optional[str]:
    v = os.environ.get(name)
    if v is not None and str(v).strip() != "":
        return v
    return default


def _parse_int_cfg(raw: Optional[str], default: int, minimum: int = 0) -> int:
    try:
        val = int(str(raw))
    except Exception:
        val = default
    return max(minimum, val)


@dataclass
class Config:
    """All runtime configuration for the supervisor."""

    # Secrets
    openrouter_api_key: str = ""
    telegram_bot_token: str = ""
    github_token: str = ""
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    composio_api_key: str = ""

    # GitHub
    github_user: str = ""
    github_repo: str = ""

    # Models
    model_main: str = "anthropic/claude-sonnet-4.6"
    model_code: str = "anthropic/claude-opus-4-6"
    model_light: str = ""

    # Workers
    max_workers: int = 5
    soft_timeout_sec: int = 600
    hard_timeout_sec: int = 1800

    # Diagnostics
    budget_report_every_messages: int = 10
    diag_heartbeat_sec: int = 30
    diag_slow_cycle_sec: int = 20

    # Paths
    drive_root: pathlib.Path = field(default_factory=lambda: pathlib.Path("/data"))
    repo_dir: pathlib.Path = field(default_factory=lambda: pathlib.Path("/app"))

    # Git
    branch_prefix: str = ""
    branch_dev: str = ""
    branch_stable: str = ""
    remote_url: str = ""

    @classmethod
    def from_env(cls) -> "Config":
        """Build Config from environment variables."""
        from ouro.llm import DEFAULT_LIGHT_MODEL

        openrouter_api_key = get_secret("OPENROUTER_API_KEY", required=True) or ""
        telegram_bot_token = get_secret("TELEGRAM_BOT_TOKEN", required=True) or ""
        github_token = get_secret("GITHUB_TOKEN", required=True) or ""
        openai_api_key = get_secret("OPENAI_API_KEY", default="") or ""
        anthropic_api_key = get_secret("ANTHROPIC_API_KEY", required=True) or ""
        composio_api_key = get_secret("COMPOSIO_API_KEY", required=True) or ""

        github_user = get_cfg("GITHUB_USER") or ""
        github_repo = get_cfg("GITHUB_REPO") or ""
        assert github_user.strip(), "GITHUB_USER not set. Add it to your .env file."
        assert github_repo.strip(), "GITHUB_REPO not set. Add it to your .env file."

        max_workers = int(get_cfg("OURO_MAX_WORKERS", default="5") or "5")
        model_main = get_cfg("OURO_MODEL", default="anthropic/claude-sonnet-4.6") or "anthropic/claude-sonnet-4.6"
        model_code = get_cfg("OURO_MODEL_CODE", default="anthropic/claude-opus-4-6") or "anthropic/claude-opus-4-6"
        model_light = get_cfg("OURO_MODEL_LIGHT", default=DEFAULT_LIGHT_MODEL) or ""

        soft_timeout_sec = max(60, int(get_cfg("OURO_SOFT_TIMEOUT_SEC", default="600") or "600"))
        hard_timeout_sec = max(120, int(get_cfg("OURO_HARD_TIMEOUT_SEC", default="1800") or "1800"))
        diag_heartbeat_sec = _parse_int_cfg(get_cfg("OURO_DIAG_HEARTBEAT_SEC", default="30"), default=30, minimum=0)
        diag_slow_cycle_sec = _parse_int_cfg(get_cfg("OURO_DIAG_SLOW_CYCLE_SEC", default="20"), default=20, minimum=0)

        drive_root = pathlib.Path(os.environ.get("DRIVE_ROOT", "/data")).resolve()
        repo_dir = pathlib.Path(os.environ.get("OURO_REPO_DIR", "/app")).resolve()

        branch_prefix = get_cfg("OURO_BRANCH_PREFIX") or ""
        assert branch_prefix.strip(), "OURO_BRANCH_PREFIX not set. Add it to your .env file."
        branch_dev = branch_prefix
        branch_stable = f"{branch_prefix}-stable"
        remote_url = f"https://{github_token}:x-oauth-basic@github.com/{github_user}/{github_repo}.git"

        return cls(
            openrouter_api_key=openrouter_api_key,
            telegram_bot_token=telegram_bot_token,
            github_token=github_token,
            openai_api_key=openai_api_key,
            anthropic_api_key=anthropic_api_key,
            composio_api_key=composio_api_key,
            github_user=github_user,
            github_repo=github_repo,
            model_main=model_main,
            model_code=model_code,
            model_light=model_light,
            max_workers=max_workers,
            soft_timeout_sec=soft_timeout_sec,
            hard_timeout_sec=hard_timeout_sec,
            budget_report_every_messages=10,
            diag_heartbeat_sec=diag_heartbeat_sec,
            diag_slow_cycle_sec=diag_slow_cycle_sec,
            drive_root=drive_root,
            repo_dir=repo_dir,
            branch_prefix=branch_prefix,
            branch_dev=branch_dev,
            branch_stable=branch_stable,
            remote_url=remote_url,
        )

    def export_to_env(self) -> None:
        """Write config values back to os.environ for child processes."""
        os.environ["OPENROUTER_API_KEY"] = self.openrouter_api_key
        os.environ["OPENAI_API_KEY"] = self.openai_api_key
        os.environ["ANTHROPIC_API_KEY"] = self.anthropic_api_key
        os.environ["GITHUB_USER"] = self.github_user
        os.environ["GITHUB_REPO"] = self.github_repo
        os.environ["OURO_MODEL"] = self.model_main
        os.environ["OURO_MODEL_CODE"] = self.model_code
        if self.model_light:
            os.environ["OURO_MODEL_LIGHT"] = self.model_light
        os.environ["OURO_DIAG_HEARTBEAT_SEC"] = str(self.diag_heartbeat_sec)
        os.environ["OURO_DIAG_SLOW_CYCLE_SEC"] = str(self.diag_slow_cycle_sec)
        os.environ["TELEGRAM_BOT_TOKEN"] = self.telegram_bot_token
        os.environ["OURO_BRANCH_PREFIX"] = self.branch_prefix

    def ensure_directories(self) -> None:
        """Create required data directories."""
        for sub in ["state", "logs", "memory", "index", "locks", "archive"]:
            (self.drive_root / sub).mkdir(parents=True, exist_ok=True)
        self.repo_dir.mkdir(parents=True, exist_ok=True)

        # Ensure chat log exists
        chat_log = self.drive_root / "logs" / "chat.jsonl"
        if not chat_log.exists():
            chat_log.write_text("", encoding="utf-8")
