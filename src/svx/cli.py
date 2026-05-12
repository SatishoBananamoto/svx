"""CLI entry point for svx — standalone and Claude Code hook mode."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .parser import parse_command
from .snapshot import capture
from .simulator import simulate
from .verifier import verify
from .audit import get_audit_dir, log_event
from .config import (
    find_svx_root,
    is_disabled_by_env,
    is_paused,
    load_config,
    load_project_config,
    save_project_config,
)
from .schemas import Verdict, RiskLevel, ParsedCommand, CommandCategory
from .hook_config import (
    disable_svx_hook,
    enable_svx_hook,
    load_settings,
    save_settings,
    settings_path,
)


# ANSI colors
RED = "\033[91m"
YELLOW = "\033[93m"
GREEN = "\033[92m"
CYAN = "\033[96m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"
BG_RED = "\033[41m"
BG_YELLOW = "\033[43m"
WHITE = "\033[97m"

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

    # svx init (initialize .svx/ in current project)
    init_parser = sub.add_parser("init", help="Initialize SVX guarding for this project")
    init_parser.add_argument(
        "--mode", choices=["vibe", "strict"], default="vibe",
        help="vibe = only block catastrophic; strict = confirm risky ops"
    )

    # svx enable/disable (manage local Claude Code hook settings)
    sub.add_parser("enable", help="Enable the SVX Claude Code hook in this project")
    sub.add_parser("disable", help="Disable the SVX Claude Code hook in this project")
    sub.add_parser("pause", help="Temporarily pause the SVX hook in this project")
    sub.add_parser("resume", help="Resume the SVX hook in this project")

    # svx serve (MCP server mode)
    sub.add_parser("serve", help="Run as MCP server (stdio transport)")

    # svx watch (live dashboard)
    watch_parser = sub.add_parser("watch", help="Live dashboard — tail audit log")
    watch_parser.add_argument(
        "--dir", help="Audit directory to watch", default=None
    )
    watch_parser.add_argument(
        "--technical", action="store_true",
        help="Show raw technical output instead of plain language",
    )

    args = parser.parse_args()

    if args.subcommand == "check":
        _cmd_check(args)
    elif args.subcommand == "hook":
        _cmd_hook()
    elif args.subcommand == "init":
        _cmd_init(args)
    elif args.subcommand == "enable":
        _cmd_enable()
    elif args.subcommand == "disable":
        _cmd_disable()
    elif args.subcommand == "pause":
        _cmd_pause()
    elif args.subcommand == "resume":
        _cmd_resume()
    elif args.subcommand == "audit":
        _cmd_audit(args)
    elif args.subcommand == "serve":
        _cmd_serve()
    elif args.subcommand == "watch":
        _cmd_watch(args)
    else:
        parser.print_help()
        sys.exit(0)


def _cmd_init(args):
    """Initialize SVX guarding for the current project."""
    svx_dir = Path.cwd() / ".svx"
    if svx_dir.exists():
        print(f"{YELLOW}svx already initialized in {Path.cwd()}{RESET}")
        sys.exit(0)

    svx_dir.mkdir()
    config_path = svx_dir / "config.yaml"
    config_path.write_text(f"mode: {args.mode}\n")

    # Add .svx/ to .gitignore if git repo
    gitignore = Path.cwd() / ".gitignore"
    if (Path.cwd() / ".git").exists():
        existing = gitignore.read_text() if gitignore.exists() else ""
        if ".svx/" not in existing:
            with open(gitignore, "a") as f:
                if existing and not existing.endswith("\n"):
                    f.write("\n")
                f.write(".svx/\n")

    print(f"{GREEN}svx initialized{RESET} in {Path.cwd()}")
    print(f"  Mode: {args.mode}")
    print(f"  Config: {config_path}")
    if args.mode == "vibe":
        print(f"  {DIM}Only catastrophic operations will be blocked.{RESET}")
    else:
        print(f"  {DIM}Risky operations will require confirmation.{RESET}")


def _cmd_enable():
    """Enable SVX as a local Claude Code PreToolUse hook."""
    path = settings_path(Path.cwd())
    try:
        settings = load_settings(path)
        updated, added = enable_svx_hook(settings)
        backup_path = save_settings(path, updated)
    except ValueError as exc:
        print(f"{RED}svx enable failed:{RESET} {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"{GREEN}svx hook enabled{RESET} in {path}")
    if added:
        print(f"  Added PreToolUse hooks for: {', '.join(added)}")
    else:
        print("  SVX hooks were already present.")
    if backup_path:
        print(f"  Backup: {backup_path}")
    print(f"  {DIM}Verify in Claude Code with /hooks.{RESET}")


def _cmd_disable():
    """Disable SVX local Claude Code PreToolUse hooks."""
    path = settings_path(Path.cwd())
    if not path.exists():
        print(f"{YELLOW}svx hook already disabled{RESET} ({path} does not exist)")
        return

    try:
        settings = load_settings(path)
        updated, removed = disable_svx_hook(settings)
        backup_path = save_settings(path, updated)
    except ValueError as exc:
        print(f"{RED}svx disable failed:{RESET} {exc}", file=sys.stderr)
        sys.exit(1)

    if removed:
        print(f"{GREEN}svx hook disabled{RESET} in {path}")
        print(f"  Removed {removed} hook handler(s).")
    else:
        print(f"{YELLOW}svx hook was not present{RESET} in {path}")
    if backup_path:
        print(f"  Backup: {backup_path}")


def _cmd_pause():
    """Pause SVX hook enforcement for the current project."""
    _set_project_paused(True)


def _cmd_resume():
    """Resume SVX hook enforcement for the current project."""
    _set_project_paused(False)


def _set_project_paused(paused: bool):
    root = _find_svx_root(Path.cwd())
    if root is None:
        action = "pause" if paused else "resume"
        print(
            f"{RED}svx {action} failed:{RESET} run 'svx init' first",
            file=sys.stderr,
        )
        sys.exit(1)

    config = load_project_config(root)
    config["paused"] = paused
    path = save_project_config(config, root)
    if paused:
        print(f"{YELLOW}svx paused{RESET} for {root}")
        print(f"  Config: {path}")
        print(f"  {DIM}Run 'svx resume' to re-enable hook checks.{RESET}")
    else:
        print(f"{GREEN}svx resumed{RESET} for {root}")
        print(f"  Config: {path}")


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


_INTERCEPTED_TOOLS = {"Bash", "Edit", "Write"}


def _any_target_in_svx_project(commands: list) -> bool:
    """Check if any command targets a file inside a .svx/-initialized project."""
    return bool(_svx_roots_for_targets(commands))


def _svx_roots_for_targets(commands: list) -> list[Path]:
    """Return unique .svx project roots for command targets."""
    roots = []
    for cmd in commands:
        for target in cmd.targets:
            path = Path(target).resolve() if target else None
            root = _find_svx_root(path) if path else None
            if root and root not in roots:
                roots.append(root)
    return roots


def _find_svx_root(path: Path) -> Path | None:
    """Walk up from path looking for a .svx/ directory."""
    return find_svx_root(path)


def _cmd_hook():
    """Run as a Claude Code PreToolUse hook.

    Reads hook input from stdin as JSON:
      {"tool_name": "...", "tool_input": {...}, "hook_event_name": "PreToolUse"}

    Intercepts: Bash, Edit, Write tool calls.

    Output JSON to stdout using hookSpecificOutput format:
      - permissionDecision: "allow" | "deny" | "ask"

    Exit 0 always — decisions communicated via JSON output.
    """
    try:
        raw_input = sys.stdin.read()
        hook_input = json.loads(raw_input)
    except (json.JSONDecodeError, ValueError):
        # Can't parse — fail open
        print(json.dumps({}))
        sys.exit(0)

    tool_name = hook_input.get("tool_name", "")
    if tool_name not in _INTERCEPTED_TOOLS:
        print(json.dumps({}))
        sys.exit(0)

    tool_input = hook_input.get("tool_input", {})

    if is_disabled_by_env():
        print(json.dumps({}))
        sys.exit(0)

    # Build command list depending on tool type
    if tool_name == "Bash":
        command = tool_input.get("command", "")
        if not command:
            print(json.dumps({}))
            sys.exit(0)
        commands = parse_command(command)
    elif tool_name == "Edit":
        commands = [_parse_edit_tool(tool_input)]
    elif tool_name == "Write":
        commands = [_parse_write_tool(tool_input)]
    else:
        print(json.dumps({}))
        sys.exit(0)

    # Project scoping: only guard directories with .svx/ present.
    # No .svx/ directory = no guarding. Like git without .git/.
    roots = _svx_roots_for_targets(commands)
    if not roots:
        print(json.dumps({}))
        sys.exit(0)

    from .schemas import DenyKind

    configs = [load_config(cwd=root) for root in roots]
    active_configs = [
        config
        for config in configs
        if config.get("mode") != "passthrough" and not is_paused(config)
    ]
    if not active_configs:
        print(json.dumps({}))
        sys.exit(0)

    vibe = all(config.get("mode") == "vibe" for config in active_configs)
    worst_verdict = Verdict.ALLOW
    worst_cmd = None
    worst_result = None

    for cmd in commands:
        snap = capture(cmd)
        sim = simulate(cmd, snap)
        result = verify(cmd, snap, sim)

        # Loop prevention: if same command denied 3+ times recently, escalate
        if result.verdict in (Verdict.CONFIRM, Verdict.BLOCK):
            if result.deny_kind == DenyKind.ADVISORY and _check_retry_count(cmd.raw):
                result.deny_kind = DenyKind.HARD
                result.advisory_action = None
                result.reasons.insert(0, "Denied multiple times — try a different approach")

        log_event(cmd, snap, result)

        if result.verdict == Verdict.BLOCK:
            worst_verdict = Verdict.BLOCK
            worst_cmd = cmd
            worst_result = result
        elif result.verdict == Verdict.CONFIRM and worst_verdict != Verdict.BLOCK:
            worst_verdict = Verdict.CONFIRM
            worst_cmd = cmd
            worst_result = result

    # Vibe mode: only BLOCK verdicts deny. CONFIRM becomes allow-with-log.
    if vibe and worst_verdict == Verdict.CONFIRM:
        worst_verdict = Verdict.ALLOW

    _emit_hook_output(worst_verdict, worst_cmd, worst_result)
    sys.exit(0)


def _parse_edit_tool(tool_input: dict) -> ParsedCommand:
    """Convert an Edit tool call into a ParsedCommand."""
    file_path = tool_input.get("file_path", "")
    old_string = tool_input.get("old_string", "")
    new_string = tool_input.get("new_string", "")

    return ParsedCommand(
        raw=f"Edit {file_path}",
        program="Edit",
        category=CommandCategory.FILE_EDIT,
        targets=[file_path],
        metadata={
            "old_string": old_string,
            "new_string": new_string,
        },
    )


def _parse_write_tool(tool_input: dict) -> ParsedCommand:
    """Convert a Write tool call into a ParsedCommand."""
    file_path = tool_input.get("file_path", "")
    content = tool_input.get("content", "")

    return ParsedCommand(
        raw=f"Write {file_path}",
        program="Write",
        category=CommandCategory.FILE_WRITE,
        targets=[file_path],
        metadata={
            "content_length": len(content),
        },
    )


def _stderr_alarm(cmd: ParsedCommand, result: VerificationResult) -> None:
    """Print a visible alarm to stderr so the user sees it in their terminal."""
    sim = result.simulation
    is_block = result.verdict == Verdict.BLOCK

    if is_block:
        header_color = f"{BG_RED}{WHITE}{BOLD}"
        icon = "##"
        label = "BLOCKED"
    else:
        header_color = f"{BG_YELLOW}{BOLD}"
        icon = "!!"
        label = "RISKY"

    # Build the alarm
    lines = [
        "",
        f"  {header_color} {icon} svx: {label} {RESET}  {BOLD}{cmd.raw}{RESET}",
    ]

    # Lead with the most important effect (what you'll lose)
    danger_effects = [
        e for e in sim.effects
        if any(w in e.upper() for w in ["LOST", "DESTROY", "OVERWRITE", "DELETE", "PERMANENTLY", "WARNING"])
    ]
    if danger_effects:
        for e in danger_effects[:2]:
            lines.append(f"  {RED}  -> {e}{RESET}")
    elif sim.effects:
        lines.append(f"  {YELLOW}  -> {sim.effects[0]}{RESET}")

    # Show the consequence
    if sim.failure_modes:
        lines.append(f"  {DIM}  -> {sim.failure_modes[0]}{RESET}")

    # One suggestion — the safer alternative
    if result.suggestions:
        # Pick the most actionable suggestion (has a command in it)
        actionable = [s for s in result.suggestions if "'" in s or "`" in s]
        suggestion = actionable[0] if actionable else result.suggestions[0]
        lines.append(f"  {GREEN}  >> {suggestion}{RESET}")

    lines.append("")

    print("\n".join(lines), file=sys.stderr)


def _check_retry_count(
    cmd_raw: str, max_retries: int = 3, window_sec: int = 60
) -> bool:
    """Return True if this command has been denied too many times recently."""
    from datetime import datetime, timezone
    audit_file = get_audit_dir() / "audit.jsonl"
    if not audit_file.exists():
        return False

    now = datetime.now(timezone.utc)
    count = 0
    try:
        with open(audit_file) as f:
            # Read last 50 lines max for performance
            lines = f.readlines()[-50:]
        for line in reversed(lines):
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("command") != cmd_raw:
                continue
            if entry.get("verdict") not in ("confirm", "block"):
                continue
            ts = entry.get("timestamp", "")
            try:
                entry_time = datetime.fromisoformat(ts)
                age = (now - entry_time).total_seconds()
                if age > window_sec:
                    break
                count += 1
            except (ValueError, TypeError):
                continue
    except (OSError, IOError):
        return False

    return count >= max_retries


def _emit_hook_output(
    verdict: Verdict,
    cmd: ParsedCommand | None,
    result: VerificationResult | None,
) -> None:
    """Print the hookSpecificOutput JSON to stdout.

    Advisor mode: both CONFIRM and BLOCK map to 'deny'.
    - CONFIRM + advisory: deny with actionable instruction for Claude
    - CONFIRM + hard: deny with no workaround
    - BLOCK: deny with hard stop
    """
    from .schemas import DenyKind

    if verdict == Verdict.ALLOW or not result or not cmd:
        print(json.dumps({}))
        return

    # Everything non-ALLOW is a deny — Claude gets the feedback
    sim = result.simulation

    if result.deny_kind == DenyKind.ADVISORY and result.advisory_action:
        # Advisory: tell Claude what to do, then retry
        msg = f"[svx] {sim.description}. Action needed: {result.advisory_action}"
    elif result.verdict == Verdict.BLOCK:
        # Hard block: no workaround
        msg = f"[svx BLOCKED] {sim.description}. This action is not allowed."
        if result.reasons:
            msg += f" {result.reasons[0]}."
    else:
        # Hard deny (CONFIRM without advisory): no safe workaround
        msg = f"[svx] {sim.description}. This action is too risky in the current context."
        if result.reasons:
            msg += f" {result.reasons[0]}."

    output = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": msg,
        }
    }
    print(json.dumps(output))


def _cmd_serve():
    """Run svx as an MCP server over stdio."""
    from .server import run_server
    run_server()


def _cmd_audit(args):
    """Print recent audit entries."""
    log_file = get_audit_dir() / "audit.jsonl"
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


def _cmd_watch(args):
    """Live dashboard — tails audit log and displays events as they happen."""
    # Find audit file — centralized in ~/.svx-audit/
    search_dirs = []
    if args.dir:
        search_dirs.append(Path(args.dir))
    else:
        search_dirs.append(get_audit_dir())

    log_files = [d / "audit.jsonl" for d in search_dirs if (d / "audit.jsonl").exists()]

    # Fallback: watch the configured audit path if nothing exists yet
    if not log_files:
        fallback = get_audit_dir() / "audit.jsonl"
        log_files = [fallback]

    # Print header
    print(flush=True)
    print(f"  {BOLD}{CYAN}svx watch{RESET}  {DIM}— live safety dashboard{RESET}", flush=True)
    print(f"  {DIM}{'─' * 56}{RESET}", flush=True)
    for lf in log_files:
        print(f"  {DIM}Watching: {lf.parent}{RESET}", flush=True)
    if not any(lf.exists() for lf in log_files):
        print(f"  {DIM}No audit log yet. Waiting for svx activity...{RESET}", flush=True)
    print(flush=True)
    print(f"  {DIM}Sit tight — I'll let you know when something catches fire{RESET}", flush=True)
    print(flush=True)

    # Humanize mode
    explain_mode = "technical" if args.technical else "human"

    # Session counters
    counts = {"allow": 0, "confirm": 0, "block": 0}

    # Track file positions — start from end of existing files
    file_positions = {}
    for lf in log_files:
        if lf.exists():
            file_positions[str(lf)] = lf.stat().st_size
        else:
            file_positions[str(lf)] = 0

    try:
        while True:
            for lf in log_files:
                lf_str = str(lf)
                if not lf.exists():
                    continue

                current_size = lf.stat().st_size
                last_pos = file_positions.get(lf_str, 0)

                if current_size > last_pos:
                    with open(lf) as f:
                        f.seek(last_pos)
                        new_lines = f.readlines()
                    file_positions[lf_str] = current_size

                    for line in new_lines:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entry = json.loads(line)
                        except json.JSONDecodeError:
                            continue

                        _watch_render_entry(entry, counts, explain_mode)

            time.sleep(0.5)
    except KeyboardInterrupt:
        _watch_print_summary(counts)


def _watch_render_entry(entry: dict, counts: dict, mode: str = "human") -> None:
    """Render a single audit entry in the watch dashboard."""
    from .humanize import explain

    v = entry.get("verdict", "?")
    cmd = entry.get("command", "?")
    ts = entry.get("timestamp", "")
    deny_kind = entry.get("deny_kind")
    advisory = entry.get("advisory_action")

    # Update counters
    if v in counts:
        counts[v] += 1

    # Format timestamp (just time, not date)
    time_str = ts[11:19] if len(ts) >= 19 else ts

    headline, detail = explain(entry, mode=mode)

    if v == "allow":
        print(f"  {DIM}{time_str}  {GREEN}OK{RESET}  {DIM}{headline[:65]}{RESET}", flush=True)
    elif v == "confirm" and deny_kind == "advisory" and advisory:
        # Advisory deny — svx told Claude what to do
        print(f"  {DIM}{time_str}{RESET}  {BG_YELLOW}{BOLD} !! {RESET}  {BOLD}{headline}{RESET}", flush=True)
        print(f"  {' ' * 10}  {CYAN}>> Told Claude: {advisory}{RESET}", flush=True)
        print(f"  {' ' * 10}  {DIM}({cmd[:70]}){RESET}", flush=True)
        print(flush=True)
    elif v == "confirm":
        # Hard deny (no advisory available)
        print(f"  {DIM}{time_str}{RESET}  {BG_YELLOW}{BOLD} !! {RESET}  {BOLD}{headline}{RESET}", flush=True)
        if detail:
            print(f"  {' ' * 10}  {YELLOW}{detail}{RESET}", flush=True)
        print(f"  {' ' * 10}  {DIM}({cmd[:70]}){RESET}", flush=True)
        print(flush=True)
    elif v == "block":
        print(f"  {DIM}{time_str}{RESET}  {BG_RED}{WHITE}{BOLD} ## {RESET}  {BOLD}{headline}{RESET}", flush=True)
        if detail:
            print(f"  {' ' * 10}  {RED}{detail}{RESET}", flush=True)
        print(f"  {' ' * 10}  {DIM}({cmd[:70]}){RESET}", flush=True)
        print(flush=True)


def _watch_print_summary(counts: dict) -> None:
    """Print session summary when watch exits."""
    total = sum(counts.values())
    if total == 0:
        print(f"\n  {DIM}No activity recorded.{RESET}\n")
        return

    print()
    print(f"  {BOLD}Session Summary{RESET}")
    print(f"  {DIM}{'─' * 40}{RESET}")
    print(f"  {GREEN}{counts['allow']:>4} allowed{RESET}   "
          f"{YELLOW}{counts['confirm']:>4} advised{RESET}   "
          f"{RED}{counts['block']:>4} blocked{RESET}")
    print(f"  {DIM}{total} total actions assessed{RESET}")
    print()


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
