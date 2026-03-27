"""Translate technical svx verdicts into plain language.

Two modes:
  - "technical": default, for developers (git terminology, exact details)
  - "human": for vibe coders (no jargon, consequences only)

Usage:
    from svx.humanize import explain
    headline, detail = explain(audit_entry, mode="human")
"""

from __future__ import annotations


def explain(entry: dict, mode: str = "human") -> tuple[str, str]:
    """Turn an audit entry into (headline, detail).

    headline: one-line summary of what's happening
    detail: one-line consequence — what you'll lose or why it matters
    """
    if mode == "technical":
        return _technical(entry)
    return _human(entry)


def _human(entry: dict) -> tuple[str, str]:
    """No jargon. Consequences only. Written for someone who
    doesn't know what git, branches, or commits are."""
    cmd = entry.get("command", "")
    parsed = entry.get("parsed", {})
    sim = entry.get("simulation", {})
    category = parsed.get("category", "")
    targets = parsed.get("targets", [])
    flags = parsed.get("flags", [])
    sub = parsed.get("subcommand", "")
    effects = sim.get("effects", [])

    names = _short_names(targets)

    # ── Git ──
    if category == "git":
        if sub == "push":
            forced = _has_force(flags)
            if forced:
                return (
                    "About to overwrite code on the server",
                    "Other people's work could be lost — this can't be undone",
                )
            return "Uploading your code to the server", ""

        if sub == "reset" and "--hard" in flags:
            lost = _extract_lost_files(effects)
            if lost:
                return (
                    "About to erase your recent work",
                    f"These changes will be gone forever: {lost}",
                )
            return (
                "About to erase your recent work",
                "All unsaved changes will be gone forever",
            )

        if sub == "reset":
            return "Reorganizing saved changes (your files are safe)", ""

        if sub in ("checkout", "restore") and "--" in flags:
            return (
                f"About to undo your changes to {names or 'some files'}",
                "Your recent edits to those files will be replaced with the old version",
            )

        if sub in ("checkout", "restore"):
            return "Switching to a different version of the code", ""

        if sub == "clean":
            return (
                "About to delete files that weren't saved",
                "New files you created will be permanently removed",
            )

        if sub == "branch" and "-D" in flags:
            return (
                f"About to delete a version of your project called '{names}'",
                "Any work on it that wasn't merged will be lost",
            )

        if sub == "rebase":
            return (
                "Reorganizing your project history",
                "This can cause problems if others are working on the same code",
            )

        if sub == "stash":
            return (
                "About to throw away saved-aside changes",
                "You won't be able to get them back",
            )

        # Safe git ops
        if sub in ("add", "commit", "pull", "fetch", "merge", "tag", "status",
                    "log", "diff", "show", "branch"):
            return f"Routine operation: git {sub}", ""

        return f"Git operation: {sub or cmd[:40]}", ""

    # ── Deleting files ──
    if category == "file_delete":
        recursive = _has_recursive(flags)
        has_backup = not any("untracked" in e.lower() for e in effects)
        no_exist = all("does not exist" in e.lower() for e in effects)

        if no_exist:
            return f"Tried to delete {names} but it doesn't exist", ""

        if recursive and not has_backup:
            return (
                f"About to permanently delete {names} and everything inside",
                "Some of these have no backup — once deleted, they're gone",
            )
        if recursive:
            return (
                f"About to delete {names} and everything inside",
                "You can recover these from your project history if needed",
            )
        return f"About to delete {names}", ""

    # ── Editing files ──
    if category == "file_edit":
        fname = _short_names(targets[:1])
        is_config = any(
            w in e.lower() for e in effects
            for w in ["config", "sensitive", ".env", "secret"]
        )
        is_major = any("Major rewrite" in e for e in effects)

        if is_config and is_major:
            return (
                f"About to rewrite most of {fname} — this controls your app's settings",
                "A mistake here could break your app or expose passwords",
            )
        if is_config:
            return (
                f"About to change {fname} — this is a settings or secrets file",
                "This controls how your app connects, runs, or authenticates",
            )
        if is_major:
            return (
                f"About to rewrite most of {fname}",
                "This is a big change — review carefully before accepting",
            )
        return f"Editing {fname}", ""

    # ── Overwriting files ──
    if category == "file_write":
        fname = _short_names(targets[:1])
        is_overwrite = any("OVERWRITE" in e for e in effects)
        no_backup = any("permanently lost" in e.lower() for e in effects)

        if is_overwrite and no_backup:
            return (
                f"About to replace {fname} — the old version has no backup",
                "Once overwritten, there's no way to get the original back",
            )
        if is_overwrite:
            return (
                f"About to replace all of {fname}",
                "The old version is saved in your project history",
            )
        return f"Creating new file: {fname}", ""

    # ── Installing/removing packages ──
    if category == "package":
        if sub in ("install", "add", "i"):
            return f"Installing software: {names or 'from project file'}", ""
        if sub in ("uninstall", "remove", "rm"):
            return f"Removing software: {names}", ""
        return f"Managing software: {sub}", ""

    # ── Killing programs ──
    if category == "process":
        if "-9" in flags or "-KILL" in flags:
            return (
                "About to force-stop a running program",
                "If it was doing something important, that work is lost",
            )
        return "Stopping a running program", ""

    # ── Fallback ──
    # Common harmless commands — keep it simple
    first_word = cmd.strip().split()[0] if cmd.strip() else ""
    simple_cmds = {
        "cd": "Changing directory",
        "ls": "Listing files",
        "echo": "Printing text",
        "cat": "Reading a file",
        "pwd": "Checking current directory",
        "mkdir": "Creating a folder",
        "touch": "Creating an empty file",
        "cp": "Copying files",
        "head": "Reading start of a file",
        "tail": "Reading end of a file",
        "grep": "Searching in files",
        "find": "Finding files",
        "which": "Looking up a command",
        "python3": "Running Python",
        "python": "Running Python",
        "node": "Running Node.js",
        "npm": "Running npm",
        "git": "Git operation",
        "wc": "Counting lines/words",
    }
    if first_word in simple_cmds:
        return simple_cmds[first_word], ""

    desc = sim.get("description", "")
    if desc:
        return desc, ""
    return cmd[:50], ""


def _technical(entry: dict) -> tuple[str, str]:
    """Developer-facing: uses git terminology, exact details."""
    cmd = entry.get("command", "")
    sim = entry.get("simulation", {})
    reasons = entry.get("reasons", [])
    desc = sim.get("description", cmd[:60])
    detail = reasons[0] if reasons else ""
    return desc, detail


# ── Helpers ──

def _short_names(targets: list[str]) -> str:
    """Extract just filenames from paths."""
    if not targets:
        return ""
    names = [t.rsplit("/", 1)[-1] for t in targets[:3]]
    result = ", ".join(names)
    if len(targets) > 3:
        result += f" (+{len(targets) - 3} more)"
    return result


def _has_force(flags: list[str]) -> bool:
    return any(f in flags for f in ["--force", "-f", "--force-with-lease"])


def _has_recursive(flags: list[str]) -> bool:
    return any(f in flags for f in ["-r", "-rf", "-fr", "-R"])


def _extract_lost_files(effects: list[str]) -> str:
    """Pull out file names from effects like 'Unstaged changes lost: ...'"""
    for e in effects:
        if "changes lost:" in e:
            after = e.split(":", 1)[1].strip()
            # Parse git diff --stat output into clean file names
            # Format: "file.py | 25 ----" or "file.py | 3 +-"
            files = []
            for part in after.split("\n"):
                part = part.strip()
                if "|" in part:
                    fname = part.split("|")[0].strip()
                    if fname and not fname.startswith(("warning", "error", "usage")):
                        files.append(fname)
            if files:
                return ", ".join(files[:5])
            # Fallback: just truncate
            if len(after) > 60:
                after = after[:57] + "..."
            return after
    return ""
