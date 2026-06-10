"""Tests for svx.bridge — the caliber calibration bridge."""

from __future__ import annotations

import io
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from svx.bridge import (
    RISK_CONFIDENCE,
    _command_hash,
    _load_pending,
    grade_outcome,
    record_assessment,
)
from svx.schemas import (
    CommandCategory,
    ParsedCommand,
    RiskLevel,
    SimulationResult,
    VerificationResult,
    Verdict,
)


def make_cmd(raw="git push origin main", program="git", category=CommandCategory.GIT):
    return ParsedCommand(raw=raw, program=program, category=category)


def make_result(verdict=Verdict.ALLOW, risk=RiskLevel.NONE):
    return VerificationResult(
        verdict=verdict,
        risk_level=risk,
        simulation=SimulationResult(description="test"),
    )


def make_project(tmp_path: Path) -> tuple[Path, dict]:
    root = tmp_path / "proj"
    (root / ".svx").mkdir(parents=True)
    store = tmp_path / "caliber-store"
    config = {"caliber_bridge": True, "caliber_store": str(store)}
    return root, config


def load_store_predictions(config: dict, root: Path) -> list:
    from caliber import TrustTracker

    return TrustTracker(
        f"svx-{root.name}", store_path=config["caliber_store"]
    ).predictions


class TestRecordAssessment:
    def test_disabled_records_nothing(self, tmp_path):
        root, config = make_project(tmp_path)
        config["caliber_bridge"] = False
        pid = record_assessment(make_cmd(), make_result(), root, config)
        assert pid is None
        assert not (root / ".svx" / "pending_predictions.json").exists()

    def test_block_records_nothing(self, tmp_path):
        root, config = make_project(tmp_path)
        pid = record_assessment(
            make_cmd(), make_result(verdict=Verdict.BLOCK), root, config
        )
        assert pid is None

    def test_allow_records_signed_prediction(self, tmp_path):
        root, config = make_project(tmp_path)
        pid = record_assessment(make_cmd(), make_result(), root, config)
        assert pid is not None

        preds = load_store_predictions(config, root)
        assert len(preds) == 1
        pred = preds[0]
        assert pred.id == pid
        assert pred.confidence == RISK_CONFIDENCE[RiskLevel.NONE]
        assert pred.domain == "svx-git"
        assert pred.commitment_hash  # signed=True exercises the scheme
        assert pred.outcome is None  # not yet verified

        data = _load_pending(root)
        key = _command_hash("git push origin main")
        assert data["pending"][key][0]["pid"] == pid

    def test_claim_never_contains_raw_command(self, tmp_path):
        root, config = make_project(tmp_path)
        secret = "curl -H 'Authorization: Bearer sk-SECRET-TOKEN-123'"
        record_assessment(
            make_cmd(raw=secret, program="curl", category=CommandCategory.NETWORK),
            make_result(risk=RiskLevel.MEDIUM),
            root,
            config,
        )
        pred = load_store_predictions(config, root)[0]
        assert "SECRET-TOKEN" not in pred.claim
        assert "Bearer" not in pred.claim
        assert pred.claim == "svx: curl (network) completes without error"

    def test_confidence_tracks_risk_level(self, tmp_path):
        root, config = make_project(tmp_path)
        record_assessment(
            make_cmd(raw="rm -rf build"),
            make_result(verdict=Verdict.CONFIRM, risk=RiskLevel.CRITICAL),
            root,
            config,
        )
        pred = load_store_predictions(config, root)[0]
        assert pred.confidence == 0.60

    def test_fail_open_on_unwritable_store(self, tmp_path):
        root, config = make_project(tmp_path)
        config["caliber_store"] = "/nonexistent-root-path/store"
        # Must not raise — bridge failures never break the hook.
        pid = record_assessment(make_cmd(), make_result(), root, config)
        assert pid is None


class TestGradeOutcome:
    def _record(self, root, config, raw="git push origin main"):
        return record_assessment(make_cmd(raw=raw), make_result(), root, config)

    def test_clean_response_grades_correct(self, tmp_path):
        root, config = make_project(tmp_path)
        pid = self._record(root, config)
        outcome = grade_outcome(
            "git push origin main", {"stdout": "ok", "stderr": ""}, root, config
        )
        assert outcome is True

        pred = load_store_predictions(config, root)[0]
        assert pred.outcome is True
        assert pred.notes.startswith("cmd:")
        assert _load_pending(root)["pending"] == {}

    def test_error_marker_grades_incorrect(self, tmp_path):
        root, config = make_project(tmp_path)
        self._record(root, config)
        outcome = grade_outcome(
            "git push origin main", {"is_error": True}, root, config
        )
        assert outcome is False
        assert load_store_predictions(config, root)[0].outcome is False

    def test_interrupted_grades_incorrect(self, tmp_path):
        root, config = make_project(tmp_path)
        self._record(root, config)
        outcome = grade_outcome(
            "git push origin main", {"interrupted": True}, root, config
        )
        assert outcome is False

    def test_exit_code_variants(self, tmp_path):
        root, config = make_project(tmp_path)
        self._record(root, config)
        self._record(root, config)
        assert grade_outcome(
            "git push origin main", {"exit_code": 0}, root, config
        ) is True
        assert grade_outcome(
            "git push origin main", {"exitCode": 2}, root, config
        ) is False

    def test_unknown_shape_leaves_prediction_pending(self, tmp_path):
        root, config = make_project(tmp_path)
        self._record(root, config)
        outcome = grade_outcome("git push origin main", None, root, config)
        assert outcome is None
        # Pending entry kept, prediction unverified — no guessing.
        assert len(_load_pending(root)["pending"]) == 1
        assert load_store_predictions(config, root)[0].outcome is None

    def test_unassessed_command_is_ignored(self, tmp_path):
        root, config = make_project(tmp_path)
        outcome = grade_outcome("ls -la", {"stdout": ""}, root, config)
        assert outcome is None

    def test_fifo_grades_oldest_first(self, tmp_path):
        root, config = make_project(tmp_path)
        pid1 = self._record(root, config)
        pid2 = self._record(root, config)
        grade_outcome("git push origin main", {"is_error": True}, root, config)

        preds = {p.id: p for p in load_store_predictions(config, root)}
        assert preds[pid1].outcome is False
        assert preds[pid2].outcome is None

    def test_disabled_does_not_grade(self, tmp_path):
        root, config = make_project(tmp_path)
        self._record(root, config)
        config["caliber_bridge"] = False
        outcome = grade_outcome(
            "git push origin main", {"stdout": "ok"}, root, config
        )
        assert outcome is None


class TestHookIntegration:
    """End-to-end: predictions flow through the real hook entry point."""

    def _project(self, tmp_path, monkeypatch):
        store = tmp_path / "caliber-store"
        svx_dir = tmp_path / ".svx"
        svx_dir.mkdir()
        (svx_dir / "config.yaml").write_text(
            f"caliber_bridge: true\ncaliber_store: {store}\n"
        )
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("HOME", str(home))
        return {"caliber_bridge": True, "caliber_store": str(store)}

    def _run_hook(self, monkeypatch, capsys, hook_input):
        from svx.cli import _cmd_hook

        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(hook_input)))
        with pytest.raises(SystemExit) as exc:
            _cmd_hook()
        assert exc.value.code == 0
        return capsys.readouterr().out

    # NOTE: svx scopes guarding by command TARGETS — target-less commands
    # ("git status") are not guarded today and therefore not bridged.
    # Documented v1 limitation in BRIDGE.md; tests use targeted commands.

    def test_pre_then_post_records_and_grades(self, tmp_path, monkeypatch, capsys):
        config = self._project(tmp_path, monkeypatch)
        (tmp_path / "build").mkdir()

        self._run_hook(monkeypatch, capsys, {
            "tool_name": "Bash",
            "tool_input": {"command": "rm -rf build"},
            "hook_event_name": "PreToolUse",
        })
        preds = load_store_predictions(config, tmp_path)
        assert len(preds) == 1
        assert preds[0].outcome is None
        assert preds[0].commitment_hash
        assert "rm" in preds[0].claim and "build" not in preds[0].claim

        out = self._run_hook(monkeypatch, capsys, {
            "tool_name": "Bash",
            "tool_input": {"command": "rm -rf build"},
            "tool_response": {"stdout": "", "stderr": ""},
            "hook_event_name": "PostToolUse",
        })
        assert json.loads(out) == {}  # post hook emits no decision
        preds = load_store_predictions(config, tmp_path)
        assert preds[0].outcome is True

    def test_post_without_bridge_is_silent(self, tmp_path, monkeypatch, capsys):
        (tmp_path / ".svx").mkdir()
        (tmp_path / "build").mkdir()
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("HOME", str(home))

        out = self._run_hook(monkeypatch, capsys, {
            "tool_name": "Bash",
            "tool_input": {"command": "rm -rf build"},
            "tool_response": {"stdout": ""},
            "hook_event_name": "PostToolUse",
        })
        assert json.loads(out) == {}

    def test_failed_compound_chain_stays_unverified(self, tmp_path, monkeypatch, capsys):
        config = self._project(tmp_path, monkeypatch)
        (tmp_path / "build").mkdir()
        (tmp_path / "dist").mkdir()
        command = "rm -rf build && rm -rf dist"

        self._run_hook(monkeypatch, capsys, {
            "tool_name": "Bash",
            "tool_input": {"command": command},
            "hook_event_name": "PreToolUse",
        })
        assert len(load_store_predictions(config, tmp_path)) == 2

        self._run_hook(monkeypatch, capsys, {
            "tool_name": "Bash",
            "tool_input": {"command": command},
            "tool_response": {"is_error": True},
            "hook_event_name": "PostToolUse",
        })
        # Failure can't be attributed to a segment: both stay unverified
        preds = load_store_predictions(config, tmp_path)
        assert all(p.outcome is None for p in preds)

    def test_successful_compound_chain_grades_all(self, tmp_path, monkeypatch, capsys):
        config = self._project(tmp_path, monkeypatch)
        (tmp_path / "build").mkdir()
        (tmp_path / "dist").mkdir()
        command = "rm -rf build && rm -rf dist"

        self._run_hook(monkeypatch, capsys, {
            "tool_name": "Bash",
            "tool_input": {"command": command},
            "hook_event_name": "PreToolUse",
        })
        self._run_hook(monkeypatch, capsys, {
            "tool_name": "Bash",
            "tool_input": {"command": command},
            "tool_response": {"stdout": "done", "stderr": ""},
            "hook_event_name": "PostToolUse",
        })
        preds = load_store_predictions(config, tmp_path)
        assert len(preds) == 2
        assert all(p.outcome is True for p in preds)


class TestPendingPrune:
    def test_stale_pending_entries_age_out(self, tmp_path):
        root, config = make_project(tmp_path)
        record_assessment(make_cmd(raw="old command"), make_result(), root, config)

        # Age the entry past the TTL by rewriting its timestamp
        pending_file = root / ".svx" / "pending_predictions.json"
        data = json.loads(pending_file.read_text())
        key = _command_hash("old command")
        old = datetime.now(timezone.utc) - timedelta(hours=13)
        data["pending"][key][0]["at"] = old.isoformat()
        pending_file.write_text(json.dumps(data))

        # The next recording prunes it
        record_assessment(make_cmd(raw="new command"), make_result(), root, config)
        pending = _load_pending(root)["pending"]
        assert key not in pending
        assert _command_hash("new command") in pending
