"""Tests for Claude Code hookSpecificOutput behavior."""

import io
import json
import sys

import pytest

from svx.cli import _cmd_hook, _emit_hook_output
from svx.schemas import (
    CommandCategory,
    DenyKind,
    ParsedCommand,
    RiskLevel,
    SimulationResult,
    Verdict,
    VerificationResult,
)


def _cmd() -> ParsedCommand:
    return ParsedCommand(
        raw="Write notes.txt",
        program="Write",
        category=CommandCategory.FILE_WRITE,
        targets=["notes.txt"],
    )


def _result(
    verdict: Verdict,
    *,
    deny_kind: DenyKind | None = None,
    advisory_action: str | None = None,
) -> VerificationResult:
    return VerificationResult(
        verdict=verdict,
        risk_level=RiskLevel.HIGH,
        simulation=SimulationResult(description="Overwrite untracked file"),
        reasons=["Potential data loss detected"],
        deny_kind=deny_kind,
        advisory_action=advisory_action,
    )


def test_emit_hook_output_allow_is_empty_json(capsys):
    _emit_hook_output(Verdict.ALLOW, None, None)

    assert json.loads(capsys.readouterr().out) == {}


def test_emit_hook_output_advisory_deny_has_action(capsys):
    result = _result(
        Verdict.CONFIRM,
        deny_kind=DenyKind.ADVISORY,
        advisory_action="Back up the file first, then retry.",
    )

    _emit_hook_output(Verdict.CONFIRM, _cmd(), result)

    data = json.loads(capsys.readouterr().out)
    hook_output = data["hookSpecificOutput"]
    assert hook_output["hookEventName"] == "PreToolUse"
    assert hook_output["permissionDecision"] == "deny"
    assert "Action needed: Back up the file first" in hook_output["permissionDecisionReason"]


def test_emit_hook_output_block_uses_blocked_reason(capsys):
    result = _result(Verdict.BLOCK, deny_kind=DenyKind.HARD)

    _emit_hook_output(Verdict.BLOCK, _cmd(), result)

    data = json.loads(capsys.readouterr().out)
    reason = data["hookSpecificOutput"]["permissionDecisionReason"]
    assert data["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "[svx BLOCKED]" in reason
    assert "Potential data loss detected" in reason


def test_cmd_hook_strict_write_advisory_outputs_hook_specific_json(
    tmp_path,
    monkeypatch,
    capsys,
):
    (tmp_path / ".svx").mkdir()
    target = tmp_path / "notes.txt"
    target.write_text("old content\n")

    home = tmp_path / "home"
    home.mkdir()
    (home / ".svx.yaml").write_text("mode: strict\n")

    hook_input = {
        "tool_name": "Write",
        "tool_input": {
            "file_path": str(target),
            "content": "new content\n",
        },
        "hook_event_name": "PreToolUse",
    }

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(hook_input)))

    with pytest.raises(SystemExit) as exc:
        _cmd_hook()

    assert exc.value.code == 0
    data = json.loads(capsys.readouterr().out)
    hook_output = data["hookSpecificOutput"]
    assert hook_output["hookEventName"] == "PreToolUse"
    assert hook_output["permissionDecision"] == "deny"
    assert "Action needed:" in hook_output["permissionDecisionReason"]
