"""Session context tracking for read-before-write checks."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import find_svx_root

SESSION_TTL_SECONDS = 60 * 60 * 12
SESSION_FILE = "session.json"


@dataclass
class _SessionRecord:
    """Per-file read state kept inside a project .svx/session.json file."""

    seen_at: str
    mtime: float | None = None

    def to_dict(self) -> dict[str, object]:
        return {"seen_at": self.seen_at, "mtime": self.mtime}


def get_session_path(cwd: str | Path | None = None) -> Path | None:
    """Return .svx/session.json for the project containing cwd."""
    root = find_svx_root(Path(cwd) if cwd is not None else Path.cwd())
    if root is None:
        return None
    return root / ".svx" / SESSION_FILE


def record_file_read(target: str, cwd: str | Path | None = None) -> None:
    """Record that a file has been read in this project session."""
    path = _resolve_path(target, cwd)
    if not path.exists():
        return

    session_file = get_session_path(path)
    if session_file is None:
        return

    session_file.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    data = _load_session(session_file)
    reads = data.setdefault("reads", {})

    reads[str(path)] = _SessionRecord(
        seen_at=now,
        mtime=path.stat().st_mtime,
    ).to_dict()
    _prune_expired_reads(reads, max_age_sec=SESSION_TTL_SECONDS)

    _write_session(session_file, {"version": 1, "reads": reads})


def has_file_been_read(
    target: str,
    cwd: str | Path | None = None,
    max_age_sec: int = SESSION_TTL_SECONDS,
) -> bool:
    """Return True if target was read recently and is still current."""
    path = _resolve_path(target, cwd)
    session_file = get_session_path(path)
    if session_file is None or not session_file.exists() or not path.exists():
        return False

    data = _load_session(session_file)
    reads = data.get("reads", {})
    raw_record = reads.get(str(path))
    if not isinstance(raw_record, dict):
        return False

    seen_at = raw_record.get("seen_at")
    if not isinstance(seen_at, str):
        return False

    try:
        seen_time = datetime.fromisoformat(seen_at.replace("Z", "+00:00"))
    except ValueError:
        return False

    age_sec = (
        datetime.now(timezone.utc).replace(tzinfo=timezone.utc) - seen_time
    ).total_seconds()
    if age_sec > max_age_sec:
        return False

    recorded_mtime = raw_record.get("mtime")
    if recorded_mtime is not None:
        try:
            return abs(path.stat().st_mtime - float(recorded_mtime)) < 1e-3
        except (OSError, TypeError, ValueError):
            return False
    return True


def prune_stale_reads(
    cwd: str | Path | None = None,
    max_age_sec: int = SESSION_TTL_SECONDS,
) -> int:
    """Prune stale read records for a project and return number removed."""
    session_file = get_session_path(cwd)
    if session_file is None or not session_file.exists():
        return 0

    data = _load_session(session_file)
    reads = data.get("reads")
    if not isinstance(reads, dict):
        reads = {}
        data["reads"] = reads

    before = len(reads)
    _prune_expired_reads(reads, max_age_sec=max_age_sec)
    if len(reads) == before:
        return 0

    _write_session(session_file, {"version": data.get("version", 1), "reads": reads})
    return before - len(reads)


def _resolve_path(target: str, cwd: str | Path | None) -> Path:
    p = Path(target)
    if not p.is_absolute():
        p = Path(cwd or Path.cwd()) / p
    return p


def _load_session(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "reads": {}}
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "reads": {}}
    if not isinstance(data, dict):
        return {"version": 1, "reads": {}}
    if not isinstance(data.get("reads"), dict):
        data["reads"] = {}
    return data


def _write_session(path: Path, data: dict[str, Any]) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
    except OSError:
        # Session read tracking is non-blocking for safety.
        return


def _prune_expired_reads(
    reads: dict[str, dict[str, object]],
    max_age_sec: int = SESSION_TTL_SECONDS,
) -> None:
    """Mutate reads map, removing entries older than the session TTL."""
    now = datetime.now(timezone.utc).timestamp()
    expired = []
    for key, payload in list(reads.items()):
        if not isinstance(payload, dict):
            expired.append(key)
            continue
        seen_at = payload.get("seen_at")
        if not isinstance(seen_at, str):
            expired.append(key)
            continue
        try:
            seen_time = datetime.fromisoformat(seen_at.replace("Z", "+00:00")).timestamp()
        except ValueError:
            expired.append(key)
            continue
        if now - seen_time > max_age_sec:
            expired.append(key)
    for key in expired:
        reads.pop(key, None)
