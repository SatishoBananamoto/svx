"""Tests for the verification pipeline."""

from svx.parser import parse_command
from svx.schemas import (
    WorldSnapshot,
    Verdict,
    RiskLevel,
    Reversibility,
)
from svx.simulator import simulate
from svx.verifier import verify


def _make_snap(**kwargs) -> WorldSnapshot:
    defaults = {
        "cwd": "/tmp/test-repo",
        "is_git_repo": True,
        "git_branch": "main",
        "git_dirty": False,
    }
    defaults.update(kwargs)
    return WorldSnapshot(**defaults)


def test_force_push_main_blocked():
    cmd = parse_command("git push --force origin main")[0]
    snap = _make_snap()
    sim = simulate(cmd, snap)
    result = verify(cmd, snap, sim)
    assert result.verdict == Verdict.BLOCK


def test_force_push_feature_confirms():
    cmd = parse_command("git push --force origin feature-x")[0]
    snap = _make_snap(git_branch="feature-x")
    sim = simulate(cmd, snap)
    result = verify(cmd, snap, sim)
    assert result.verdict == Verdict.CONFIRM


def test_normal_push_allows():
    cmd = parse_command("git push origin feature-x")[0]
    snap = _make_snap(git_branch="feature-x")
    sim = simulate(cmd, snap)
    result = verify(cmd, snap, sim)
    assert result.verdict == Verdict.ALLOW


def test_git_reset_hard_confirms():
    cmd = parse_command("git reset --hard HEAD~3")[0]
    snap = _make_snap(git_dirty=True)
    sim = simulate(cmd, snap)
    result = verify(cmd, snap, sim)
    assert result.verdict == Verdict.CONFIRM
    assert sim.reversibility == Reversibility.IRREVERSIBLE


def test_rm_untracked_file_confirms():
    cmd = parse_command("rm important.log")[0]
    snap = _make_snap(
        target_exists={"important.log": True},
        target_sizes={"important.log": 1024},
        target_git_tracked={"important.log": False},
    )
    sim = simulate(cmd, snap)
    result = verify(cmd, snap, sim)
    assert sim.data_loss_possible
    assert result.verdict == Verdict.CONFIRM


def test_rm_tracked_file_allows():
    cmd = parse_command("rm src/old.py")[0]
    snap = _make_snap(
        target_exists={"src/old.py": True},
        target_sizes={"src/old.py": 512},
        target_git_tracked={"src/old.py": True},
    )
    sim = simulate(cmd, snap)
    result = verify(cmd, snap, sim)
    # Tracked file can be recovered from git
    assert not sim.data_loss_possible
    assert result.verdict == Verdict.ALLOW


def test_rm_rf_large_dir_confirms():
    cmd = parse_command("rm -rf build/")[0]
    snap = _make_snap(
        target_exists={"build/": True},
        target_sizes={"build/": 50_000_000},
        target_git_tracked={"build/": False},
    )
    sim = simulate(cmd, snap)
    result = verify(cmd, snap, sim)
    assert result.verdict == Verdict.CONFIRM
    assert sim.blast_radius > 5


def test_git_status_allows():
    cmd = parse_command("git status")[0]
    snap = _make_snap()
    sim = simulate(cmd, snap)
    result = verify(cmd, snap, sim)
    assert result.verdict == Verdict.ALLOW
    assert result.risk_level == RiskLevel.NONE


def test_git_clean_confirms():
    cmd = parse_command("git clean -fd")[0]
    snap = _make_snap(git_untracked_count=15)
    sim = simulate(cmd, snap)
    result = verify(cmd, snap, sim)
    assert result.verdict == Verdict.CONFIRM
    assert sim.data_loss_possible


def test_npm_install_allows():
    cmd = parse_command("npm install lodash")[0]
    snap = _make_snap()
    sim = simulate(cmd, snap)
    result = verify(cmd, snap, sim)
    assert result.verdict == Verdict.ALLOW


def test_kill_9_confirms():
    cmd = parse_command("kill -9 1234")[0]
    snap = _make_snap()
    sim = simulate(cmd, snap)
    result = verify(cmd, snap, sim)
    assert result.verdict == Verdict.CONFIRM
    assert sim.data_loss_possible
