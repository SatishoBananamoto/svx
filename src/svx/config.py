"""SVX configuration loader.

Reads ~/.svx.yaml and project-local .svx/config.yaml preferences. Falls back to
defaults.

Config format:
    mode: vibe    # "vibe" (auto-allow, only block catastrophic) or "strict" (ask for risky)
    paused: false # true temporarily bypasses the hook for this project
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml


_DEFAULT = {
    "mode": "vibe",
    "paused": False,
}

_DISABLED_VALUES = {"1", "true", "yes", "on"}


def load_config(cwd: str | Path | None = None) -> dict:
    """Load config from defaults, home config, and project config."""
    config = dict(_DEFAULT)
    config_path = Path.home() / ".svx.yaml"
    config.update(_read_yaml(config_path))

    project_path = project_config_path(cwd)
    if project_path:
        config.update(_read_yaml(project_path))

    return config


def load_project_config(cwd: str | Path | None = None) -> dict:
    """Load only the project-local .svx/config.yaml file."""
    path = project_config_path(cwd)
    if not path:
        return {}
    return _read_yaml(path)


def save_project_config(config: dict, cwd: str | Path | None = None) -> Path:
    """Write project-local .svx/config.yaml, preserving existing unknown keys."""
    root = find_svx_root(Path(cwd) if cwd is not None else Path.cwd())
    if root is None:
        raise FileNotFoundError("No .svx directory found. Run 'svx init' first.")

    path = root / ".svx" / "config.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False))
    return path


def project_config_path(cwd: str | Path | None = None) -> Path | None:
    """Return the project-local config path when inside an svx project."""
    root = find_svx_root(Path(cwd) if cwd is not None else Path.cwd())
    if root is None:
        return None
    return root / ".svx" / "config.yaml"


def find_svx_root(path: Path) -> Path | None:
    """Walk up from path looking for a .svx/ directory."""
    current = path if path.is_dir() else path.parent
    for _ in range(50):
        if (current / ".svx").is_dir():
            return current
        parent = current.parent
        if parent == current:
            break
        current = parent
    return None


def is_paused(config: dict | None = None, cwd: str | Path | None = None) -> bool:
    """Check if the project hook is paused."""
    if config is None:
        config = load_config(cwd=cwd)
    return bool(config.get("paused", False))


def is_disabled_by_env() -> bool:
    """Check if the hook should be bypassed by environment override."""
    return os.environ.get("SVX_DISABLED", "").strip().lower() in _DISABLED_VALUES


def is_vibe_mode(config: dict | None = None) -> bool:
    """Check if running in vibe mode."""
    if config is None:
        config = load_config()
    return config.get("mode", "vibe") == "vibe"


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}
