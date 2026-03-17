"""Core types for svx."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class RiskLevel(Enum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Verdict(Enum):
    ALLOW = "allow"
    CONFIRM = "confirm"
    BLOCK = "block"


class Reversibility(Enum):
    REVERSIBLE = "reversible"
    PARTIALLY = "partially_reversible"
    IRREVERSIBLE = "irreversible"
    UNKNOWN = "unknown"


class CommandCategory(Enum):
    GIT = "git"
    FILE_DELETE = "file_delete"
    FILE_MOVE = "file_move"
    FILE_PERMISSION = "file_permission"
    FILE_EDIT = "file_edit"
    FILE_WRITE = "file_write"
    PACKAGE = "package"
    PROCESS = "process"
    NETWORK = "network"
    SHELL = "shell"
    UNKNOWN = "unknown"


@dataclass
class ParsedCommand:
    """A shell command parsed into structured form."""
    raw: str
    program: str
    subcommand: str | None = None
    args: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    category: CommandCategory = CommandCategory.UNKNOWN
    targets: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class WorldSnapshot:
    """Current state of the world relevant to a command."""
    cwd: str
    is_git_repo: bool = False
    git_branch: str | None = None
    git_dirty: bool = False
    git_untracked_count: int = 0
    git_staged_count: int = 0
    git_remote: str | None = None
    target_exists: dict[str, bool] = field(default_factory=dict)
    target_sizes: dict[str, int] = field(default_factory=dict)
    target_git_tracked: dict[str, bool] = field(default_factory=dict)
    target_line_count: dict[str, int] = field(default_factory=dict)
    target_is_config: dict[str, bool] = field(default_factory=dict)
    edit_old_string_found: bool | None = None
    edit_change_ratio: float = 0.0


@dataclass
class SimulationResult:
    """What we predict will happen if this command runs."""
    description: str
    effects: list[str] = field(default_factory=list)
    failure_modes: list[str] = field(default_factory=list)
    reversibility: Reversibility = Reversibility.UNKNOWN
    blast_radius: int = 0  # estimated number of files/objects affected
    data_loss_possible: bool = False


@dataclass
class VerificationResult:
    """Final safety assessment."""
    verdict: Verdict
    risk_level: RiskLevel
    simulation: SimulationResult
    reasons: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)


@dataclass
class AuditEntry:
    """A single logged event."""
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    command: str = ""
    parsed: dict[str, Any] = field(default_factory=dict)
    snapshot: dict[str, Any] = field(default_factory=dict)
    simulation: dict[str, Any] = field(default_factory=dict)
    verdict: str = ""
    risk_level: str = ""
    reasons: list[str] = field(default_factory=list)
