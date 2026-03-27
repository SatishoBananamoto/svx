"""Tests for command parser."""

from svx.parser import parse_command, has_force_flags
from svx.schemas import CommandCategory


def test_simple_git_push():
    cmds = parse_command("git push origin main")
    assert len(cmds) == 1
    cmd = cmds[0]
    assert cmd.program == "git"
    assert cmd.subcommand == "push"
    assert cmd.category == CommandCategory.GIT
    assert "origin" in cmd.targets


def test_git_force_push():
    cmds = parse_command("git push --force origin main")
    cmd = cmds[0]
    assert has_force_flags(cmd)
    assert "--force" in cmd.flags


def test_git_reset_hard():
    cmds = parse_command("git reset --hard HEAD~3")
    cmd = cmds[0]
    assert cmd.subcommand == "reset"
    assert "--hard" in cmd.flags
    assert not has_force_flags(cmd)  # --hard is a mode flag, not a force override


def test_rm_recursive():
    cmds = parse_command("rm -rf node_modules")
    cmd = cmds[0]
    assert cmd.category == CommandCategory.FILE_DELETE
    assert "node_modules" in cmd.targets


def test_chained_commands():
    cmds = parse_command("git add . && git commit -m 'test' && git push")
    assert len(cmds) == 3
    assert cmds[0].subcommand == "add"
    assert cmds[1].subcommand == "commit"
    assert cmds[2].subcommand == "push"


def test_sudo_stripped():
    cmds = parse_command("sudo rm -rf /tmp/build")
    cmd = cmds[0]
    assert cmd.program == "rm"
    assert cmd.category == CommandCategory.FILE_DELETE


def test_pipe_analyzes_last():
    cmds = parse_command("cat file.txt | grep pattern")
    cmd = cmds[0]
    assert cmd.program == "grep"


def test_npm_install():
    cmds = parse_command("npm install lodash")
    cmd = cmds[0]
    assert cmd.category == CommandCategory.PACKAGE
    assert cmd.subcommand == "install"


def test_git_clean():
    cmds = parse_command("git clean -fd")
    cmd = cmds[0]
    assert cmd.subcommand == "clean"
    assert "-fd" in cmd.flags


def test_mv_overwrite():
    cmds = parse_command("mv src/old.py src/new.py")
    cmd = cmds[0]
    assert cmd.category == CommandCategory.FILE_MOVE
    assert len(cmd.targets) == 2


def test_kill_process():
    cmds = parse_command("kill -9 1234")
    cmd = cmds[0]
    assert cmd.category == CommandCategory.PROCESS
    assert "-9" in cmd.flags


def test_empty_command():
    cmds = parse_command("")
    assert len(cmds) == 0


def test_git_branch_force_delete():
    cmds = parse_command("git branch -D feature-old")
    cmd = cmds[0]
    assert cmd.subcommand == "branch"
    assert "-D" in cmd.flags
    assert has_force_flags(cmd)
