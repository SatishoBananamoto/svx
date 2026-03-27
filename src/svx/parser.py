"""Parse shell commands into structured form."""

from __future__ import annotations

import shlex
from .schemas import ParsedCommand, CommandCategory


# Git subcommands that modify state
GIT_DANGEROUS_SUBCMDS = {
    "push", "reset", "rebase", "checkout", "clean", "stash",
    "branch", "merge", "cherry-pick", "revert", "tag", "am",
}

GIT_SAFE_SUBCMDS = {
    "status", "log", "diff", "show", "branch", "remote",
    "fetch", "ls-files", "rev-parse", "describe", "shortlog",
}

# Programs that delete or overwrite
FILE_DELETE_PROGRAMS = {"rm", "rmdir", "unlink", "shred"}
FILE_MOVE_PROGRAMS = {"mv", "cp"}
FILE_PERM_PROGRAMS = {"chmod", "chown", "chgrp"}
PACKAGE_PROGRAMS = {"npm", "pip", "pip3", "yarn", "pnpm", "apt", "apt-get", "brew"}
PROCESS_PROGRAMS = {"kill", "killall", "pkill"}
NETWORK_PROGRAMS = {"curl", "wget"}

# Flags that escalate risk
FORCE_FLAGS = {"--force", "-f", "--no-verify", "-D", "--delete"}


def parse_command(raw: str) -> list[ParsedCommand]:
    """Parse a raw command string into one or more ParsedCommands.

    Handles chained commands (&&, ||, ;) by splitting and parsing each.
    """
    segments = _split_chains(raw)
    return [_parse_single(seg) for seg in segments if seg.strip()]


def _split_chains(raw: str) -> list[str]:
    """Split chained commands on &&, ||, ; while respecting quotes."""
    segments = []
    current = []
    i = 0
    in_quote = None

    while i < len(raw):
        ch = raw[i]

        if ch in ('"', "'") and in_quote is None:
            in_quote = ch
            current.append(ch)
        elif ch == in_quote:
            in_quote = None
            current.append(ch)
        elif in_quote:
            current.append(ch)
        elif ch == ';':
            segments.append("".join(current))
            current = []
        elif ch == '&' and i + 1 < len(raw) and raw[i + 1] == '&':
            segments.append("".join(current))
            current = []
            i += 1  # skip second &
        elif ch == '|' and i + 1 < len(raw) and raw[i + 1] == '|':
            segments.append("".join(current))
            current = []
            i += 1  # skip second |
        else:
            current.append(ch)
        i += 1

    if current:
        segments.append("".join(current))

    return segments


def _parse_single(raw: str) -> ParsedCommand:
    """Parse a single command (no chains)."""
    raw = raw.strip()

    # Handle pipes — analyze the last command in the pipe for risk
    if '|' in raw:
        parts = raw.split('|')
        # Parse the last command in the pipe (it's the one that writes)
        last = parts[-1].strip()
        parsed = _parse_single(last)
        parsed.raw = raw  # keep the full pipe as raw
        return parsed

    try:
        tokens = shlex.split(raw)
    except ValueError:
        return ParsedCommand(raw=raw, program="", category=CommandCategory.UNKNOWN)

    if not tokens:
        return ParsedCommand(raw=raw, program="", category=CommandCategory.UNKNOWN)

    # Handle sudo — skip it, parse the real command
    if tokens[0] == "sudo":
        tokens = tokens[1:]
        if not tokens:
            return ParsedCommand(raw=raw, program="sudo", category=CommandCategory.SHELL)

    program = tokens[0]
    rest = tokens[1:]

    flags = [t for t in rest if t.startswith("-")]
    args = [t for t in rest if not t.startswith("-")]

    cmd = ParsedCommand(
        raw=raw,
        program=program,
        args=args,
        flags=flags,
    )

    # Categorize
    if program == "git":
        cmd.category = CommandCategory.GIT
        if args:
            cmd.subcommand = args[0]
            cmd.targets = args[1:]
    elif program in FILE_DELETE_PROGRAMS:
        cmd.category = CommandCategory.FILE_DELETE
        cmd.targets = [a for a in args if not a.startswith("-")]
    elif program in FILE_MOVE_PROGRAMS:
        cmd.category = CommandCategory.FILE_MOVE
        cmd.targets = [a for a in args if not a.startswith("-")]
    elif program in FILE_PERM_PROGRAMS:
        cmd.category = CommandCategory.FILE_PERMISSION
        cmd.targets = [a for a in args if not a.startswith("-")]
    elif program in PACKAGE_PROGRAMS:
        cmd.category = CommandCategory.PACKAGE
        if args:
            cmd.subcommand = args[0]
            cmd.targets = args[1:]
    elif program in PROCESS_PROGRAMS:
        cmd.category = CommandCategory.PROCESS
        cmd.targets = args
    elif program in NETWORK_PROGRAMS:
        cmd.category = CommandCategory.NETWORK
        cmd.targets = args
    else:
        cmd.category = CommandCategory.UNKNOWN

    return cmd


def has_force_flags(cmd: ParsedCommand) -> bool:
    """Check if a command has any force/destructive flags."""
    return bool(set(cmd.flags) & FORCE_FLAGS)
