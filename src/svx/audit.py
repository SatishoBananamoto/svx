"""Structured audit logging with provenance."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from .schemas import (
    AuditEntry,
    ParsedCommand,
    WorldSnapshot,
    VerificationResult,
)


def get_audit_dir(audit_dir: Path | None = None) -> Path:
    """Return the configured audit directory."""
    if audit_dir is not None:
        return audit_dir
    if configured := os.environ.get("SVX_AUDIT_DIR"):
        return Path(configured).expanduser()
    return Path.home() / ".svx-audit"


def _write_entry(log_file: Path, entry: AuditEntry) -> Path:
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(entry), default=str) + "\n")
    return log_file


def log_event(
    cmd: ParsedCommand,
    snap: WorldSnapshot,
    result: VerificationResult,
    audit_dir: Path | None = None,
    auto_allowed: bool = False,
) -> Path:
    """Write an audit entry to the log file. Returns the log file path."""
    entry = AuditEntry(
        timestamp=datetime.now(timezone.utc).isoformat(),
        command=cmd.raw,
        parsed={
            "program": cmd.program,
            "subcommand": cmd.subcommand,
            "category": cmd.category.value,
            "targets": cmd.targets,
            "flags": cmd.flags,
        },
        snapshot={
            "cwd": snap.cwd,
            "is_git_repo": snap.is_git_repo,
            "git_branch": snap.git_branch,
            "git_dirty": snap.git_dirty,
        },
        simulation={
            "description": result.simulation.description,
            "effects": result.simulation.effects,
            "reversibility": result.simulation.reversibility.value,
            "blast_radius": result.simulation.blast_radius,
            "data_loss_possible": result.simulation.data_loss_possible,
        },
        verdict=result.verdict.value,
        risk_level=result.risk_level.value,
        reasons=result.reasons,
        auto_allowed=auto_allowed,
        deny_kind=result.deny_kind.value if result.deny_kind else None,
        advisory_action=result.advisory_action,
    )

    preferred = get_audit_dir(audit_dir)
    fallback = Path(tempfile.gettempdir()) / "svx-audit"
    tried = set()

    for directory in (preferred, fallback):
        if directory in tried:
            continue
        tried.add(directory)
        log_file = directory / "audit.jsonl"
        try:
            directory.mkdir(parents=True, exist_ok=True)
            return _write_entry(log_file, entry)
        except OSError:
            continue

    return preferred / "audit.jsonl"
