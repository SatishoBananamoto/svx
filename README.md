# svx — Simulate, Verify, Execute

A safety layer for coding agents. Before any action touches your codebase, svx simulates the outcome, verifies it against safety policies, and gives a clear verdict.

**Core principle:** simulation is for proposal; verification is for commitment.

## Why

Coding agents (Claude Code, Copilot, Cursor, Aider) make mistakes — destructive commands, wrong git operations, irreversible changes. Current safety is either "human watches everything" (doesn't scale) or static blocklists (no context). svx adds intelligent, context-aware simulation.

## Install

```bash
pip install -e .
```

## Usage

### Check a command before running it

```bash
svx check "git push --force origin main"
```

```
  svx ~ git push --force origin main
  ────────────────────────────────────────────────────────
  Verdict:  BLOCK
  Risk:     CRITICAL
  Force-push to main — rewrites remote history
  Effects:
    → Remote branch 'main' will be overwritten
    → Commits on remote not in local will become unreachable
  Reasons:
    ! BLOCKED: Force push to main/master is not allowed
  Suggestions:
    > Do not run this command.
    > Consider a safer alternative.
```

### Safe commands pass through

```bash
svx check "git status"
```

```
  svx ~ git status
  ────────────────────────────────────────────────────────
  Verdict:  ALLOW
  Risk:     NONE
  git status: read-only or low-risk operation
```

### Chained commands — each one analyzed

```bash
svx check "git add . && git commit -m 'fix' && git push --force origin master"
```

svx analyzes each command in the chain independently. If any step is blocked, the whole chain is blocked.

### JSON output for programmatic use

```bash
svx check "git reset --hard HEAD" --json
```

### Claude Code hook integration

svx can run as a pre-tool hook for Claude Code, automatically intercepting every shell command:

```bash
svx hook  # reads tool input from stdin
```

For a project-local Claude Code setup, run:

```bash
svx init
svx enable
```

`svx enable` writes `.claude/settings.local.json` with local `PreToolUse` hooks
for `Bash`, `Edit`, and `Write`, preserving any existing hooks and creating a
timestamped backup when the file already exists. Use `/hooks` inside Claude Code
to inspect the active configuration.

Bash file writes through stdout redirects, heredocs, and `tee` are routed
through the same file-write verifier as the `Write` tool. Direct modification
of `.claude/settings*` is hard-blocked so an agent cannot remove the SVX hook by
overwriting Claude Code's local settings.

To remove only the SVX hook entries later:

```bash
svx disable
```

To temporarily bypass the hook without editing Claude Code settings:

```bash
svx pause
svx resume
```

`svx pause` writes `paused: true` in the project-local `.svx/config.yaml`.
`svx resume` flips it back to `false`. For one-off shell sessions or CI, set
`SVX_DISABLED=1` to make the hook return an allow response immediately.

To prune stale session cache entries (`.svx/session.json`) for the current project:

```bash
svx session-prune --max-age-hours 1
```

This removes tracked read entries older than the supplied age (default: 12 hours)
and prints the number of records removed.

### View audit log

```bash
svx audit --tail 20
```

Audit entries are written to `~/.svx-audit/audit.jsonl` by default. Set
`SVX_AUDIT_DIR=/path/to/audit` when running in CI, tests, or a sandbox where
the home audit directory is not writable.

## What it catches

| Command | Verdict | Why |
|---|---|---|
| `git push --force origin main` | BLOCK | Force push to main — irreversible, rewrites history |
| `git reset --hard HEAD~3` | CONFIRM | Destroys uncommitted changes |
| `git clean -fd` | CONFIRM | Permanently deletes untracked files |
| `git branch -D feature` | CONFIRM | Force-deletes branch, may lose unmerged work |
| `rm -rf build/` | CONFIRM | Recursive delete, untracked files unrecoverable |
| `kill -9 1234` | CONFIRM | Force-kills process, unsaved state lost |
| `echo "new" > data/output.csv` | CONFIRM | Bash redirect overwrites an untracked file |
| `cat > .claude/settings.local.json` | BLOCK | Would modify Claude Code hook settings |
| `git push origin feature` | ALLOW | Normal push, reversible |
| `git status` | ALLOW | Read-only |
| `npm install lodash` | ALLOW | Reversible package install |

## How it works

```
Command → Parse → Snapshot world state → Simulate outcome → Verify safety → Verdict
```

1. **Parse**: Break the command into program, subcommand, flags, targets, including Bash redirects and `tee` writes
2. **Snapshot**: Capture current git state, file existence, sizes, tracking status
3. **Simulate**: Predict effects using dry-run flags and heuristic analysis (no LLM calls)
4. **Verify**: Score risk based on reversibility, blast radius, data loss, and policies
5. **Verdict**: ALLOW, CONFIRM, or BLOCK — with reasons and suggestions
6. **Audit**: Log every decision with full provenance

## Policies

Safety rules are defined in `policies/default.yaml`:

```yaml
blocks:
  force_push_to_main: true
  delete_root: true
  protect_claude_settings: true

confirmations:
  irreversible_actions: true
  data_loss: true
  force_flags: true

thresholds:
  max_blast_radius_without_confirm: 5
```

## Project Scoping

SVX only guards projects you opt into — like git only works inside a `.git/` repo:

```bash
cd my-project
svx init                    # creates .svx/ directory
svx init --mode strict      # confirm risky ops
svx init --mode vibe        # only block catastrophic (default)
```

Operations outside `.svx/` projects are auto-allowed. Project-local
`.svx/config.yaml` overrides `~/.svx.yaml`, so `svx init --mode strict`,
`svx pause`, and `svx resume` apply to the current project without changing
global defaults.

## Safety Boundary

SVX is a safety rail, not OS-level containment. It is designed to catch,
explain, log, confirm, or block risky pre-tool actions before normal agent
mistakes touch a project. A deliberately adversarial process with unrestricted
shell access is outside this trust boundary; use operating-system sandboxing
when containment is required.

### Modes

- **vibe** (default): Only BLOCK verdicts deny. CONFIRM verdicts auto-allow with logging. For when you trust the agent but want catastrophic-only protection.
- **strict**: Both BLOCK and CONFIRM verdicts require approval. For sensitive repositories.
- **paused**: `svx pause` makes the hook allow project actions until `svx resume` is run. `SVX_DISABLED=1` bypasses the hook from the environment.

## Exit codes

- `0` — ALLOW
- `1` — CONFIRM (needs user approval)
- `2` — BLOCK (should not run)

## Architecture

- **No LLM calls** — pure deterministic analysis in v0.1
- **Uses real data** — git dry-runs, file stats, not guesswork
- **Fast** — runs in <100ms for most commands
- **Provenance** — every decision logged with full context

## License

MIT
