"""Verify safety of a command based on simulation results and policies."""

from __future__ import annotations

import yaml
from pathlib import Path
from .schemas import (
    ParsedCommand,
    WorldSnapshot,
    SimulationResult,
    VerificationResult,
    CommandCategory,
    RiskLevel,
    Verdict,
    Reversibility,
)
from .parser import has_force_flags


DEFAULT_POLICIES_PATH = Path(__file__).parent.parent.parent / "policies" / "default.yaml"


def verify(
    cmd: ParsedCommand,
    snap: WorldSnapshot,
    sim: SimulationResult,
    policies_path: Path | None = None,
) -> VerificationResult:
    """Produce a final safety verdict for a command."""
    policies = _load_policies(policies_path or DEFAULT_POLICIES_PATH)
    reasons: list[str] = []
    suggestions: list[str] = []

    # 1. Assess risk level
    risk = _assess_risk(cmd, snap, sim, policies)

    # 2. Check hard blocks
    blocked, block_reasons = _check_blocks(cmd, snap, sim, policies)
    if blocked:
        return VerificationResult(
            verdict=Verdict.BLOCK,
            risk_level=RiskLevel.CRITICAL,
            simulation=sim,
            reasons=block_reasons,
            suggestions=["Do not run this command.", "Consider a safer alternative."],
        )

    # 3. Check confirmation requirements
    needs_confirm, confirm_reasons = _check_confirmations(cmd, snap, sim, policies)

    # 4. Build verdict
    if needs_confirm:
        reasons.extend(confirm_reasons)
        verdict = Verdict.CONFIRM
    elif risk in (RiskLevel.HIGH, RiskLevel.CRITICAL):
        reasons.append(f"Risk level is {risk.value}")
        verdict = Verdict.CONFIRM
    else:
        verdict = Verdict.ALLOW

    # 5. Gather all reasons
    if sim.data_loss_possible:
        reasons.append("Potential data loss detected")
    if sim.reversibility == Reversibility.IRREVERSIBLE:
        reasons.append("Action is irreversible")
    if sim.blast_radius > 5:
        reasons.append(f"High blast radius: ~{sim.blast_radius} objects affected")
    if has_force_flags(cmd):
        reasons.append("Force flag detected — bypasses safety checks")

    # 6. Suggestions
    suggestions.extend(_suggest_alternatives(cmd, snap, sim))

    return VerificationResult(
        verdict=verdict,
        risk_level=risk,
        simulation=sim,
        reasons=reasons,
        suggestions=suggestions,
    )


def _assess_risk(
    cmd: ParsedCommand,
    snap: WorldSnapshot,
    sim: SimulationResult,
    policies: dict,
) -> RiskLevel:
    """Score the risk level of a command."""
    score = 0

    # Irreversibility
    if sim.reversibility == Reversibility.IRREVERSIBLE:
        score += 3
    elif sim.reversibility == Reversibility.PARTIALLY:
        score += 1

    # Data loss
    if sim.data_loss_possible:
        score += 2

    # Blast radius
    if sim.blast_radius > 10:
        score += 3
    elif sim.blast_radius > 5:
        score += 2
    elif sim.blast_radius > 2:
        score += 1

    # Force flags
    if has_force_flags(cmd):
        score += 2

    # Main branch operations
    if cmd.category == CommandCategory.GIT and cmd.subcommand == "push":
        targets = " ".join(cmd.targets)
        if "main" in targets or "master" in targets:
            score += 2

    # Config file edits
    if cmd.category in (CommandCategory.FILE_EDIT, CommandCategory.FILE_WRITE):
        for target in cmd.targets:
            if snap.target_is_config.get(target, False):
                score += 2

    # Large rewrite via Edit tool
    if cmd.category == CommandCategory.FILE_EDIT and snap.edit_change_ratio > 0.5:
        score += 2

    # Overwriting untracked file via Write tool
    if cmd.category == CommandCategory.FILE_WRITE:
        for target in cmd.targets:
            if (
                snap.target_exists.get(target, False)
                and not snap.target_git_tracked.get(target, False)
            ):
                score += 3  # high risk — unrecoverable overwrite

    # Dirty repo + destructive command
    if snap.git_dirty and sim.data_loss_possible:
        score += 1

    # Map score to level
    if score >= 7:
        return RiskLevel.CRITICAL
    elif score >= 5:
        return RiskLevel.HIGH
    elif score >= 3:
        return RiskLevel.MEDIUM
    elif score >= 1:
        return RiskLevel.LOW
    return RiskLevel.NONE


def _check_blocks(
    cmd: ParsedCommand,
    snap: WorldSnapshot,
    sim: SimulationResult,
    policies: dict,
) -> tuple[bool, list[str]]:
    """Check if the command should be hard-blocked."""
    reasons = []

    block_rules = policies.get("blocks", {})

    # Force push to main/master
    if block_rules.get("force_push_to_main", True):
        if (
            cmd.category == CommandCategory.GIT
            and cmd.subcommand == "push"
            and has_force_flags(cmd)
        ):
            targets = " ".join(cmd.targets)
            if "main" in targets or "master" in targets:
                reasons.append("BLOCKED: Force push to main/master is not allowed")

    # rm -rf /
    if cmd.category == CommandCategory.FILE_DELETE:
        for target in cmd.targets:
            if target in ("/", "/*", "~", "~/*"):
                reasons.append(f"BLOCKED: Deleting '{target}' is catastrophic")

    return bool(reasons), reasons


def _check_confirmations(
    cmd: ParsedCommand,
    snap: WorldSnapshot,
    sim: SimulationResult,
    policies: dict,
) -> tuple[bool, list[str]]:
    """Check if the command needs user confirmation."""
    reasons = []
    confirm_rules = policies.get("confirmations", {})
    thresholds = policies.get("thresholds", {})

    # Irreversible actions
    if confirm_rules.get("irreversible_actions", True):
        if sim.reversibility == Reversibility.IRREVERSIBLE:
            reasons.append("Irreversible action requires confirmation")

    # Data loss
    if confirm_rules.get("data_loss", True):
        if sim.data_loss_possible:
            reasons.append("Potential data loss requires confirmation")

    # High blast radius
    max_blast = thresholds.get("max_blast_radius_without_confirm", 5)
    if sim.blast_radius > max_blast:
        reasons.append(
            f"Blast radius ({sim.blast_radius}) exceeds threshold ({max_blast})"
        )

    # Force flags
    if confirm_rules.get("force_flags", True):
        if has_force_flags(cmd):
            reasons.append("Force flag requires confirmation")

    # Config file edits
    if confirm_rules.get("config_file_edits", True):
        if cmd.category in (CommandCategory.FILE_EDIT, CommandCategory.FILE_WRITE):
            for target in cmd.targets:
                if snap.target_is_config.get(target, False):
                    reasons.append(f"Editing config file '{target}' requires confirmation")

    # Large rewrites (>50% of file)
    if cmd.category == CommandCategory.FILE_EDIT and snap.edit_change_ratio > 0.5:
        reasons.append(
            f"Major rewrite ({snap.edit_change_ratio:.0%} of file) requires confirmation"
        )

    # Overwriting existing untracked files
    if cmd.category == CommandCategory.FILE_WRITE:
        for target in cmd.targets:
            if (
                snap.target_exists.get(target, False)
                and not snap.target_git_tracked.get(target, False)
            ):
                reasons.append(
                    f"Overwriting untracked file '{target}' — no git recovery possible"
                )

    return bool(reasons), reasons


def _suggest_alternatives(
    cmd: ParsedCommand,
    snap: WorldSnapshot,
    sim: SimulationResult,
) -> list[str]:
    """Suggest safer alternatives when possible."""
    suggestions = []

    if cmd.category == CommandCategory.GIT:
        if cmd.subcommand == "push" and has_force_flags(cmd):
            suggestions.append(
                "Use 'git push --force-with-lease' instead — it fails if remote has new commits"
            )
        if cmd.subcommand == "reset" and "--hard" in cmd.flags:
            suggestions.append(
                "Consider 'git stash' first to save uncommitted work"
            )
        if cmd.subcommand == "clean":
            suggestions.append(
                "Run 'git clean -n' first to preview what will be deleted"
            )
        if cmd.subcommand == "branch" and "-D" in cmd.flags:
            suggestions.append(
                "Use '-d' instead of '-D' — it refuses to delete unmerged branches"
            )

    if cmd.category == CommandCategory.FILE_DELETE:
        recursive = "-r" in cmd.flags or "-rf" in cmd.flags or "-fr" in cmd.flags
        if recursive:
            suggestions.append(
                f"Run 'ls {' '.join(cmd.targets)}' first to see what will be deleted"
            )

    if cmd.category == CommandCategory.FILE_EDIT:
        if snap.edit_change_ratio > 0.5:
            suggestions.append(
                "Consider smaller, incremental edits instead of rewriting large sections"
            )
        if snap.edit_old_string_found is False:
            suggestions.append(
                "old_string not found in file — re-read the file before editing"
            )

    if cmd.category == CommandCategory.FILE_WRITE:
        for target in cmd.targets:
            if snap.target_exists.get(target, False):
                if not snap.target_git_tracked.get(target, False):
                    suggestions.append(
                        f"Back up '{target}' before overwriting — it's not git-tracked"
                    )
                suggestions.append(
                    "Consider using Edit (targeted replacement) instead of Write (full overwrite)"
                )

    return suggestions


def _load_policies(path: Path) -> dict:
    """Load policy YAML file."""
    if path.exists():
        with open(path) as f:
            return yaml.safe_load(f) or {}
    return {}
