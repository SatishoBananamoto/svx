"""Tests for the MCP server tools."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from svx.server import assess_command, assess_edit, assess_write, get_audit


def test_assess_command_safe():
    """Safe command should return allow."""
    result = assess_command("git status")
    assert result["overall_verdict"] == "allow"
    assert len(result["commands"]) == 1


def test_assess_command_dangerous():
    """Force push to main should return block."""
    result = assess_command("git push --force origin main")
    assert result["overall_verdict"] == "block"
    assert result["commands"][0]["verdict"] == "block"
    assert result["commands"][0]["risk_level"] == "critical"


def test_assess_command_confirm():
    """Destructive command should return confirm."""
    result = assess_command("git reset --hard HEAD")
    assert result["overall_verdict"] == "confirm"


def test_assess_command_chained():
    """Chained commands should return worst verdict."""
    result = assess_command("git status && git push --force origin main")
    assert result["overall_verdict"] == "block"
    assert len(result["commands"]) == 2


def test_assess_edit_safe():
    """Small edit to a file in a git repo should work.

    Note: temp files in /tmp aren't git-tracked, so they'll flag
    data loss. We test with a file inside the svx repo itself.
    """
    # Use an actual git-tracked file in the svx repo
    svx_root = str(Path(__file__).parent.parent)
    result = assess_edit(
        file_path="src/svx/__init__.py",
        old_string='"""',
        new_string='"""',  # no-op edit
        cwd=svx_root,
    )
    assert result["verdict"] == "allow"
    assert result["reversibility"] == "reversible"


def test_assess_edit_config_file():
    """Editing a file named .env should confirm."""
    with tempfile.NamedTemporaryFile(
        mode="w", prefix=".env", delete=False, dir="/tmp"
    ) as f:
        f.write("SECRET=old_value\n")
        tmp_path = f.name

    try:
        # Rename to .env for config detection
        env_path = os.path.join(os.path.dirname(tmp_path), ".env")
        os.rename(tmp_path, env_path)

        result = assess_edit(
            file_path=env_path,
            old_string="SECRET=old_value",
            new_string="SECRET=new_value",
            cwd="/tmp",
        )
        assert result["verdict"] == "confirm"
        assert any("config" in r.lower() for r in result["reasons"])
    finally:
        if os.path.exists(env_path):
            os.unlink(env_path)


def test_assess_edit_nonexistent():
    """Editing a nonexistent file should report it."""
    result = assess_edit(
        file_path="/tmp/does_not_exist_svx_test.py",
        old_string="old",
        new_string="new",
        cwd="/tmp",
    )
    assert "does not exist" in result["description"]


def test_assess_write_new_file():
    """Writing a new file should allow."""
    result = assess_write(
        file_path="/tmp/svx_new_file_test.py",
        content_length=100,
        cwd="/tmp",
    )
    assert result["verdict"] == "allow"
    assert result["data_loss_possible"] is False


def test_assess_write_overwrite():
    """Overwriting an existing untracked file should confirm."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".dat", delete=False) as f:
        f.write("important data\n" * 100)
        tmp_path = f.name

    try:
        result = assess_write(
            file_path=tmp_path,
            content_length=50,
            cwd=os.path.dirname(tmp_path),
        )
        assert result["verdict"] == "confirm"
        assert result["data_loss_possible"] is True
    finally:
        os.unlink(tmp_path)


def test_get_audit_empty():
    """get_audit should handle missing audit log gracefully."""
    result = get_audit(count=5)
    # May or may not have entries depending on test order,
    # but should not crash
    assert "entries" in result


def test_assess_command_result_structure():
    """Verify the result dict has all expected fields."""
    result = assess_command("ls")
    cmd = result["commands"][0]
    expected_keys = {
        "verdict", "risk_level", "description", "effects",
        "failure_modes", "reversibility", "blast_radius",
        "data_loss_possible", "reasons", "suggestions",
    }
    assert expected_keys.issubset(set(cmd.keys()))
