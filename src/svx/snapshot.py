"""Capture current world state relevant to a command."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from .schemas import ParsedCommand, WorldSnapshot, CommandCategory


def capture(cmd: ParsedCommand, cwd: str | None = None) -> WorldSnapshot:
    """Build a snapshot of the current world state relevant to this command."""
    cwd = cwd or os.getcwd()
    snap = WorldSnapshot(cwd=cwd)

    # Git state
    snap.is_git_repo = _is_git_repo(cwd)
    if snap.is_git_repo:
        snap.git_branch = _git_current_branch(cwd)
        snap.git_dirty = _git_is_dirty(cwd)
        snap.git_untracked_count = _git_untracked_count(cwd)
        snap.git_staged_count = _git_staged_count(cwd)
        snap.git_remote = _git_remote(cwd)

    # Target-specific state
    if cmd.category in (
        CommandCategory.FILE_DELETE,
        CommandCategory.FILE_MOVE,
        CommandCategory.FILE_PERMISSION,
    ):
        for target in cmd.targets:
            path = _resolve_path(target, cwd)
            snap.target_exists[target] = path.exists()
            if path.exists():
                snap.target_sizes[target] = _get_size(path)
                if snap.is_git_repo:
                    snap.target_git_tracked[target] = _is_git_tracked(
                        target, cwd
                    )

    # File edit/write state
    if cmd.category in (CommandCategory.FILE_EDIT, CommandCategory.FILE_WRITE):
        for target in cmd.targets:
            path = _resolve_path(target, cwd)
            snap.target_exists[target] = path.exists()
            snap.target_is_config[target] = _is_config_file(target)

            if path.exists():
                snap.target_sizes[target] = _get_size(path)
                if snap.is_git_repo:
                    snap.target_git_tracked[target] = _is_git_tracked(
                        target, cwd
                    )

                # Read file content for edit analysis
                if cmd.category == CommandCategory.FILE_EDIT:
                    try:
                        content = path.read_text(errors="replace")
                        lines = content.splitlines()
                        snap.target_line_count[target] = len(lines)

                        old_string = cmd.metadata.get("old_string", "")
                        new_string = cmd.metadata.get("new_string", "")
                        snap.edit_old_string_found = old_string in content

                        if len(content) > 0:
                            snap.edit_change_ratio = len(old_string) / len(content)
                        else:
                            snap.edit_change_ratio = 1.0
                    except (OSError, UnicodeDecodeError):
                        snap.target_line_count[target] = 0

                elif cmd.category == CommandCategory.FILE_WRITE:
                    try:
                        content = path.read_text(errors="replace")
                        snap.target_line_count[target] = len(content.splitlines())
                    except (OSError, UnicodeDecodeError):
                        snap.target_line_count[target] = 0

    return snap


def _resolve_path(target: str, cwd: str) -> Path:
    p = Path(target)
    if not p.is_absolute():
        p = Path(cwd) / p
    return p


def _run(args: list[str], cwd: str) -> str | None:
    try:
        r = subprocess.run(
            args, capture_output=True, text=True, cwd=cwd, timeout=5
        )
        return r.stdout.strip() if r.returncode == 0 else None
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


def _is_git_repo(cwd: str) -> bool:
    return _run(["git", "rev-parse", "--is-inside-work-tree"], cwd) == "true"


def _git_current_branch(cwd: str) -> str | None:
    return _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd)


def _git_is_dirty(cwd: str) -> bool:
    result = _run(["git", "status", "--porcelain"], cwd)
    return bool(result)


def _git_untracked_count(cwd: str) -> int:
    result = _run(["git", "ls-files", "--others", "--exclude-standard"], cwd)
    return len(result.splitlines()) if result else 0


def _git_staged_count(cwd: str) -> int:
    result = _run(["git", "diff", "--cached", "--name-only"], cwd)
    return len(result.splitlines()) if result else 0


def _git_remote(cwd: str) -> str | None:
    return _run(["git", "remote", "get-url", "origin"], cwd)


def _is_git_tracked(target: str, cwd: str) -> bool:
    r = subprocess.run(
        ["git", "ls-files", "--error-unmatch", target],
        capture_output=True, cwd=cwd, timeout=5,
    )
    return r.returncode == 0


# File patterns that are sensitive / config files
_CONFIG_FILENAMES = {
    ".env", ".envrc", ".env.local", ".env.production",
    "pyproject.toml", "setup.py", "setup.cfg",
    "package.json", "package-lock.json", "yarn.lock",
    "Dockerfile", "docker-compose.yml", "docker-compose.yaml",
    "Makefile", "Cargo.toml", "go.mod", "go.sum",
    "tsconfig.json", "requirements.txt", "Pipfile", "Pipfile.lock",
    ".gitignore", ".gitattributes", ".gitmodules",
    "CLAUDE.md", ".claude.json",
}

_CONFIG_PATH_PATTERNS = {
    ".github/workflows", ".github/actions",
    ".circleci", ".gitlab-ci",
    ".vscode/settings", ".idea/",
}


def _is_config_file(target: str) -> bool:
    """Check if a file path points to a config/sensitive file."""
    name = Path(target).name
    if name in _CONFIG_FILENAMES:
        return True
    if name.startswith(".env"):
        return True
    # Check path patterns
    for pattern in _CONFIG_PATH_PATTERNS:
        if pattern in target:
            return True
    return False


def _get_size(path: Path) -> int:
    """Get total size in bytes. For directories, sum all contents."""
    if path.is_file():
        return path.stat().st_size
    total = 0
    try:
        for f in path.rglob("*"):
            if f.is_file():
                total += f.stat().st_size
    except PermissionError:
        pass
    return total
