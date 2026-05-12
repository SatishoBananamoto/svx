"""Tests for read-session tracking used by read-before-write checks."""

import json
from pathlib import Path
from datetime import datetime, timedelta, timezone
import time

from svx.session import (
    get_session_path,
    prune_stale_reads,
    has_file_been_read,
    record_file_read,
)


def test_record_file_read_writes_session_entry(tmp_path):
    (tmp_path / ".svx").mkdir()
    target = tmp_path / "notes.txt"
    target.write_text("hello\n")

    record_file_read(target, cwd=tmp_path)
    assert has_file_been_read(target, cwd=tmp_path)

    session_file = get_session_path(tmp_path)
    assert session_file is not None
    assert session_file.exists()


def test_read_tracking_depends_on_mtime(tmp_path):
    (tmp_path / ".svx").mkdir()
    target = tmp_path / "notes.txt"
    target.write_text("hello\n")

    record_file_read(target, cwd=tmp_path)
    assert has_file_been_read(target, cwd=tmp_path)

    time.sleep(0.01)
    target.write_text("changed\n")
    assert not has_file_been_read(target, cwd=tmp_path)


def test_missing_targets_do_not_create_session(tmp_path):
    (tmp_path / ".svx").mkdir()
    missing = tmp_path / "does-not-exist.txt"
    record_file_read(missing, cwd=tmp_path)
    assert has_file_been_read(missing, cwd=tmp_path) is False
    assert get_session_path(tmp_path) is not None


def test_prune_stale_reads_removes_expired_entries(tmp_path):
    (tmp_path / ".svx").mkdir()
    target = tmp_path / "notes.txt"
    target.write_text("hello\n")

    record_file_read(target, cwd=tmp_path)
    session_path = get_session_path(tmp_path)
    assert session_path is not None

    data = json.loads(session_path.read_text())
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    data["reads"][str(target)]["seen_at"] = old
    session_path.write_text(json.dumps(data))

    removed = prune_stale_reads(tmp_path, max_age_sec=60 * 60)
    assert removed == 1
    data_after = json.loads(session_path.read_text())
    assert str(target) not in data_after.get("reads", {})


def test_prune_stale_reads_noop_when_fresh(tmp_path):
    (tmp_path / ".svx").mkdir()
    target = tmp_path / "notes.txt"
    target.write_text("hello\n")

    record_file_read(target, cwd=tmp_path)
    removed = prune_stale_reads(tmp_path, max_age_sec=60 * 60 * 24)
    assert removed == 0

    session_path = get_session_path(tmp_path)
    assert session_path is not None
    data = json.loads(session_path.read_text())
    assert str(target) in data.get("reads", {})
