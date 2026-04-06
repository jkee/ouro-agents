"""Tests for the cron/scheduler system (supervisor/cron.py)."""

from __future__ import annotations

import datetime
import json
import pathlib

import pytest

from supervisor import cron


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def cron_tmp(tmp_path):
    """Init cron module with a temp drive root for every test."""
    cron.init(tmp_path)
    return tmp_path


# ---------------------------------------------------------------------------
# Store CRUD
# ---------------------------------------------------------------------------

def test_load_crons_empty():
    """Loading from non-existent file returns empty list."""
    assert cron.load_crons() == []


def test_add_cron_valid():
    """Adding a cron with valid expression persists to disk."""
    entry = cron.add_cron("*/5 * * * *", "test task")
    assert entry["expression"] == "*/5 * * * *"
    assert entry["description"] == "test task"
    assert entry["enabled"] is True
    assert entry["fire_count"] == 0
    assert entry["last_fired_at"] is None
    assert len(entry["id"]) == 8

    # Persisted
    loaded = cron.load_crons()
    assert len(loaded) == 1
    assert loaded[0]["id"] == entry["id"]


def test_add_cron_invalid_expression():
    """Adding a cron with invalid expression raises ValueError."""
    with pytest.raises(ValueError, match="Invalid cron expression"):
        cron.add_cron("not a cron", "bad task")


def test_add_cron_empty_expression():
    with pytest.raises(ValueError, match="cannot be empty"):
        cron.add_cron("", "task")


def test_add_cron_empty_description():
    with pytest.raises(ValueError, match="cannot be empty"):
        cron.add_cron("* * * * *", "")


@pytest.mark.parametrize("alias", ["@hourly", "@daily", "@weekly", "@monthly"])
def test_add_cron_aliases(alias):
    """Standard aliases are accepted by croniter."""
    entry = cron.add_cron(alias, f"test {alias}")
    assert entry["expression"] == alias


def test_add_multiple_crons():
    cron.add_cron("0 * * * *", "hourly")
    cron.add_cron("0 0 * * *", "daily")
    assert len(cron.load_crons()) == 2


def test_remove_cron_exists():
    entry = cron.add_cron("*/10 * * * *", "to remove")
    assert cron.remove_cron(entry["id"]) is True
    assert cron.load_crons() == []


def test_remove_cron_not_found():
    assert cron.remove_cron("nonexist") is False


def test_remove_preserves_others():
    e1 = cron.add_cron("0 * * * *", "keep")
    e2 = cron.add_cron("0 0 * * *", "remove")
    cron.remove_cron(e2["id"])
    loaded = cron.load_crons()
    assert len(loaded) == 1
    assert loaded[0]["id"] == e1["id"]


def test_toggle_cron_disable():
    entry = cron.add_cron("0 * * * *", "toggle me")
    result = cron.toggle_cron(entry["id"], enabled=False)
    assert result["enabled"] is False
    # Persisted
    loaded = cron.load_crons()
    assert loaded[0]["enabled"] is False


def test_toggle_cron_flip():
    entry = cron.add_cron("0 * * * *", "toggle me")
    cron.toggle_cron(entry["id"], enabled=False)
    result = cron.toggle_cron(entry["id"], enabled=None)
    assert result["enabled"] is True


def test_toggle_cron_not_found():
    assert cron.toggle_cron("nonexist") is None


def test_list_crons():
    cron.add_cron("0 * * * *", "one")
    cron.add_cron("0 0 * * *", "two")
    result = cron.list_crons()
    assert len(result) == 2


# ---------------------------------------------------------------------------
# Due-checking (_is_due)
# ---------------------------------------------------------------------------

def test_is_due_never_fired():
    """A cron created >1h ago with @hourly should be due."""
    now = datetime.datetime(2026, 3, 19, 12, 0, 0, tzinfo=datetime.timezone.utc)
    entry = {
        "expression": "@hourly",
        "created_at": (now - datetime.timedelta(hours=2)).isoformat(),
        "last_fired_at": None,
    }
    assert cron._is_due(entry, now) is True


def test_is_not_due_recently_fired():
    """A cron that just fired should not be due again."""
    now = datetime.datetime(2026, 3, 19, 12, 5, 0, tzinfo=datetime.timezone.utc)
    entry = {
        "expression": "@hourly",
        "created_at": (now - datetime.timedelta(hours=2)).isoformat(),
        "last_fired_at": (now - datetime.timedelta(minutes=5)).isoformat(),
    }
    assert cron._is_due(entry, now) is False


def test_is_due_after_interval():
    """A cron with last_fired_at older than interval should be due."""
    now = datetime.datetime(2026, 3, 19, 14, 0, 0, tzinfo=datetime.timezone.utc)
    entry = {
        "expression": "@hourly",
        "created_at": (now - datetime.timedelta(hours=5)).isoformat(),
        "last_fired_at": (now - datetime.timedelta(hours=1, minutes=1)).isoformat(),
    }
    assert cron._is_due(entry, now) is True


def test_is_due_specific_minute():
    """5-field expression: '30 * * * *' fires at minute 30."""
    now = datetime.datetime(2026, 3, 19, 12, 31, 0, tzinfo=datetime.timezone.utc)
    entry = {
        "expression": "30 * * * *",
        "created_at": (now - datetime.timedelta(hours=1)).isoformat(),
        "last_fired_at": (now - datetime.timedelta(hours=1)).isoformat(),
    }
    assert cron._is_due(entry, now) is True


def test_is_not_due_before_specific_minute():
    """'30 * * * *' should NOT fire at minute 15."""
    now = datetime.datetime(2026, 3, 19, 12, 15, 0, tzinfo=datetime.timezone.utc)
    entry = {
        "expression": "30 * * * *",
        "created_at": (now - datetime.timedelta(minutes=20)).isoformat(),
        "last_fired_at": (now - datetime.timedelta(minutes=20)).isoformat(),
    }
    assert cron._is_due(entry, now) is False


# ---------------------------------------------------------------------------
# check_and_enqueue_due_crons integration
# ---------------------------------------------------------------------------

def _enqueue_collector():
    """Returns (enqueue_fn, collected_tasks_list)."""
    tasks = []

    def enqueue(task):
        tasks.append(task)
        return task
    return enqueue, tasks


def test_check_enqueue_fires_due_cron():
    """Full integration: add cron, mock time forward, verify enqueue called."""
    entry = cron.add_cron("@hourly", "hourly task")

    # Manually backdate created_at so it's due
    crons = cron.load_crons()
    past = datetime.datetime(2026, 3, 19, 10, 0, 0, tzinfo=datetime.timezone.utc)
    crons[0]["created_at"] = past.isoformat()
    cron.save_crons(crons)

    enqueue_fn, tasks = _enqueue_collector()
    now = datetime.datetime(2026, 3, 19, 12, 0, 0, tzinfo=datetime.timezone.utc)

    fired = cron.check_and_enqueue_due_crons(
        running={},
        enqueue_fn=enqueue_fn, owner_chat_id=12345,
        budget_remaining=100.0, _now=now,
    )

    assert fired == 1
    assert len(tasks) == 1
    assert tasks[0]["text"] == "hourly task"
    assert tasks[0]["type"] == "cron"
    assert tasks[0]["chat_id"] == 12345
    assert tasks[0]["cron_id"] == entry["id"]

    # Verify last_fired_at was updated
    updated = cron.load_crons()
    assert updated[0]["last_fired_at"] is not None
    assert updated[0]["fire_count"] == 1


def test_check_enqueue_disabled_cron_skipped():
    cron.add_cron("@hourly", "disabled task")
    crons = cron.load_crons()
    past = datetime.datetime(2026, 3, 19, 10, 0, 0, tzinfo=datetime.timezone.utc)
    crons[0]["created_at"] = past.isoformat()
    crons[0]["enabled"] = False
    cron.save_crons(crons)

    enqueue_fn, tasks = _enqueue_collector()
    now = datetime.datetime(2026, 3, 19, 12, 0, 0, tzinfo=datetime.timezone.utc)

    fired = cron.check_and_enqueue_due_crons(
        running={},
        enqueue_fn=enqueue_fn, owner_chat_id=12345,
        budget_remaining=100.0, _now=now,
    )

    assert fired == 0
    assert len(tasks) == 0


def test_check_enqueue_overlap_prevention():
    """If last_task_id is in RUNNING, cron is skipped."""
    cron.add_cron("@hourly", "overlap test")
    crons = cron.load_crons()
    past = datetime.datetime(2026, 3, 19, 10, 0, 0, tzinfo=datetime.timezone.utc)
    crons[0]["created_at"] = past.isoformat()
    crons[0]["last_task_id"] = "running123"
    cron.save_crons(crons)

    enqueue_fn, tasks = _enqueue_collector()
    running = {"running123": {"task": {}, "started_at": 0}}
    now = datetime.datetime(2026, 3, 19, 12, 0, 0, tzinfo=datetime.timezone.utc)

    fired = cron.check_and_enqueue_due_crons(
        running=running,
        enqueue_fn=enqueue_fn, owner_chat_id=12345,
        budget_remaining=100.0, _now=now,
    )

    assert fired == 0


def test_check_enqueue_overlap_cleared():
    """If last_task_id is NOT in RUNNING, cron fires normally."""
    cron.add_cron("@hourly", "cleared test")
    crons = cron.load_crons()
    past = datetime.datetime(2026, 3, 19, 10, 0, 0, tzinfo=datetime.timezone.utc)
    crons[0]["created_at"] = past.isoformat()
    crons[0]["last_task_id"] = "done123"
    cron.save_crons(crons)

    enqueue_fn, tasks = _enqueue_collector()
    running = {}  # done123 not in running
    now = datetime.datetime(2026, 3, 19, 12, 0, 0, tzinfo=datetime.timezone.utc)

    fired = cron.check_and_enqueue_due_crons(
        running=running,
        enqueue_fn=enqueue_fn, owner_chat_id=12345,
        budget_remaining=100.0, _now=now,
    )

    assert fired == 1


def test_check_enqueue_low_budget_skips():
    """When budget < reserve, no crons fire."""
    cron.add_cron("@hourly", "budget test")
    crons = cron.load_crons()
    past = datetime.datetime(2026, 3, 19, 10, 0, 0, tzinfo=datetime.timezone.utc)
    crons[0]["created_at"] = past.isoformat()
    cron.save_crons(crons)

    enqueue_fn, tasks = _enqueue_collector()
    fired = cron.check_and_enqueue_due_crons(
        running={},
        enqueue_fn=enqueue_fn, owner_chat_id=12345,
        budget_remaining=1.0,  # Below EVOLUTION_BUDGET_RESERVE
    )
    assert fired == 0


def test_check_enqueue_no_owner():
    """No owner_chat_id = no crons fire."""
    cron.add_cron("@hourly", "no owner test")
    enqueue_fn, tasks = _enqueue_collector()
    fired = cron.check_and_enqueue_due_crons(
        running={},
        enqueue_fn=enqueue_fn, owner_chat_id=0,
        budget_remaining=100.0,
    )
    assert fired == 0


def test_check_enqueue_empty_crons():
    """No crons = immediate return 0."""
    enqueue_fn, tasks = _enqueue_collector()
    fired = cron.check_and_enqueue_due_crons(
        running={},
        enqueue_fn=enqueue_fn, owner_chat_id=12345,
        budget_remaining=100.0,
    )
    assert fired == 0


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_corrupt_crons_file(cron_tmp):
    """Corrupt JSON falls back to empty list."""
    cron.CRONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    cron.CRONS_PATH.write_text("not json at all{{{", encoding="utf-8")
    assert cron.load_crons() == []


def test_crons_file_wrong_type(cron_tmp):
    """Non-dict JSON falls back to empty list."""
    cron.CRONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    cron.CRONS_PATH.write_text("[1, 2, 3]", encoding="utf-8")
    assert cron.load_crons() == []


def test_crons_file_missing_key(cron_tmp):
    """Dict without 'crons' key falls back to empty list."""
    cron.CRONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    cron.CRONS_PATH.write_text('{"other": 1}', encoding="utf-8")
    assert cron.load_crons() == []


def test_notify_flag_persisted():
    entry = cron.add_cron("@daily", "with notify", notify=True)
    loaded = cron.load_crons()
    assert loaded[0]["notify"] is True
