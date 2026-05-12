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
WRITE_REDIRECT_OPERATORS = {">", ">>", ">|", "&>"}
CONTROL_TOKENS = {"|", "&&", "||", ";"}


def parse_command(raw: str) -> list[ParsedCommand]:
    """Parse a raw command string into one or more ParsedCommands.

    Handles chained commands (&&, ||, ;) by splitting and parsing each.
    """
    segments = _split_chains(raw)
    commands = []
    for segment in segments:
        if not segment.strip():
            continue
        base = _parse_single(segment)
        bash_write = _parse_bash_file_write(segment)

        if bash_write and base.category in (
            CommandCategory.UNKNOWN,
            CommandCategory.SHELL,
        ):
            commands.append(bash_write)
        else:
            commands.append(base)
            if bash_write:
                commands.append(bash_write)
    return commands


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


def _parse_bash_file_write(raw: str) -> ParsedCommand | None:
    """Detect shell-level file writes and route them through FILE_WRITE.

    Claude can write files through Bash without using the Write tool, for
    example: cat > file, echo > file, heredoc redirects, or tee file. Those
    patterns need the same verifier path as Write tool calls.
    """
    tokens = _shell_tokens(raw)
    if not tokens:
        return None

    command_tokens = tokens[1:] if tokens[0] == "sudo" else tokens
    if not command_tokens:
        return None

    redirect_targets = _redirect_targets(command_tokens)
    tee_targets = _tee_targets(command_tokens)
    targets = _dedupe([*redirect_targets, *tee_targets])
    if not targets:
        return None

    flags = [
        token
        for token in command_tokens[1:]
        if token.startswith("-") and token not in CONTROL_TOKENS
    ]
    args = [
        token
        for token in command_tokens[1:]
        if token not in CONTROL_TOKENS and not token.startswith("-")
    ]

    return ParsedCommand(
        raw=raw,
        program=command_tokens[0],
        args=args,
        flags=flags,
        category=CommandCategory.FILE_WRITE,
        targets=targets,
        metadata={
            "source": "bash_file_write",
            "content_length": 0,
            "append": _uses_append_redirect(command_tokens),
        },
    )


def _shell_tokens(raw: str) -> list[str]:
    """Tokenize shell syntax while preserving redirection operators."""
    try:
        lexer = shlex.shlex(raw, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        return list(lexer)
    except ValueError:
        return []


def _redirect_targets(tokens: list[str]) -> list[str]:
    targets = []
    for index, token in enumerate(tokens[:-1]):
        if token not in WRITE_REDIRECT_OPERATORS:
            continue
        target = tokens[index + 1]
        if _looks_like_file_target(target):
            targets.append(target)
    return targets


def _tee_targets(tokens: list[str]) -> list[str]:
    targets = []
    index = 0
    while index < len(tokens):
        segment = []
        while index < len(tokens) and tokens[index] != "|":
            segment.append(tokens[index])
            index += 1

        if segment and segment[0] == "tee":
            targets.extend(_tee_segment_targets(segment))

        index += 1
    return targets


def _tee_segment_targets(segment: list[str]) -> list[str]:
    targets = []
    end_of_options = False
    for token in segment[1:]:
        if token == "--":
            end_of_options = True
            continue
        if not end_of_options and token.startswith("-"):
            continue
        if token in WRITE_REDIRECT_OPERATORS or token in CONTROL_TOKENS:
            continue
        if _looks_like_file_target(token):
            targets.append(token)
    return targets


def _looks_like_file_target(token: str) -> bool:
    if not token:
        return False
    if token in CONTROL_TOKENS:
        return False
    if token in WRITE_REDIRECT_OPERATORS or token in {"<", "<<", "<<<"}:
        return False
    if token.startswith("&"):
        return False
    return True


def _uses_append_redirect(tokens: list[str]) -> bool:
    if ">>" in tokens:
        return True
    for segment in _pipe_segments(tokens):
        if segment and segment[0] == "tee" and any(
            token in ("-a", "--append") for token in segment[1:]
        ):
            return True
    return False


def _pipe_segments(tokens: list[str]) -> list[list[str]]:
    segments = []
    current = []
    for token in tokens:
        if token == "|":
            if current:
                segments.append(current)
            current = []
        else:
            current.append(token)
    if current:
        segments.append(current)
    return segments


def _dedupe(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def has_force_flags(cmd: ParsedCommand) -> bool:
    """Check if a command has any force/destructive flags."""
    return bool(set(cmd.flags) & FORCE_FLAGS)
