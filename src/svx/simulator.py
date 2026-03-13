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
    elif sub == "stash" and (not cmd.targets or cmd.targets[0] == "drop"):
        return _sim_git_stash_drop(cmd, snap)
    elif sub in ("add", "commit", "pull", "fetch", "merge", "tag"):
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

        return SimulationResult(
            description="Hard reset — destroys all uncommitted changes",
            effects=effects or ["All uncommitted work will be lost"],
            failure_modes=["No recovery without reflog (and only within gc window)"],
            reversibility=Reversibility.IRREVERSIBLE,
            data_loss_possible=True,
            blast_radius=snap.git_staged_count + 5,
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


# ── Helpers ──────────────────────────────────────────────────────────────────


def _run_git_dry_run(args: list[str], cwd: str) -> str | None:
    try:
        r = subprocess.run(args, capture_output=True, text=True, cwd=cwd, timeout=5)
        return r.stdout.strip() or r.stderr.strip() or None
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
