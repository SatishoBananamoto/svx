"""Tests for Claude Code hook settings helpers."""

import sys

from svx.cli import main
from svx.hook_config import (
    disable_svx_hook,
    enable_svx_hook,
    load_settings,
    save_settings,
    settings_path,
)


def test_enable_adds_tool_hooks_for_guarded_tools():
    updated, added = enable_svx_hook({})

    assert added == ["Bash", "Edit", "Write", "Bash (PostToolUse)"]
    pre_groups = updated["hooks"]["PreToolUse"]
    assert [group["matcher"] for group in pre_groups] == ["Bash", "Edit", "Write"]
    assert all(group["hooks"] == [{"type": "command", "command": "svx hook"}] for group in pre_groups)

    post_groups = updated["hooks"]["PostToolUse"]
    assert [group["matcher"] for group in post_groups] == ["Bash"]
    assert post_groups[0]["hooks"] == [{"type": "command", "command": "svx hook"}]


def test_enable_preserves_existing_hooks_and_is_idempotent():
    settings = {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [{"type": "command", "command": "./security-check.sh"}],
                }
            ]
        }
    }

    updated, added = enable_svx_hook(settings)
    updated_again, added_again = enable_svx_hook(updated)

    bash_pre_hooks = updated_again["hooks"]["PreToolUse"][0]["hooks"]
    bash_post_hooks = updated_again["hooks"]["PostToolUse"][0]["hooks"]
    assert added == ["Bash", "Edit", "Write", "Bash (PostToolUse)"]
    assert added_again == []
    assert {"type": "command", "command": "./security-check.sh"} in bash_pre_hooks
    assert bash_pre_hooks.count({"type": "command", "command": "svx hook"}) == 1
    assert bash_post_hooks.count({"type": "command", "command": "svx hook"}) == 1


def test_disable_removes_only_svx_hooks():
    settings = {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [
                        {"type": "command", "command": "svx hook"},
                        {"type": "command", "command": "./security-check.sh"},
                    ],
                },
                {
                    "matcher": "Edit",
                    "hooks": [{"type": "command", "command": "svx hook"}],
                },
            ],
            "PostToolUse": [
                {"matcher": "Bash", "hooks": [{"type": "command", "command": "svx hook"}]},
            ],
        }
    }

    updated, removed = disable_svx_hook(settings)

    assert removed == 3
    assert updated["hooks"]["PreToolUse"] == [
        {
            "matcher": "Bash",
            "hooks": [{"type": "command", "command": "./security-check.sh"}],
        }
    ]
    assert "PostToolUse" not in updated["hooks"]


def test_disable_preserves_non_svx_posttooluse_hooks():
    settings = {
        "hooks": {
            "PostToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [
                        {"type": "command", "command": "svx hook"},
                        {"type": "command", "command": "./audit.sh"},
                    ],
                }
            ],
        }
    }

    updated, removed = disable_svx_hook(settings)

    assert removed == 1
    assert updated["hooks"]["PostToolUse"] == [
        {
            "matcher": "Bash",
            "hooks": [{"type": "command", "command": "./audit.sh"}],
        }
    ]


def test_disable_skips_malformed_event_sections():
    settings = {
        "hooks": {
            "PreToolUse": {"matcher": "Bash"},
            "PostToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [{"type": "command", "command": "svx hook"}],
                },
            ],
        }
    }

    updated, removed = disable_svx_hook(settings)

    assert removed == 1
    assert updated["hooks"]["PreToolUse"] == {"matcher": "Bash"}
    assert "PostToolUse" not in updated["hooks"]


def test_save_settings_backs_up_existing_file(tmp_path):
    path = settings_path(tmp_path)
    path.parent.mkdir()
    path.write_text('{"existing": true}\n')

    backup = save_settings(path, {"hooks": {}}, backup=True)

    assert backup is not None
    assert backup.exists()
    assert '"existing": true' in backup.read_text()
    assert load_settings(path) == {"hooks": {}}


def test_save_settings_does_not_overwrite_existing_backup(tmp_path):
    path = settings_path(tmp_path)
    path.parent.mkdir()
    path.write_text('{"existing": true}\n')

    first_backup = save_settings(path, {"first": True}, backup=True)
    second_backup = save_settings(path, {"second": True}, backup=True)

    assert first_backup is not None
    assert second_backup is not None
    assert first_backup != second_backup
    assert first_backup.exists()
    assert second_backup.exists()


def test_enable_cli_writes_project_local_settings(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["svx", "enable"])

    main()

    settings = load_settings(settings_path(tmp_path))
    pre_groups = settings["hooks"]["PreToolUse"]
    post_groups = settings["hooks"]["PostToolUse"]
    assert [group["matcher"] for group in pre_groups] == ["Bash", "Edit", "Write"]
    assert [group["matcher"] for group in post_groups] == ["Bash"]
