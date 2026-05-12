"""Claude Code hook settings helpers for svx."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


HOOK_TOOLS = ("Bash", "Edit", "Write")
SVX_HOOK_COMMAND = "svx hook"


def settings_path(project_root: Path) -> Path:
    """Return the local Claude Code settings path for a project."""
    return project_root / ".claude" / "settings.local.json"


def load_settings(path: Path) -> dict[str, Any]:
    """Load settings JSON, returning an empty config for a missing file."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Expected object at top level in {path}")
    return data


def save_settings(path: Path, settings: dict[str, Any], *, backup: bool = True) -> Path | None:
    """Write settings JSON, optionally backing up the previous file first."""
    path.parent.mkdir(parents=True, exist_ok=True)
    backup_path = None
    if backup and path.exists():
        backup_path = _next_backup_path(path)
        shutil.copy2(path, backup_path)

    path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    return backup_path


def enable_svx_hook(
    settings: dict[str, Any],
    *,
    command: str = SVX_HOOK_COMMAND,
) -> tuple[dict[str, Any], list[str]]:
    """Add svx PreToolUse command hooks for Bash, Edit, and Write."""
    settings = _copy_settings(settings)
    hooks = settings.setdefault("hooks", {})
    pre_tool_use = hooks.setdefault("PreToolUse", [])
    if not isinstance(pre_tool_use, list):
        raise ValueError("hooks.PreToolUse must be a list")

    added = []
    for matcher in HOOK_TOOLS:
        group = _find_or_create_matcher_group(pre_tool_use, matcher)
        group_hooks = group.setdefault("hooks", [])
        if not isinstance(group_hooks, list):
            raise ValueError(f"hooks.PreToolUse matcher '{matcher}' has invalid hooks")
        if not _has_command_hook(group_hooks, command):
            group_hooks.append({"type": "command", "command": command})
            added.append(matcher)

    return settings, added


def disable_svx_hook(
    settings: dict[str, Any],
    *,
    command: str = SVX_HOOK_COMMAND,
) -> tuple[dict[str, Any], int]:
    """Remove svx command hooks while preserving other hooks."""
    settings = _copy_settings(settings)
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return settings, 0

    pre_tool_use = hooks.get("PreToolUse")
    if not isinstance(pre_tool_use, list):
        return settings, 0

    removed = 0
    kept_groups = []
    for group in pre_tool_use:
        if not isinstance(group, dict):
            kept_groups.append(group)
            continue
        group_hooks = group.get("hooks")
        if not isinstance(group_hooks, list):
            kept_groups.append(group)
            continue

        kept_hooks = []
        for hook in group_hooks:
            if _is_command_hook(hook, command):
                removed += 1
            else:
                kept_hooks.append(hook)

        if kept_hooks:
            group["hooks"] = kept_hooks
            kept_groups.append(group)

    if kept_groups:
        hooks["PreToolUse"] = kept_groups
    else:
        hooks.pop("PreToolUse", None)

    return settings, removed


def _copy_settings(settings: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(settings))


def _next_backup_path(path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    candidate = path.with_name(f"{path.name}.{stamp}.bak")
    if not candidate.exists():
        return candidate

    suffix = 1
    while True:
        candidate = path.with_name(f"{path.name}.{stamp}.{suffix}.bak")
        if not candidate.exists():
            return candidate
        suffix += 1


def _find_or_create_matcher_group(groups: list[Any], matcher: str) -> dict[str, Any]:
    for group in groups:
        if isinstance(group, dict) and group.get("matcher") == matcher:
            return group

    group = {"matcher": matcher, "hooks": []}
    groups.append(group)
    return group


def _has_command_hook(hooks: list[Any], command: str) -> bool:
    return any(_is_command_hook(hook, command) for hook in hooks)


def _is_command_hook(hook: Any, command: str) -> bool:
    return (
        isinstance(hook, dict)
        and hook.get("type") == "command"
        and hook.get("command") == command
    )
