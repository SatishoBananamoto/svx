"""caliber bridge — grade svx against reality.

Every svx assessment is an implicit prediction: "I understood this command;
it will complete without error." This module makes that prediction explicit
by recording it in caliber (signed, so timing is provable) at PreToolUse,
and grading it against the tool result at PostToolUse.

Design: BRIDGE.md. Three hard rules:

1. Fail-open everywhere — a safety layer must never break the workflow to
   feed its own scorecard. Any bridge failure means "no bridge", never
   "no command".
2. No raw command text in claims — Trust Cards travel, commands can carry
   secrets. Claims hold parsed metadata; correlation uses a hash prefix.
3. Don't guess outcomes — if the tool response carries no recognizable
   success/failure signal, the prediction stays unverified.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .schemas import ParsedCommand, VerificationResult, Verdict, RiskLevel
from .session import SESSION_TTL_SECONDS

PENDING_FILE = "pending_predictions.json"

# Confidence that an assessed command completes without error, by risk
# level. This table is a HYPOTHESIS the bridge exists to test: if svx says
# 0.95 and only 80% run clean, the Trust Card will show it and these
# numbers get tuned from evidence.
RISK_CONFIDENCE: dict[RiskLevel, float] = {
    RiskLevel.NONE: 0.95,
    RiskLevel.LOW: 0.90,
    RiskLevel.MEDIUM: 0.80,
    RiskLevel.HIGH: 0.70,
    RiskLevel.CRITICAL: 0.60,
}


def bridge_enabled(config: dict | None) -> bool:
    """The bridge is opt-in per project: caliber_bridge: true in config."""
    return bool(config) and config.get("caliber_bridge") is True


def _command_hash(raw_command: str) -> str:
    return hashlib.sha256(raw_command.encode("utf-8")).hexdigest()


def _agent_name(root: Path) -> str:
    return f"svx-{root.name}"


def _store_path(config: dict | None) -> str:
    if config and isinstance(config.get("caliber_store"), str):
        return config["caliber_store"]
    return str(Path.home() / ".caliber")


def _pending_path(root: Path) -> Path:
    return root / ".svx" / PENDING_FILE


def _load_pending(root: Path) -> dict[str, Any]:
    path = _pending_path(root)
    if not path.exists():
        return {"version": 1, "pending": {}}
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "pending": {}}
    if not isinstance(data, dict) or not isinstance(data.get("pending"), dict):
        return {"version": 1, "pending": {}}
    return data


def _write_pending(root: Path, data: dict[str, Any]) -> None:
    try:
        path = _pending_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except OSError:
        # Non-blocking, like session tracking.
        return


def _prune_pending(pending: dict[str, Any], max_age_sec: int = SESSION_TTL_SECONDS) -> None:
    """Drop entries whose PostToolUse never arrived. Their predictions
    honestly remain unverified in caliber."""
    now = datetime.now(timezone.utc).timestamp()
    for key in list(pending.keys()):
        entries = pending.get(key)
        if not isinstance(entries, list):
            pending.pop(key, None)
            continue
        kept = []
        for entry in entries:
            try:
                at = datetime.fromisoformat(entry["at"]).timestamp()
                if now - at <= max_age_sec:
                    kept.append(entry)
            except (KeyError, TypeError, ValueError):
                continue
        if kept:
            pending[key] = kept
        else:
            pending.pop(key, None)


def record_assessment(
    cmd: ParsedCommand,
    result: VerificationResult,
    root: Path,
    config: dict | None,
) -> Optional[str]:
    """Record the assessment as a signed caliber prediction.

    Called at PreToolUse after verify(). Returns the prediction id, or
    None when the bridge is disabled, the command will not run (BLOCK),
    or anything at all goes wrong (fail-open).
    """
    try:
        if not bridge_enabled(config):
            return None
        if result.verdict == Verdict.BLOCK:
            # Blocked commands never run: the claim would be unverifiable.
            return None

        from caliber import TrustTracker

        confidence = RISK_CONFIDENCE.get(result.risk_level, 0.80)
        category = cmd.category.value
        # Parsed metadata only — never cmd.raw (claims travel with cards).
        claim = f"svx: {cmd.program} ({category}) completes without error"

        tracker = TrustTracker(
            _agent_name(root), store_path=_store_path(config), signed=True
        )
        pid = tracker.predict(claim, confidence=confidence, domain=f"svx-{category}")

        data = _load_pending(root)
        pending = data["pending"]
        _prune_pending(pending)
        key = _command_hash(cmd.raw)
        pending.setdefault(key, []).append(
            {"pid": pid, "at": datetime.now(timezone.utc).isoformat()}
        )
        _write_pending(root, data)
        return pid
    except Exception:
        return None


def grade_outcome(
    raw_command: str,
    tool_response: Any,
    root: Path,
    config: dict | None,
) -> Optional[bool]:
    """Verify the oldest pending prediction for this command.

    Called at PostToolUse. Returns the recorded outcome, or None when
    there is nothing to grade, the response carries no recognizable
    signal, or anything goes wrong (fail-open).
    """
    try:
        if not bridge_enabled(config):
            return None

        outcome = _outcome_from_response(tool_response)
        if outcome is None:
            # No recognizable signal: leave the prediction pending rather
            # than guess. It ages out via the TTL and stays unverified.
            return None

        data = _load_pending(root)
        pending = data["pending"]
        key = _command_hash(raw_command)
        entries = pending.get(key)
        if not isinstance(entries, list) or not entries:
            return None
        entry = entries.pop(0)  # FIFO: oldest prediction for this command
        if not entries:
            pending.pop(key, None)
        _write_pending(root, data)

        from caliber import TrustTracker

        tracker = TrustTracker(
            _agent_name(root), store_path=_store_path(config), signed=True
        )
        tracker.verify(
            entry["pid"], correct=outcome, notes=f"cmd:{key[:12]}"
        )
        return outcome
    except Exception:
        return None


def _outcome_from_response(tool_response: Any) -> Optional[bool]:
    """Extract success/failure from a PostToolUse tool_response.

    Conservative by design: only explicit signals grade the prediction.
    stderr content is NOT a failure signal (git and friends write
    warnings there). Unknown shapes return None.
    """
    if not isinstance(tool_response, dict):
        return None

    for key in ("is_error", "isError"):
        if key in tool_response:
            return not bool(tool_response[key])

    if tool_response.get("interrupted") is True:
        return False

    for key in ("exit_code", "exitCode", "returnCode"):
        value = tool_response.get(key)
        if isinstance(value, int):
            return value == 0

    if "stdout" in tool_response:
        # The harness returned a normal command result with no error
        # marker — the command completed.
        return True

    return None
