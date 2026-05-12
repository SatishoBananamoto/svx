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


def test_enable_adds_pretooluse_hooks_for_guarded_tools():
    updated, added = enable_svx_hook({})

    assert added == ["Bash", "Edit", "Write"]
    groups = updated["hooks"]["PreToolUse"]
    assert [group["matcher"] for group in groups] == ["Bash", "Edit", "Write"]
    assert all(group["hooks"] == [{"type": "command", "command": "svx hook"}] for group in groups)


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

    bash_hooks = updated_again["hooks"]["PreToolUse"][0]["hooks"]
    assert added == ["Bash", "Edit", "Write"]
    assert added_again == []
    assert {"type": "command", "command": "./security-check.sh"} in bash_hooks
    assert bash_hooks.count({"type": "command", "command": "svx hook"}) == 1


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

    assert removed == 2
    assert updated["hooks"]["PreToolUse"] == [
        {
            "matcher": "Bash",
            "hooks": [{"type": "command", "command": "./security-check.sh"}],
        }
    ]
    assert "PostToolUse" in updated["hooks"]


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
    groups = settings["hooks"]["PreToolUse"]
    assert [group["matcher"] for group in groups] == ["Bash", "Edit", "Write"]
