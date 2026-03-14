"""CLI entry point for svx — standalone and Claude Code hook mode."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .parser import parse_command
from .snapshot import capture
from .simulator import simulate
from .verifier import verify
from .audit import log_event
from .schemas import Verdict, RiskLevel


# ANSI colors
RED = "\033[91m"
YELLOW = "\033[93m"
GREEN = "\033[92m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"

VERDICT_STYLE = {
    Verdict.ALLOW: (GREEN, "ALLOW"),
    Verdict.CONFIRM: (YELLOW, "CONFIRM"),
    Verdict.BLOCK: (RED, "BLOCK"),
}

RISK_STYLE = {
    RiskLevel.NONE: (DIM, "NONE"),
    RiskLevel.LOW: (GREEN, "LOW"),
    RiskLevel.MEDIUM: (YELLOW, "MEDIUM"),
    RiskLevel.HIGH: (RED, "HIGH"),
    RiskLevel.CRITICAL: (RED + BOLD, "CRITICAL"),
}


def main():
    parser = argparse.ArgumentParser(
        prog="svx",
        description="Simulate, Verify, Execute — safety layer for coding agents",
    )
    sub = parser.add_subparsers(dest="subcommand")

    # svx check "command"
    check_parser = sub.add_parser("check", help="Analyze a command before execution")
    check_parser.add_argument("command", help="The shell command to analyze")
    check_parser.add_argument("--cwd", help="Working directory", default=None)
    check_parser.add_argument("--json", dest="as_json", action="store_true", help="Output as JSON")
    check_parser.add_argument("--quiet", action="store_true", help="Exit code only")
    check_parser.add_argument("--policies", help="Path to policies YAML", default=None)

    # svx hook (reads from stdin — for Claude Code integration)
    sub.add_parser("hook", help="Run as Claude Code pre-tool hook (reads stdin)")

    # svx audit (view audit log)
    audit_parser = sub.add_parser("audit", help="View audit log")
    audit_parser.add_argument("--tail", type=int, default=10, help="Number of entries")

    args = parser.parse_args()

    if args.subcommand == "check":
        _cmd_check(args)
    elif args.subcommand == "hook":
        _cmd_hook()
    elif args.subcommand == "audit":
        _cmd_audit(args)
    else:
        parser.print_help()
        sys.exit(0)


def _cmd_check(args):
    """Analyze a command and print the verdict."""
    commands = parse_command(args.command)
    policies_path = Path(args.policies) if args.policies else None
    worst_verdict = Verdict.ALLOW

    for cmd in commands:
        snap = capture(cmd, cwd=args.cwd)
        sim = simulate(cmd, snap)
        result = verify(cmd, snap, sim, policies_path=policies_path)

        # Log it
        log_event(cmd, snap, result)

        if args.as_json:
            print(json.dumps(_result_to_dict(cmd, result), indent=2))
        elif not args.quiet:
            _print_result(cmd, result)

        # Track worst verdict
        if result.verdict == Verdict.BLOCK:
            worst_verdict = Verdict.BLOCK
        elif result.verdict == Verdict.CONFIRM and worst_verdict != Verdict.BLOCK:
            worst_verdict = Verdict.CONFIRM

    # Exit codes: 0=allow, 1=confirm, 2=block
    if worst_verdict == Verdict.BLOCK:
        sys.exit(2)
    elif worst_verdict == Verdict.CONFIRM:
        sys.exit(1)
    sys.exit(0)


def _cmd_hook():
    """Run as a Claude Code PreToolUse hook.

    Reads hook input from stdin as JSON:
      {"tool_name": "Bash", "tool_input": {"command": "..."}, "hook_event_name": "PreToolUse"}

    Output JSON to stdout:
      - {"decision": "allow"} to allow
      - {"decision": "block", "reason": "..."} to block
      - {"decision": "allow", "systemMessage": "..."} to allow with warning

    Exit 0 always — decisions communicated via JSON output.
    """
    try:
        raw_input = sys.stdin.read()
        hook_input = json.loads(raw_input)
    except (json.JSONDecodeError, ValueError):
        # Can't parse — fail open
        print(json.dumps({}))
        sys.exit(0)

    # Only intercept Bash tool calls
    tool_name = hook_input.get("tool_name", "")
    if tool_name != "Bash":
        print(json.dumps({}))
        sys.exit(0)

    tool_input = hook_input.get("tool_input", {})
    command = tool_input.get("command", "")
    if not command:
        print(json.dumps({}))
        sys.exit(0)

    commands = parse_command(command)
    worst_verdict = Verdict.ALLOW
    all_reasons = []
    all_suggestions = []

    for cmd in commands:
        snap = capture(cmd)
        sim = simulate(cmd, snap)
        result = verify(cmd, snap, sim)
        log_event(cmd, snap, result)

        if result.verdict == Verdict.BLOCK:
            worst_verdict = Verdict.BLOCK
        elif result.verdict == Verdict.CONFIRM and worst_verdict != Verdict.BLOCK:
            worst_verdict = Verdict.CONFIRM

        all_reasons.extend(result.reasons)
        all_suggestions.extend(result.suggestions)

    if worst_verdict == Verdict.BLOCK:
        msg = "[svx BLOCK] " + " | ".join(all_reasons[:3])
        if all_suggestions:
            msg += "\nSuggestions: " + " | ".join(all_suggestions[:2])
        print(json.dumps({"decision": "block", "reason": msg}))
    elif worst_verdict == Verdict.CONFIRM:
        msg = "[svx] " + " | ".join(all_reasons[:3])
        if all_suggestions:
            msg += "\nSuggestions: " + " | ".join(all_suggestions[:2])
        print(json.dumps({"systemMessage": msg}))
    else:
        print(json.dumps({}))

    sys.exit(0)


def _cmd_audit(args):
    """Print recent audit entries."""
    log_file = Path.cwd() / ".svx-audit" / "audit.jsonl"
    if not log_file.exists():
        print("No audit log found.")
        return

    with open(log_file) as f:
        lines = f.readlines()

    entries = lines[-args.tail:]
    for line in entries:
        entry = json.loads(line)
        v = entry.get("verdict", "?")
        risk = entry.get("risk_level", "?")
        cmd = entry.get("command", "?")
        ts = entry.get("timestamp", "?")[:19]

        color = GREEN if v == "allow" else YELLOW if v == "confirm" else RED
        print(f"  {DIM}{ts}{RESET}  {color}{v.upper():>7}{RESET}  {risk:<8}  {cmd}")


def _print_result(cmd, result):
    """Pretty-print a verification result."""
    v_color, v_label = VERDICT_STYLE[result.verdict]
    r_color, r_label = RISK_STYLE[result.risk_level]

    print()
    print(f"  {BOLD}svx{RESET} {DIM}~{RESET} {cmd.raw}")
    print(f"  {'─' * 60}")
    print(f"  Verdict:  {v_color}{v_label}{RESET}")
    print(f"  Risk:     {r_color}{r_label}{RESET}")
    print(f"  {DIM}{result.simulation.description}{RESET}")

    if result.simulation.effects:
        print(f"  Effects:")
        for e in result.simulation.effects[:5]:
            print(f"    {DIM}→{RESET} {e}")

    if result.reasons:
        print(f"  Reasons:")
        for r in result.reasons:
            print(f"    {YELLOW}!{RESET} {r}")

    if result.suggestions:
        print(f"  Suggestions:")
        for s in result.suggestions:
            print(f"    {GREEN}>{RESET} {s}")

    print()


def _format_hook_message(cmd, result):
    """Format a message for Claude Code hook output."""
    lines = [
        f"[svx] {result.verdict.value.upper()}: {result.simulation.description}",
    ]
    for r in result.reasons:
        lines.append(f"  ! {r}")
    for s in result.suggestions:
        lines.append(f"  > {s}")
    return "\n".join(lines)


def _result_to_dict(cmd, result):
    """Convert result to JSON-serializable dict."""
    return {
        "command": cmd.raw,
        "program": cmd.program,
        "category": cmd.category.value,
        "verdict": result.verdict.value,
        "risk_level": result.risk_level.value,
        "simulation": {
            "description": result.simulation.description,
            "effects": result.simulation.effects,
            "failure_modes": result.simulation.failure_modes,
            "reversibility": result.simulation.reversibility.value,
            "blast_radius": result.simulation.blast_radius,
            "data_loss_possible": result.simulation.data_loss_possible,
        },
        "reasons": result.reasons,
        "suggestions": result.suggestions,
    }


if __name__ == "__main__":
    main()
