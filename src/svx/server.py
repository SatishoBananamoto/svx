"""MCP server for svx — exposes safety assessment as tools Claude can call."""

from __future__ import annotations

from mcp.server import FastMCP

from .parser import parse_command
from .schemas import CommandCategory, ParsedCommand, Verdict
from .snapshot import capture
from .simulator import simulate
from .verifier import verify
from .audit import get_audit_dir, log_event

server = FastMCP(
    name="svx",
    instructions=(
        "svx (Simulate, Verify, Execute) is a safety layer for coding agents. "
        "Use these tools to assess risk BEFORE executing commands or editing files. "
        "Call assess_command before running shell commands, assess_edit before "
        "editing files, and assess_write before creating/overwriting files."
    ),
)


def _format_result(cmd: ParsedCommand, snap, sim, result) -> dict:
    """Format a verification result as a structured dict."""
    return {
        "verdict": result.verdict.value,
        "risk_level": result.risk_level.value,
        "description": sim.description,
        "effects": sim.effects,
        "failure_modes": sim.failure_modes,
        "reversibility": sim.reversibility.value,
        "blast_radius": sim.blast_radius,
        "data_loss_possible": sim.data_loss_possible,
        "reasons": result.reasons,
        "suggestions": result.suggestions,
    }


@server.tool()
def assess_command(command: str, cwd: str | None = None) -> dict:
    """Assess the safety of a shell command before executing it.

    Parses the command, captures the current world state (git status,
    file existence, etc.), simulates what will happen, and returns
    a verdict: allow, confirm, or block.

    Args:
        command: The shell command to analyze (e.g. "git push --force origin main")
        cwd: Working directory (defaults to current directory)
    """
    commands = parse_command(command)
    results = []

    for cmd in commands:
        snap = capture(cmd, cwd=cwd)
        sim = simulate(cmd, snap)
        result = verify(cmd, snap, sim)
        log_event(cmd, snap, result)
        results.append(_format_result(cmd, snap, sim, result))

    # Return worst verdict across chained commands
    verdicts = [r["verdict"] for r in results]
    if "block" in verdicts:
        worst = "block"
    elif "confirm" in verdicts:
        worst = "confirm"
    else:
        worst = "allow"

    return {
        "overall_verdict": worst,
        "commands": results,
    }


@server.tool()
def assess_edit(
    file_path: str,
    old_string: str,
    new_string: str,
    cwd: str | None = None,
) -> dict:
    """Assess the safety of a file edit before making it.

    Checks whether the target file exists, if the old_string is present,
    how much of the file is being changed, whether it's a config/sensitive
    file, and whether the file is git-tracked (recoverable).

    Args:
        file_path: Path to the file being edited
        old_string: The text being replaced
        new_string: The replacement text
        cwd: Working directory (defaults to current directory)
    """
    cmd = ParsedCommand(
        raw=f"Edit {file_path}",
        program="Edit",
        category=CommandCategory.FILE_EDIT,
        targets=[file_path],
        metadata={"old_string": old_string, "new_string": new_string},
    )

    snap = capture(cmd, cwd=cwd)
    sim = simulate(cmd, snap)
    result = verify(cmd, snap, sim)
    log_event(cmd, snap, result)

    return _format_result(cmd, snap, sim, result)


@server.tool()
def assess_write(
    file_path: str,
    content_length: int,
    cwd: str | None = None,
) -> dict:
    """Assess the safety of writing/creating a file before doing it.

    Checks whether the file already exists (overwrite risk), whether it's
    git-tracked (recoverable), whether it's a config/sensitive file, and
    the size of the content being written.

    Args:
        file_path: Path to the file being written
        content_length: Length of the content being written (in characters)
        cwd: Working directory (defaults to current directory)
    """
    cmd = ParsedCommand(
        raw=f"Write {file_path}",
        program="Write",
        category=CommandCategory.FILE_WRITE,
        targets=[file_path],
        metadata={"content_length": content_length},
    )

    snap = capture(cmd, cwd=cwd)
    sim = simulate(cmd, snap)
    result = verify(cmd, snap, sim)
    log_event(cmd, snap, result)

    return _format_result(cmd, snap, sim, result)


@server.tool()
def get_audit(count: int = 10) -> dict:
    """Get recent audit log entries.

    Returns the most recent safety assessments that svx has performed,
    including verdicts, risk levels, and the commands/edits that were checked.

    Args:
        count: Number of recent entries to return (default: 10)
    """
    import json
    from pathlib import Path

    log_file = get_audit_dir() / "audit.jsonl"
    if not log_file.exists():
        return {"entries": [], "message": "No audit log found."}

    with open(log_file) as f:
        lines = f.readlines()

    entries = []
    for line in lines[-count:]:
        try:
            entry = json.loads(line)
            entries.append({
                "timestamp": entry.get("timestamp", ""),
                "command": entry.get("command", ""),
                "verdict": entry.get("verdict", ""),
                "risk_level": entry.get("risk_level", ""),
                "reasons": entry.get("reasons", []),
            })
        except json.JSONDecodeError:
            continue

    return {"entries": entries, "total_logged": len(lines)}


def run_server():
    """Entry point for the MCP server."""
    server.run(transport="stdio")
