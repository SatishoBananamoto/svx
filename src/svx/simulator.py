"""Simulate what a command will do to the world state."""

from __future__ import annotations

import subprocess
from .schemas import (
    ParsedCommand,
    WorldSnapshot,
    SimulationResult,
    CommandCategory,
    Reversibility,
)
from .parser import has_force_flags


def simulate(cmd: ParsedCommand, snap: WorldSnapshot) -> SimulationResult:
    """Predict the outcome of a command given the current world state."""
    simulators = {
        CommandCategory.GIT: _simulate_git,
        CommandCategory.FILE_DELETE: _simulate_delete,
        CommandCategory.FILE_MOVE: _simulate_move,
        CommandCategory.FILE_PERMISSION: _simulate_permission,
        CommandCategory.FILE_EDIT: _simulate_file_edit,
        CommandCategory.FILE_WRITE: _simulate_file_write,
        CommandCategory.PACKAGE: _simulate_package,
        CommandCategory.PROCESS: _simulate_process,
    }

    fn = simulators.get(cmd.category)
    if fn:
        return fn(cmd, snap)
    return SimulationResult(description=f"Unknown command: {cmd.raw}")


# ── Git ──────────────────────────────────────────────────────────────────────


def _simulate_git(cmd: ParsedCommand, snap: WorldSnapshot) -> SimulationResult:
    sub = cmd.subcommand
    if sub == "push":
        return _sim_git_push(cmd, snap)
    elif sub == "reset":
        return _sim_git_reset(cmd, snap)
    elif sub in ("checkout", "restore"):
        return _sim_git_checkout(cmd, snap)
    elif sub == "clean":
        return _sim_git_clean(cmd, snap)
    elif sub == "branch" and "-D" in cmd.flags:
        return _sim_git_branch_delete(cmd, snap)
    elif sub == "rebase":
        return _sim_git_rebase(cmd, snap)
    elif sub == "stash" and cmd.targets and cmd.targets[0] == "drop":
        return _sim_git_stash_drop(cmd, snap)
    elif sub in ("add", "commit", "pull", "fetch", "merge", "tag", "stash"):
        return _sim_git_safe_mutate(cmd, snap)
    else:
        return SimulationResult(
            description=f"git {sub}: read-only or low-risk operation",
            reversibility=Reversibility.REVERSIBLE,
        )


def _sim_git_push(cmd: ParsedCommand, snap: WorldSnapshot) -> SimulationResult:
    forced = has_force_flags(cmd)
    branch = snap.git_branch or "unknown"

    # Try dry-run to see what would be pushed
    effects = []
    dry_run = _run_git_dry_run(
        ["git", "push", "--dry-run"] + cmd.flags + cmd.targets, snap.cwd
    )
    if dry_run:
        effects.append(f"Dry-run output: {dry_run[:200]}")

    if forced:
        target_branch = cmd.targets[-1] if cmd.targets else branch
        is_main = target_branch in ("main", "master")

        return SimulationResult(
            description=f"Force-push to {target_branch} — rewrites remote history",
            effects=[
                f"Remote branch '{target_branch}' will be overwritten",
                "Commits on remote not in local will become unreachable",
                *effects,
            ],
            failure_modes=[
                "Other contributors' work may be lost",
                "CI pipelines referencing old commits will break",
                *(["FORCE PUSH TO MAIN/MASTER"] if is_main else []),
            ],
            reversibility=Reversibility.IRREVERSIBLE,
            data_loss_possible=True,
            blast_radius=10 if is_main else 5,
        )

    return SimulationResult(
        description=f"Push {branch} to remote",
        effects=[f"Remote will receive commits from '{branch}'", *effects],
        reversibility=Reversibility.PARTIALLY,
        blast_radius=1,
    )


def _sim_git_reset(cmd: ParsedCommand, snap: WorldSnapshot) -> SimulationResult:
    hard = "--hard" in cmd.flags

    if hard:
        # Check what would be lost
        diff_stat = _run_git_dry_run(["git", "diff", "--stat"], snap.cwd)
        staged_stat = _run_git_dry_run(
            ["git", "diff", "--cached", "--stat"], snap.cwd
        )

        effects = []
        if diff_stat:
            effects.append(f"Unstaged changes lost: {diff_stat[:200]}")
        if staged_stat:
            effects.append(f"Staged changes lost: {staged_stat[:200]}")
        if snap.git_dirty:
            effects.append("Working tree has uncommitted changes that will be destroyed")

        # Only flag data loss if there's actually something to lose
        has_changes = bool(diff_stat or staged_stat or snap.git_dirty)

        return SimulationResult(
            description="Hard reset — destroys all uncommitted changes",
            effects=effects or (["All uncommitted work will be lost"] if has_changes else ["No uncommitted changes detected"]),
            failure_modes=["No recovery without reflog (and only within gc window)"] if has_changes else [],
            reversibility=Reversibility.IRREVERSIBLE if has_changes else Reversibility.REVERSIBLE,
            data_loss_possible=has_changes,
            blast_radius=(snap.git_staged_count + 5) if has_changes else 0,
        )

    return SimulationResult(
        description="Soft/mixed reset — unstages changes but keeps files",
        effects=["Staged changes moved to working tree"],
        reversibility=Reversibility.REVERSIBLE,
        blast_radius=1,
    )


def _sim_git_checkout(cmd: ParsedCommand, snap: WorldSnapshot) -> SimulationResult:
    # git checkout -- files (discard changes)
    if "--" in cmd.flags or "--" in cmd.args:
        return SimulationResult(
            description="Discard uncommitted changes to specific files",
            effects=["Modified files will revert to last committed state"],
            failure_modes=["Uncommitted work in those files is permanently lost"],
            reversibility=Reversibility.IRREVERSIBLE,
            data_loss_possible=True,
            blast_radius=len(cmd.targets),
        )

    return SimulationResult(
        description=f"Switch branch/checkout",
        effects=["Working tree updated to target branch/commit"],
        reversibility=Reversibility.REVERSIBLE,
        blast_radius=1,
    )


def _sim_git_clean(cmd: ParsedCommand, snap: WorldSnapshot) -> SimulationResult:
    forced = "-f" in cmd.flags or "--force" in cmd.flags
    dirs_too = "-d" in cmd.flags

    # Use dry-run to see what would be cleaned
    dry_args = ["git", "clean", "-n"]
    if dirs_too:
        dry_args.append("-d")
    dry_run = _run_git_dry_run(dry_args, snap.cwd)

    file_count = len(dry_run.splitlines()) if dry_run else snap.git_untracked_count

    return SimulationResult(
        description=f"Remove {file_count} untracked files" + (" and directories" if dirs_too else ""),
        effects=[
            f"{file_count} untracked files will be permanently deleted",
            *(dry_run.splitlines()[:10] if dry_run else []),
        ],
        failure_modes=["Untracked files are not in git — no recovery possible"],
        reversibility=Reversibility.IRREVERSIBLE,
        data_loss_possible=True,
        blast_radius=file_count,
    )


def _sim_git_branch_delete(cmd: ParsedCommand, snap: WorldSnapshot) -> SimulationResult:
    branch_name = cmd.targets[0] if cmd.targets else "unknown"
    force = "-D" in cmd.flags

    return SimulationResult(
        description=f"{'Force-delete' if force else 'Delete'} branch '{branch_name}'",
        effects=[
            f"Branch '{branch_name}' will be removed",
            *(["Unmerged commits on this branch may become unreachable"] if force else []),
        ],
        failure_modes=["Branch recovery requires reflog within gc window"],
        reversibility=Reversibility.PARTIALLY,
        data_loss_possible=force,
        blast_radius=2,
    )


def _sim_git_rebase(cmd: ParsedCommand, snap: WorldSnapshot) -> SimulationResult:
    return SimulationResult(
        description="Rebase — rewrites commit history",
        effects=[
            "Commit hashes will change",
            "May cause conflicts requiring manual resolution",
        ],
        failure_modes=[
            "If pushed before, remote and local diverge",
            "Conflict resolution errors can corrupt history",
        ],
        reversibility=Reversibility.PARTIALLY,
        data_loss_possible=False,
        blast_radius=3,
    )


def _sim_git_stash_drop(cmd: ParsedCommand, snap: WorldSnapshot) -> SimulationResult:
    return SimulationResult(
        description="Drop git stash entry",
        effects=["Stashed changes will be permanently deleted"],
        reversibility=Reversibility.IRREVERSIBLE,
        data_loss_possible=True,
        blast_radius=1,
    )


def _sim_git_safe_mutate(cmd: ParsedCommand, snap: WorldSnapshot) -> SimulationResult:
    return SimulationResult(
        description=f"git {cmd.subcommand}: standard operation",
        reversibility=Reversibility.REVERSIBLE,
        blast_radius=1,
    )


# ── File operations ──────────────────────────────────────────────────────────


def _simulate_delete(cmd: ParsedCommand, snap: WorldSnapshot) -> SimulationResult:
    recursive = "-r" in cmd.flags or "-rf" in cmd.flags or "-fr" in cmd.flags
    effects = []
    total_size = 0
    total_tracked = 0

    for target in cmd.targets:
        exists = snap.target_exists.get(target, False)
        size = snap.target_sizes.get(target, 0)
        tracked = snap.target_git_tracked.get(target, False)

        if exists:
            size_mb = size / (1024 * 1024)
            effects.append(
                f"Delete '{target}' ({size_mb:.1f} MB)"
                + (" [git-tracked]" if tracked else " [untracked]")
            )
            total_size += size
            if tracked:
                total_tracked += 1
        else:
            effects.append(f"'{target}' does not exist — no-op")

    data_loss = bool(effects) and any(
        snap.target_exists.get(t, False)
        and not snap.target_git_tracked.get(t, False)
        for t in cmd.targets
    )

    return SimulationResult(
        description=f"Delete {len(cmd.targets)} target(s)"
        + (" recursively" if recursive else ""),
        effects=effects,
        failure_modes=[
            *(["Untracked files cannot be recovered from git"] if data_loss else []),
            *(["Git-tracked files recoverable via git checkout"] if total_tracked else []),
        ],
        reversibility=(
            Reversibility.REVERSIBLE
            if total_tracked and not data_loss
            else Reversibility.IRREVERSIBLE
        ),
        data_loss_possible=data_loss,
        blast_radius=len(cmd.targets) * (10 if recursive else 1),
    )


def _simulate_move(cmd: ParsedCommand, snap: WorldSnapshot) -> SimulationResult:
    if len(cmd.targets) >= 2:
        src, dst = cmd.targets[0], cmd.targets[-1]
        dst_exists = snap.target_exists.get(dst, False)

        effects = [f"Move '{src}' to '{dst}'"]
        if dst_exists:
            effects.append(f"WARNING: '{dst}' already exists and will be OVERWRITTEN")

        return SimulationResult(
            description=f"Move/rename '{src}' to '{dst}'",
            effects=effects,
            failure_modes=(
                [f"Original content of '{dst}' will be lost"] if dst_exists else []
            ),
            reversibility=(
                Reversibility.IRREVERSIBLE if dst_exists else Reversibility.REVERSIBLE
            ),
            data_loss_possible=dst_exists,
            blast_radius=2 if dst_exists else 1,
        )

    return SimulationResult(
        description=f"{cmd.program}: insufficient arguments to analyze",
        reversibility=Reversibility.UNKNOWN,
    )


def _simulate_permission(cmd: ParsedCommand, snap: WorldSnapshot) -> SimulationResult:
    return SimulationResult(
        description=f"Change permissions on {len(cmd.targets)} target(s)",
        effects=[f"{cmd.program} {' '.join(cmd.flags)} {' '.join(cmd.targets)}"],
        reversibility=Reversibility.REVERSIBLE,
        blast_radius=len(cmd.targets),
    )


# ── Package managers ─────────────────────────────────────────────────────────


def _simulate_package(cmd: ParsedCommand, snap: WorldSnapshot) -> SimulationResult:
    sub = cmd.subcommand or ""
    is_install = sub in ("install", "add", "i")
    is_remove = sub in ("uninstall", "remove", "rm")
    is_global = "-g" in cmd.flags or "--global" in cmd.flags

    if is_remove:
        return SimulationResult(
            description=f"Remove package(s): {' '.join(cmd.targets)}",
            effects=[f"Package(s) will be uninstalled" + (" globally" if is_global else "")],
            reversibility=Reversibility.REVERSIBLE,
            blast_radius=2,
        )

    if is_install:
        return SimulationResult(
            description=f"Install package(s): {' '.join(cmd.targets) or 'from lockfile'}",
            effects=[
                f"Dependencies will be installed" + (" globally" if is_global else ""),
                "Lock file may be updated",
            ],
            reversibility=Reversibility.REVERSIBLE,
            blast_radius=1,
        )

    return SimulationResult(
        description=f"{cmd.program} {sub}",
        reversibility=Reversibility.UNKNOWN,
        blast_radius=1,
    )


# ── Process management ───────────────────────────────────────────────────────


def _simulate_process(cmd: ParsedCommand, snap: WorldSnapshot) -> SimulationResult:
    signal_9 = "-9" in cmd.flags or "-KILL" in cmd.flags

    return SimulationResult(
        description=f"Kill process(es): {' '.join(cmd.targets)}",
        effects=["Target process(es) will be terminated" + (" forcefully" if signal_9 else "")],
        failure_modes=["Unsaved state in target process will be lost"],
        reversibility=Reversibility.IRREVERSIBLE,
        data_loss_possible=True,
        blast_radius=len(cmd.targets),
    )


# ── File edits (Edit/Write tool calls) ───────────────────────────────────────


def _simulate_file_edit(cmd: ParsedCommand, snap: WorldSnapshot) -> SimulationResult:
    """Simulate a Claude Code Edit tool call."""
    target = cmd.targets[0] if cmd.targets else "unknown"
    exists = snap.target_exists.get(target, False)
    tracked = snap.target_git_tracked.get(target, False)
    is_config = snap.target_is_config.get(target, False)
    line_count = snap.target_line_count.get(target, 0)
    change_ratio = snap.edit_change_ratio
    old_found = snap.edit_old_string_found

    old_len = len(cmd.metadata.get("old_string", ""))
    new_len = len(cmd.metadata.get("new_string", ""))

    effects = []
    failure_modes = []

    if not exists:
        return SimulationResult(
            description=f"Edit target '{target}' does not exist — will fail",
            effects=["File not found — edit cannot proceed"],
            failure_modes=["Edit tool will error"],
            reversibility=Reversibility.REVERSIBLE,
            blast_radius=0,
        )

    if old_found is False:
        failure_modes.append("old_string not found in file — edit will fail")

    # Describe the change
    if change_ratio > 0.5:
        effects.append(f"Major rewrite: replacing {change_ratio:.0%} of file content")
    elif change_ratio > 0.2:
        effects.append(f"Significant edit: replacing {change_ratio:.0%} of file content")
    else:
        effects.append(f"Targeted edit: replacing {change_ratio:.0%} of file content")

    size_delta = new_len - old_len
    if size_delta > 0:
        effects.append(f"File grows by ~{size_delta} chars")
    elif size_delta < 0:
        effects.append(f"File shrinks by ~{abs(size_delta)} chars")

    if is_config:
        effects.append(f"WARNING: '{target}' is a config/sensitive file")
        failure_modes.append("Config file changes can break builds, deploys, or secrets")

    if tracked:
        effects.append("File is git-tracked — recoverable via git checkout")
    else:
        failure_modes.append("File is NOT git-tracked — no git recovery")

    # Reversibility: git-tracked files are recoverable
    if tracked:
        reversibility = Reversibility.REVERSIBLE
        data_loss = False
    else:
        reversibility = Reversibility.PARTIALLY
        data_loss = True

    # Blast radius
    blast = 1
    if is_config:
        blast += 2  # config changes ripple
    if change_ratio > 0.5:
        blast += 1  # major rewrites are riskier

    return SimulationResult(
        description=f"Edit '{target}' ({line_count} lines, {change_ratio:.0%} changed)",
        effects=effects,
        failure_modes=failure_modes,
        reversibility=reversibility,
        data_loss_possible=data_loss,
        blast_radius=blast,
    )


def _simulate_file_write(cmd: ParsedCommand, snap: WorldSnapshot) -> SimulationResult:
    """Simulate a Claude Code Write tool call (full file create/overwrite)."""
    target = cmd.targets[0] if cmd.targets else "unknown"
    exists = snap.target_exists.get(target, False)
    tracked = snap.target_git_tracked.get(target, False)
    is_config = snap.target_is_config.get(target, False)
    current_size = snap.target_sizes.get(target, 0)
    current_lines = snap.target_line_count.get(target, 0)
    new_content_len = cmd.metadata.get("content_length", 0)

    effects = []
    failure_modes = []

    if exists:
        effects.append(
            f"OVERWRITE existing file '{target}' "
            f"({current_lines} lines, {current_size} bytes)"
        )
        if tracked:
            effects.append("Current content recoverable via git checkout")
        else:
            effects.append("Current content will be PERMANENTLY LOST (not git-tracked)")
            failure_modes.append("No recovery possible for overwritten untracked file")

        if is_config:
            effects.append(f"WARNING: overwriting config/sensitive file '{target}'")
            failure_modes.append("Config file overwrite can break builds, deploys, or secrets")
    else:
        effects.append(f"Create new file '{target}' ({new_content_len} chars)")

    # Reversibility
    if not exists:
        # Creating new file — just delete it to undo
        reversibility = Reversibility.REVERSIBLE
        data_loss = False
    elif tracked:
        reversibility = Reversibility.REVERSIBLE
        data_loss = False
    else:
        reversibility = Reversibility.IRREVERSIBLE
        data_loss = True

    # Blast radius
    blast = 1
    if exists and not tracked:
        blast += 2  # overwriting untracked = data loss
    if is_config:
        blast += 2  # config changes ripple

    return SimulationResult(
        description=(
            f"Overwrite '{target}' ({current_lines} lines)"
            if exists
            else f"Create new file '{target}'"
        ),
        effects=effects,
        failure_modes=failure_modes,
        reversibility=reversibility,
        data_loss_possible=data_loss,
        blast_radius=blast,
    )


# ── Helpers ──────────────────────────────────────────────────────────────────


def _run_git_dry_run(args: list[str], cwd: str) -> str | None:
    try:
        r = subprocess.run(args, capture_output=True, text=True, cwd=cwd, timeout=5)
        return r.stdout.strip() or r.stderr.strip() or None
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
