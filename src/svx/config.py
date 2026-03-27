"""SVX configuration loader.

Reads ~/.svx.yaml for user preferences. Falls back to defaults.

Config format:
    mode: vibe    # "vibe" (auto-allow, only block catastrophic) or "strict" (ask for risky)
"""

from __future__ import annotations

from pathlib import Path

import yaml


_DEFAULT = {
    "mode": "vibe",
}


def load_config() -> dict:
    """Load config from ~/.svx.yaml, falling back to defaults."""
    config_path = Path.home() / ".svx.yaml"
    if config_path.exists():
        try:
            with open(config_path) as f:
                user_config = yaml.safe_load(f) or {}
            merged = {**_DEFAULT, **user_config}
            return merged
        except Exception:
            return dict(_DEFAULT)
    return dict(_DEFAULT)


def is_vibe_mode(config: dict | None = None) -> bool:
    """Check if running in vibe mode."""
    if config is None:
        config = load_config()
    return config.get("mode", "vibe") == "vibe"
