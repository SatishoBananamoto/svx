# SENTINEL — svx

> Deterministic safety layer for AI coding agents. No LLM calls.
> Updated before every commit. Single source of truth.

**Current version**: v0.3.0 (on PyPI as `svx`)
**Last session**: 2026-05-12 — read-before-write session context enforcement
**Repo**: Ready to commit.

---

## NEXT SESSION — START HERE

### What just happened (2026-05-12)

Codex implemented session context tracking for read-before-write enforcement on
config files. Bash read commands now record file reads into `.svx/session.json`
and write/edit verification now requires a fresh read in-session for existing
config files before retrying. This closes the read-before-write bypass gap while
keeping non-blocking session persistence behavior.

Verification: `python3 -B -m pytest -q -p no:cacheprovider` passed with 101 tests.

Previous session (2026-05-12):

Codex resolved `.gitignore` from the high-risk config bucket. `snapshot.py` now
excludes `.gitignore`, `.gitattributes`, and `.gitmodules` from `config_file_edits`
confirmation-by-default, and targeted verification now covers the non-config
classification for `.gitignore` edits plus keep-confirms for `.env`.

Verification: `python3 -B -m pytest -q -p no:cacheprovider` passed with 93 tests.

Previous session (2026-05-12):

Codex added the easy pause/resume slice. `svx pause` and `svx resume` now
toggle a project-local `paused` flag in `.svx/config.yaml`, the hook checks
that flag after project scoping, and `SVX_DISABLED=1` provides an environment
bypass for one-off runs. Config loading now merges defaults, `~/.svx.yaml`, and
project-local config in that order, so `svx init --mode strict` is honored by
the hook.

Verification: `python3 -B -m pytest -q -p no:cacheprovider` passed with 92
tests.

Previous session (2026-05-12):

Codex closed the first Bash file-write bypass slice. Bash stdout redirects
(`>`, `>>`), heredoc-with-redirect commands, and `tee` targets now parse as
`FILE_WRITE` and reuse the existing file-write snapshot/simulation/verification
path. Writes to `.claude/settings*` are hard-blocked so SVX cannot be disabled
through a Bash overwrite of Claude Code hook settings.

Verification: `python3 -B -m pytest -q -p no:cacheprovider` passed with 86
tests.

Previous session (2026-05-12):

Codex added hook output validation for Claude Code integration. The tests now
cover empty JSON for allowed hooks, advisory-deny `hookSpecificOutput` with an
actionable instruction, hard-block output, and a strict-mode `_cmd_hook()` run
from stdin to stdout in a temporary `.svx` project.

Verification: `python3 -B -m pytest -q -p no:cacheprovider` passed with 76
tests, `python3 -B -m compileall src tests` passed, and `git diff --check`
passed.

Previous session (2026-05-12):

Codex added the first hook-wiring usability slice: `svx enable` and
`svx disable` now manage project-local Claude Code `PreToolUse` hooks in
`.claude/settings.local.json`. The helper preserves existing hooks, avoids
duplicating SVX handlers, removes only SVX handlers on disable, and creates a
timestamped backup before overwriting an existing settings file. README now
documents the `svx init` -> `svx enable` setup path and `/hooks` verification.

Verification: `python3 -B -m pytest -q -p no:cacheprovider` passed with 72
tests, `python3 -B -m compileall src tests` passed, and `git diff --check`
passed.

Previous session (2026-05-11):

Codex verified that the old tracker claim was stale: the suite initially failed because MCP server assessment tools wrote audit logs to `~/.svx-audit`, which is not writable in this sandbox. Fixed audit logging to honor `SVX_AUDIT_DIR` and fall back to `/tmp/svx-audit` if the preferred audit path is unavailable. Also synced `svx.__version__` to package version `0.3.0` and added a metadata regression test.

Verification: `python3 -B -m pytest -q -p no:cacheprovider` passed with 66 tests, `python3 -m compileall src tests` passed, and `git diff --check` passed.

Previous session (2026-03-27):

Major v0.3 rework based on real usage building scroll (SVX blocked legitimate operations). Shipped: project scoping (`svx init` creates `.svx/`), advisory denies with risk classification (replaces binary block/allow), vibe mode (relaxed for exploratory work). 10 new tests. Published to PyPI v0.3.0. CI added.

### #1 Priority: Claude Code hook wiring

svx is meant to be a PreToolUse hook for Claude Code. The hook binary exists (`svx hook`) but integration is not streamlined. Need: easy enable/disable, documentation for `settings.local.json` setup, and validation that the hook works end-to-end with the new advisory deny format.

### What NOT to do

- Don't chase the old flaky-test note without reproducing it — current local suite is 92 passing
- Don't add new policies before the existing ones are validated in real use
- Don't make the hook mandatory — opt-in per project via `svx init`

---

## Work

### Hook integration

_svx's value is as a Claude Code hook. The hook exists but isn't easy to wire up._

- [x] Document hook setup in README (settings.local.json config)
- [x] Add `svx enable` / `svx disable` commands for quick toggle
- [x] Test hook end-to-end with advisory deny format
- [x] Validate: agent receives hookSpecificOutput correctly
- [ ] Continue

### Remaining from SVX-REWORK.md

_Problems 2, 4, 5, 6 from the rework brief. Problem 1 (context) and 3 (binary) are solved._

- [x] Session context for read-before-write — track reads in `.svx/session.json`
- [x] Consistent Bash boundary — detect file-writing patterns (cat >, heredoc, tee)
- [x] Easy pause/resume — `svx pause` / `svx resume` + env var `SVX_DISABLED=1`
- [x] Config file risk calibration — `.gitignore` ≠ `settings.json`
- [ ] Continue

### Test health

- [x] Fix MCP server audit-path test failures — 2026-05-11 · `SVX_AUDIT_DIR`, `/tmp/svx-audit` fallback, 66 tests passing
- [x] Fix runtime/package version mismatch — 2026-05-11 · `svx.__version__ == 0.3.0`
- [x] Add tests for file edit simulation edge cases
- [ ] Continue

### Done

<details>
<summary>v0.1.0 → v0.3.0 — completed 2026-03-27</summary>

- [x] v0.1.0: core pipeline — parse → snapshot → simulate → verify → audit — `commit:aa57344`
- [x] Hook integration: Claude Code PreToolUse hook — `commit:ba4eccd`
- [x] Hook output format fix — `commit:e9738e1` · `engram:LRN-001`
- [x] v0.2.0: file edit simulation + MCP server — `commit:025e43d`
- [x] v0.3 rework plan: 6 problems from real scroll usage — `commit:0b518fe` · `ref:SVX-REWORK.md`
- [x] Project scoping: `svx init`, `.svx/` directory — `commit:e8da2a4` · `engram:DEC-002`
- [x] Advisory denies: risk classification replaces binary — `engram:DEC-001`
- [x] Vibe mode: relaxed policies for exploratory work — `commit:e8da2a4`
- [x] CLAUDE.md from scroll — `commit:db9538a`
- [x] 10 tests for new features — `commit:f64b4da`
- [x] README updated — `commit:0e660fd`
- [x] Ship to PyPI v0.3.0 — `commit:8f2f6fc`
- [x] CI — `commit:dc4f99a`
- [x] Audit path + version baseline — 2026-05-11 · 66 tests passing
- [x] Hook enable/disable helper — 2026-05-12 · 72 tests passing
- [x] hookSpecificOutput validation — 2026-05-12 · 76 tests passing
- [x] Bash file-write boundary — 2026-05-12 · redirects, heredoc, tee, `.claude/settings*` hard block, 86 tests passing
- [x] Pause/resume — 2026-05-12 · project-local paused flag, `SVX_DISABLED=1`, project config honored by hook, 92 tests passing
- [x] Config file risk calibration — 2026-05-12 — `.gitignore` no longer treated as config by default, 93 tests passing
- [x] Read-before-write context — 2026-05-12 — session tracking for read commands with config-file edit confirmation, 101 tests passing

</details>

---

## Decision Log

| ID | Date | Decision | Why |
|----|------|----------|-----|
| D-001 | 2026-03-27 | Advisory denies instead of binary block/allow | Binary was too restrictive. Risk classification gives actionable guidance. `engram:DEC-001` |
| D-002 | 2026-03-27 | Project scoping via `.svx/` directory | Only guard opted-in projects. Like how git only works in git repos. `engram:DEC-002` |
| D-003 | 2026-03-27 | Fail-open error handling | Safety system shouldn't break the workflow on unexpected input. `engram:DEC-004` |

---

## Session Log

### 2026-03-13 to 2026-03-14 — Initial build (Session 1)

- **Worked on:** Core pipeline, hook integration
- **Completed:** v0.1.0 — parse → snapshot → simulate → verify → audit
- **State:** Working hook, basic policies

### 2026-03-17 — File edits (Session 2)

- **Worked on:** File edit simulation, MCP server
- **Completed:** v0.2.0

### 2026-03-27 — Major rework (Session 3)

- **Worked on:** 6 problems from real scroll usage → v0.3 rework
- **Completed:** Project scoping, advisory denies, vibe mode, 10 new tests, PyPI v0.3.0, CI
- **Decisions:** D-001, D-002, D-003
- **Engram:** DEC-001, DEC-002, DEC-004, LRN-001, MST-007
- **State:** Shipped. At that time, 1 flaky test remained. Next: hook wiring.

### 2026-05-11 — Codex baseline repair

- **Worked on:** MCP server audit-path test failures and version metadata drift.
- **Completed:** `SVX_AUDIT_DIR` support, `/tmp/svx-audit` fallback, server/CLI shared audit path, isolated server tests, version metadata regression.
- **State:** 66 tests passing. Next: hook wiring and Bash file-write boundary.

### 2026-05-12 — Codex hook wiring pass

- **Worked on:** Project-local Claude Code hook setup
- **Completed:** `svx enable` / `svx disable`, settings merge/remove helpers, README setup docs, and backup/idempotence tests.
- **State:** 72 tests passing. Next: end-to-end hookSpecificOutput validation and Bash file-write boundary.

### 2026-05-12 — Codex hook output validation pass

- **Worked on:** Claude Code hookSpecificOutput contract coverage
- **Completed:** Empty allow output, advisory-deny action output, hard-block output, and strict-mode `_cmd_hook()` stdin/stdout regression tests.
- **State:** 76 tests passing. Next: Bash file-write boundary.

### 2026-05-12 — Codex Bash file-write boundary pass

- **Worked on:** Bash-level file writes that bypassed the Write tool simulator.
- **Completed:** Redirect/heredoc/tee detection, reuse of `FILE_WRITE` verification, `.claude/settings*` hard-block policy, README/review/tracker updates, and hook regressions for strict advisory deny plus vibe-mode settings block.
- **State:** 86 tests passing. Next: session context/read-before-write or pause/resume.

### 2026-05-12 — Codex pause/resume pass

- **Worked on:** Project-local escape hatch without hand-editing Claude Code settings.
- **Completed:** `svx pause`, `svx resume`, `SVX_DISABLED=1`, project config merge order, hook paused-state bypass, README/review/tracker updates, and regressions for CLI toggles plus hook bypasses.
- **State:** 92 tests passing. Next: session context/read-before-write or config file risk calibration.

---

### Key reference files

| File | What it contains |
|------|-----------------|
| SENTINEL.md | This file. |
| SVX-REWORK.md | Original rework brief — 6 problems from scroll usage. Historical. |
| REVIEW.md | Structured assessment (grade B-). Pre-rework. Needs re-review. |
| CLAUDE.md | scroll-extracted knowledge (decisions, learnings). |
| policies/default.yaml | 4 blocking rules, 4 confirmation rules. |
