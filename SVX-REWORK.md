# SVX v0.3 Rework Brief

> Context document for the next Claude Code session. Read this first.

## What SVX Is

SVX (Simulate, Verify, Execute) is a deterministic safety layer for AI coding agents. No LLM calls — pure rule-based analysis. It intercepts tool calls (Bash, Edit, Write) via Claude Code PreToolUse hooks, simulates the impact, and decides: allow, confirm, or block.

**Repo**: https://github.com/SatishoBananamoto/svx
**Current**: v0.2.0 — 4 commits, 11 modules, 2,539 LOC, 53/54 tests passing (1 flaky)

## What's Wrong (from real usage building scroll)

SVX was active as a hook while building the `scroll` project (a new, empty project). It blocked nearly everything and forced workarounds. The problems are logged in engram as MST-007.

### Problem 1: No Context Awareness
Creating `pyproject.toml` in a brand new empty directory was blocked. There's nothing to protect — the directory was just created. SVX treats every file operation the same regardless of whether the target exists, is in a git repo, or has any risk.

### Problem 2: Stateless Read-First Check
For Write tool calls, SVX says "Read the file first to verify its current content." But it doesn't actually track whether a Read happened. It blocks every Write regardless. The agent reads the file, retries the Write, and gets blocked again with the same message.

### Problem 3: Binary Decisions
Everything is either allowed or blocked. No graduated responses. A 2% edit to a markdown file gets the same treatment as `rm -rf /`. The `config_file_edits: true` policy flags ALL config file edits, but a `.gitignore` is not `settings.json`.

### Problem 4: Inconsistent Boundary
SVX hooks Write and Edit tools. When blocked, the agent falls back to Bash (`cat > file`, `sed -i`). Bash is also hooked but the same operations pass through because the Bash parser doesn't catch file-creation-via-heredoc the same way. **The safety boundary has a hole that makes the agent less auditable, not more safe.**

### Problem 5: Hook Survives MCP Disconnect
User disconnected the SVX MCP server. The PreToolUse hook kept running because it's a separate CLI binary (`/home/satishocoin/.local/bin/svx hook`). No way to disable it without editing `settings.local.json`.

### Problem 6: `config_file_edits` Is Too Broad
The verifier flags ANY edit to a "config file" (pyproject.toml, .gitignore, __init__.py, settings.json). These have wildly different risk profiles. `.gitignore` in a new project is zero risk. `settings.json` with credentials is high risk. Same policy covers both.

## Current Architecture

```
User/Agent → Tool Call (Bash/Edit/Write)
  → Claude Code PreToolUse hook
    → svx hook (stdin: JSON)
      → parser.py: extract command/targets
      → snapshot.py: capture world state (file exists? git status?)
      → simulator.py: predict effects
      → verifier.py: apply policies → verdict
      → audit.py: log the decision
    → stdout: hookSpecificOutput JSON
  → Claude Code: allow / deny / ask
```

**Key files:**
- `cli.py` (594 LOC): CLI + hook entry point. `_cmd_hook()` is the hook handler.
- `verifier.py` (397 LOC): Applies policies, produces verdicts. `_check_confirmations()` and `_assess_risk()` are where the false positives originate.
- `simulator.py` (527 LOC): Predicts file changes, blast radius, reversibility.
- `parser.py` (154 LOC): Parses shell commands into structured `ParsedCommand`.
- `snapshot.py` (183 LOC): Captures file state, git status before execution.
- `schemas.py` (121 LOC): Dataclasses for all types.
- `policies/default.yaml`: Policy rules.
- `server.py` (185 LOC): MCP server with assess_command, assess_edit, assess_write tools.

**The pipeline**: parse → snapshot → simulate → verify → audit → emit verdict.

## What Needs to Change

### 1. Project Scoping
SVX should only guard projects that have been explicitly initialized. No `.svx/` directory = no guarding. Like how `git` only works inside a git repo.

**Implementation**: In `_cmd_hook()`, check if any parent directory of the target file has a `.svx/` directory. If not, auto-allow.

### 2. Risk Calibration
Replace binary policies with a risk scoring system:

| Scenario | Risk | Verdict |
|----------|------|---------|
| New file in new/untracked directory | none | allow |
| Edit to .gitignore, __init__.py | low | allow (log) |
| Edit to pyproject.toml with dep changes | medium | confirm |
| Edit to settings.json, .env, credentials | high | block |
| rm -rf, force push, DROP TABLE | critical | block |

**Implementation**: `verifier.py` needs a `_classify_target()` function that scores files by:
- Path patterns (config vs source vs test vs generated)
- Whether the file exists (new file = lower risk)
- Whether it's tracked by git
- File extension and known sensitive patterns

### 3. Session Context (Read-Before-Write)
If the hook says "read first," it should actually check. Options:
- Track reads in a session state file (`.svx/session.json`)
- Drop the requirement entirely for new files (file doesn't exist = nothing to read)
- Only require read-before-write for files that already exist AND are config/sensitive

**Implementation**: When Write or Edit is intercepted, check if the target file exists. If not, it's a new file — auto-allow (or low-risk confirm). If it exists, check `.svx/session.json` for a recent Read of that path.

### 4. Consistent Bash Boundary
If `Write file.py` is blocked, then `Bash(cat > file.py)` must also be blocked. The parser needs to detect file-writing patterns in Bash commands:
- `cat > path`, `cat >> path`
- `echo "..." > path`
- `tee path`
- `sed -i` (already caught)
- heredoc redirects (`<< 'EOF' > path`)

**Implementation**: Expand `parser.py` to detect redirect operators in shell commands and extract target file paths. Apply the same risk assessment as Write tool.

### 5. Graduated Verdicts with Reasons
Instead of just "blocked," provide actionable feedback:

```
ALLOW (auto)   — new file in untracked directory, no risk
ALLOW (logged) — low risk, recorded in audit
CONFIRM        — medium risk, needs user approval  
BLOCK (soft)   — high risk, suggest alternative
BLOCK (hard)   — critical risk, non-negotiable
```

**Implementation**: Already partially there with `DenyKind.ADVISORY` vs `DenyKind.HARD`. Expand to include auto-allow with logging.

### 6. Easy Enable/Disable
Add `svx pause` and `svx resume` commands that toggle a flag in `.svx/config.json`. The hook checks this flag first. Also respect an env var `SVX_DISABLED=1` for quick override.

**Implementation**: In `_cmd_hook()`, check for pause state before processing. Add CLI commands.

## Existing Test Gap

1 test failing: `test_assess_command_confirm` in test_server.py (assertion error).
53 passing. Need to add tests for all new behaviors.

## Priority Order

1. Project scoping (biggest pain point — stops guarding where it shouldn't)
2. Risk calibration (stops false positives on low-risk files)
3. New-file detection (auto-allow creating files that don't exist)
4. Consistent Bash boundary (close the workaround hole)
5. Easy pause/resume (escape hatch)
6. Session context for read-before-write (nice to have)

## Engram References

- **MST-007**: SVX hooks block legitimate operations and push agents toward less-auditable workarounds
- **DEC-001**: SVX built as deterministic safety layer (no LLM calls)
- **LRN-007**: Simulation is for proposal, verification is for commitment
- **LRN-011**: Don't ask agents to use tools — put knowledge where they already look
