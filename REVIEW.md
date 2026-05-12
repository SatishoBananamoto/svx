# SVX — Review

**Reviewer**: Claude (Opus 4.6, partner session)
**Date**: 2026-03-20
**Version Reviewed**: v0.2.0, 11 modules, ~3,273 LOC, 53/54 tests passing (1 flaky)
**Previous Review**: First review

---

## 2026-05-12 Codex Update — Pause/Resume

The easy pause/resume gap is now covered without requiring manual edits to
`.claude/settings.local.json`. `svx pause` and `svx resume` toggle a
project-local `paused` flag in `.svx/config.yaml`, and `SVX_DISABLED=1` gives a
process-level bypass for one-off runs. The hook now merges defaults, global
`~/.svx.yaml`, and project-local config in that order.

Current local verification: `python3 -B -m pytest -q -p no:cacheprovider`
passes with 92 tests. Remaining high-value work: session context/read-before-write
and config-file risk calibration.

---

## 2026-05-12 Codex Update

The common Bash file-write bypass now has initial coverage. `parser.py` routes
stdout redirects (`>`, `>>`, heredoc-with-redirect) and `tee` targets into the
same `FILE_WRITE` simulation path used by the Claude Code `Write` tool.
`verifier.py` also hard-blocks destructive touches to `.claude/settings*`, so
the documented `cat > .claude/settings.local.json` self-disable path is denied
even in vibe mode.

Current local verification: `python3 -B -m pytest -q -p no:cacheprovider`
passes with 86 tests. The original B- review is now partially stale: the Bash
self-disablement finding is addressed for common file-write patterns, but
session context, pause/resume, false-positive calibration, and broader
containment/threat-model hardening remain open.

---

## 2026-05-11 Codex Update

The version mismatch noted below is fixed: `svx.__version__` now matches `pyproject.toml` at `0.3.0`. MCP server assessment tests also no longer depend on writing to `~/.svx-audit`; audit logging honors `SVX_AUDIT_DIR` and falls back to `/tmp/svx-audit` if the preferred path is unavailable.

Current local verification: `python3 -B -m pytest -q -p no:cacheprovider` passes with 66 tests, `python3 -m compileall src tests` passes, and `git diff --check` passes. The B- review still stands until the Bash file-write bypass, threat-model documentation, and false-positive work are addressed.

---

## Summary

SVX (Simulate, Verify, Execute) is a deterministic safety layer for AI coding agents. It intercepts tool calls via Claude Code PreToolUse hooks, simulates their impact by snapshotting world state, and produces verdicts: allow, confirm, or block. No LLM calls — pure rule-based analysis. It's been battle-tested through 1,864+ audit entries from real usage building the scroll project. The architecture is sound and the core works, but real-world usage revealed significant usability problems (false positives, no context awareness, inconsistent boundaries) that are documented in SVX-REWORK.md.

---

## Dimension Assessments

### Thesis & Positioning

The thesis: "Simulation is for proposal; verification is for commitment." AI coding agents have write access to filesystems, git repos, and package managers. They can `rm -rf`, force-push to main, or overwrite untracked files. SVX puts a deterministic safety layer between the agent's intent and the actual execution.

This is a real problem. Claude Code's built-in permission system is binary (allow/deny) and doesn't understand context. SVX adds simulation (predict what will happen), risk scoring (how bad is it?), and policy enforcement (what should be blocked vs. confirmed?).

**Differentiation**: No other tool does this at the PreToolUse hook level. There are sandboxing approaches (Docker, Firecracker) that isolate the entire environment, but nothing that intercepts individual tool calls, simulates their effects, and produces graduated verdicts. SVX operates at a fundamentally different layer.

**Market question**: Is this a standalone product or a feature? If Claude Code eventually ships built-in graduated risk assessment (which they likely will), SVX becomes redundant. The window of opportunity is while AI coding agents have primitive permission systems.

### Architecture

11 modules in a clean pipeline:

```
Tool Call → parser → snapshot → simulator → verifier → audit
                                                ↓
                                          CLI/hook output
```

| Module | Role | LOC | Assessment |
|--------|------|-----|-----------|
| parser.py | Tokenize commands, detect category/flags | ~154 | Solid. Handles chaining, pipes, sudo. |
| schemas.py | Core dataclasses and enums | ~121 | Clean. Well-defined types. |
| snapshot.py | Capture git state, file metadata | ~183 | Thorough. Config/sensitive path detection. |
| simulator.py | Predict effects of operations | ~527 | Largest module. Comprehensive git/file simulation. |
| verifier.py | Risk scoring + policy enforcement → verdict | ~397 | Well-structured. Additive risk scoring. |
| config.py | Load user config from ~/.svx.yaml | ~40 | Minimal. Two modes: vibe/strict. |
| audit.py | JSONL logging of every decision | ~64 | Simple, effective. |
| humanize.py | User-friendly explanations | ~273 | Two modes: human/technical. |
| cli.py | CLI + hook handler | ~594 | 5 commands. Hook retry loop detection. |
| server.py | MCP server with 4 tools | ~185 | Clean tool descriptions. |
| __init__.py | Version | ~3 | Version mismatch: says 0.1.0, pyproject says 0.2.0 |

The pipeline is linear and deterministic — no loops, no async, no LLM calls. This is correct for a safety-critical tool. A safety layer that itself has unpredictable behavior defeats the purpose.

**Concern**: simulator.py at 527 LOC is doing a lot. It handles git operations (push, reset, checkout, clean, branch, rebase, stash), file operations (delete, move, permissions), package operations, process operations, file edits, and file writes. This is the most likely module to have bugs, and bugs in the simulator mean wrong verdicts. Consider splitting into git_simulator, file_simulator, etc.

### Code Quality

| Metric | Value | Assessment |
|--------|-------|-----------|
| Tests | 53 passing, 1 flaky | Good but flaky test is a red flag |
| Test distribution | parser (18), verifier (20), file_edit (43), server (5) | Heavy on file_edit, light on server |
| Dependencies | pyyaml, mcp | Minimal |
| Entry point | `svx` CLI via pyproject.toml | Properly packaged |

The flaky test (test_assess_command_confirm in test_server.py) needs fixing. A safety tool with flaky tests undermines confidence in its own reliability.

Test coverage is strong for the core path (parser → verifier → verdict), but simulator.py — the largest and most complex module — has no dedicated test file. Simulator behavior is tested indirectly through verifier tests and file_edit tests, but edge cases in git simulation (force-push to non-main branch, reset --soft, stash pop) may be untested.

Risk scoring is additive and transparent:
- Irreversible: +3
- Data loss: +2
- Blast radius >10: +3, >5: +2, >2: +1
- Force flags: +2
- Main branch: +2
- Config edit: +2
- Large rewrite: +2
- Dirty repo: +1

This is auditable. You can trace exactly why a command got a specific risk level.

### Completeness

**Complete:**
- Shell command parsing with chaining (&&, ||, ;) and pipes
- Git operations: push, reset, checkout, clean, branch, rebase, stash, merge, tag
- File operations: delete, move, permissions
- File Edit and File Write tool interception
- World state snapshotting (git status, file metadata, config detection)
- Risk scoring (0-2: none, 3-4: medium, 5-6: high, 7+: critical)
- Policy enforcement via YAML
- Hard blocks (force-push main, delete root)
- Retry loop detection (same denied command 3+ times in 60s → escalate)
- JSONL audit trail with full provenance
- Human-readable explanations (human and technical modes)
- MCP server with 4 tools
- Claude Code PreToolUse hook integration

**Missing (from SVX-REWORK.md — the author knows these):**
- Project scoping (currently guards everything, not just opted-in projects)
- New-file awareness (blocks creating files that don't exist yet)
- Graduated risk for low-risk operations (2% markdown edit = same treatment as rm -rf)
- Consistent Bash boundary (agent circumvents via `cat >` or heredoc)
- Easy pause/resume mechanism
- Session context (doesn't track reads, so "read first" advice is stateless)
- Config file risk stratification (pyproject.toml ≠ .env ≠ Dockerfile)

### Usability

**For Claude Code integration**: The hook works. JSON in, hookSpecificOutput JSON out. Claude Code respects the deny verdict. The retry loop detection prevents the agent from hammering the same denied command.

**Pain points from real usage** (SVX-REWORK.md documents these):
1. False positives are high. Creating pyproject.toml in an empty directory gets blocked. Editing a 2% change in markdown triggers "confirm."
2. The agent circumvents blocks by using Bash `cat >` instead of the Write tool — SVX doesn't detect this.
3. No easy way to pause SVX when you know the next 10 operations are safe.
4. "Read the file first" advice is given but SVX doesn't track whether the read happened.

The `svx watch` live dashboard is a nice touch for monitoring in real-time.

### Sustainability

Zero LLM dependency — pure rules. This makes SVX the most self-contained tool in the portfolio. It can run forever without API costs, model updates, or external service dependencies.

The policy YAML is extensible — new rules can be added without code changes (as long as the verifier checks them).

Maintenance burden is low: pyyaml and mcp are stable dependencies. The audit trail is append-only JSONL with no rotation — will need log rotation at some point.

The SVX-REWORK.md shows healthy self-awareness. The author knows what's broken and has prioritized fixes. This is a good sign for sustainability.

### Portfolio Fit

SVX is the safety layer for the portfolio. It protects against destructive operations by AI agents working on any project (engram, scroll, vigil, kv-secrets, or anything else).

The relationship to engram is indirect: SVX audit entries could be ingested by scroll to extract learnings about which operations are risky in practice. SVX verdicts could inform engram decisions about project safety policies.

SVX doesn't share infrastructure with the other projects, but it protects all of them. That's the right relationship — the safety layer should be independent of the things it protects.

---

## Strengths

1. **Deterministic, no LLM.** A safety layer that itself uses unpredictable AI would be absurd. SVX is pure rules, pure logic, <100ms execution. This is a fundamental design decision that's exactly right.

2. **Battle-tested.** 1,864+ audit entries from real usage building scroll. The SVX-REWORK.md issues were discovered through actual use, not theoretical analysis. The problems are real, but so is the validation.

3. **Transparent risk scoring.** Additive scoring with clear weights. You can trace exactly why `git push --force origin main` gets a CRITICAL verdict (irreversible: 3 + force: 2 + main: 2 + data loss: 2 = 9 → CRITICAL). No black boxes.

4. **Retry loop detection.** If the agent retries a denied command 3+ times in 60 seconds, SVX escalates from advisory deny to hard deny. This prevents the common failure mode of agents brute-forcing past safety checks.

5. **Full audit provenance.** Every decision logged with: timestamp, raw command, parsed command, world snapshot, simulation result, verdict, risk level, reasons, and advisory actions. This is the kind of audit trail that safety-critical systems need.

---

## Weaknesses

1. **CRITICAL: SVX can be disabled by the agent it protects.** The Bash bypass (cat >, heredoc) extends to SVX's own configuration. An agent can overwrite `.claude/settings.local.json` via `cat > .claude/settings.local.json << 'EOF'` to remove the SVX hook entirely. SVX doesn't detect this as a file write, so it can't block its own deactivation. **Confirmed through analysis during this review.** **Fix**: Two layers — (a) parse `cat >`, heredoc, `echo >` in Bash commands (closes the general bypass), and (b) add `.claude/settings*` to the hard-block list in policies, so even if the parser catches it, it's blocked unconditionally.

2. **False positive rate is too high for real use.** Creating a new file in an empty directory shouldn't be blocked. A 2% markdown edit shouldn't require confirmation. These false positives train users to ignore SVX, which defeats its purpose. **Fix**: Implement SVX-REWORK.md priorities: project scoping, new-file detection, graduated risk.

3. **Inconsistent Bash boundary.** SVX intercepts Edit and Write tools but not `cat >`, `echo >`, or heredoc in Bash. The agent learns to use Bash for file operations SVX would block. **Fix**: Parse Bash commands for file write patterns.

4. **SVX is a safety rail, not a containment boundary — but doesn't say so.** Users may believe SVX prevents agents from doing dangerous things. It doesn't — it deters, logs, and slows down. A determined agent with shell access can bypass SVX. This is the same as `sudo` — it stops accidental root operations, not a root user from bypassing it. **Fix**: Document the threat model explicitly. README and DESIGN docs should state: "SVX catches accidental destructive operations (99% case). It does not prevent a deliberately adversarial agent from bypassing it. For containment, use OS-level sandboxing."

5. **simulator.py is a monolith.** 527 LOC handling git, files, packages, processes, edits, and writes. **Fix**: Split into git_simulator.py, file_simulator.py, etc.

6. **Flaky test in test_server.py.** A safety tool's test suite must be 100% deterministic. **Fix**: Debug and fix test_assess_command_confirm.

7. **Version mismatch.** `__init__.py` says 0.1.0, pyproject.toml says 0.2.0. **Fix**: Single source of truth.

### Note on installed users vs. developer system

On Satish's machine, the agent can read SVX's source code to learn its parser gaps. On an installed user's machine, SVX is in site-packages — the agent CAN access it (same user) but is unlikely to (it's not in the project directory). The practical security for installed users is higher because the bypass requires deliberate, targeted action rather than incidental discovery. SVX's value for the 99% case (accidental destructive operations by normal agents) is real and meaningful.

---

## Recommendations (Priority Order)

1. **Close the Bash file-write bypass and protect own config.** This is the most critical fix — it addresses both the general bypass (cat >, heredoc) and SVX self-disablement. Parse Bash commands for file write patterns AND hard-block writes to `.claude/settings*`.

2. **Document the threat model.** SVX is a safety rail, not containment. State this clearly in README and any user-facing docs. Users who understand the model will trust the tool more, not less.

3. **Fix false positive rate.** Graduated risk scoring and new-file detection from SVX-REWORK.md. Without this, users learn to ignore SVX.

4. **Fix the flaky test.** A safety tool's test suite must be 100% deterministic.

5. **Split simulator.py.** Reduce the monolith to testable components.

---

## Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| Agent disables SVX via Bash config overwrite | High | Critical | Parse file writes in Bash, hard-block settings files |
| False positives train users to ignore SVX | High | Critical | Graduated risk scoring, project scoping |
| Bash file-write bypass undermines safety | High | High | Parse cat >, heredoc, echo >, tee patterns |
| Users believe SVX is containment (it's not) | Medium | High | Document threat model explicitly |
| Claude Code ships built-in graduated risk | Medium | High | Move fast, differentiate on audit + policy |
| simulator.py edge cases produce wrong verdicts | Medium | High | Split and test independently |

---

## Verdict

SVX solves a real problem — AI agents need graduated safety checks, not binary allow/deny. The architecture is sound (deterministic, fast, auditable), and it's been battle-tested through real usage. The self-disablement vulnerability (agent can remove SVX via Bash config overwrite) is serious but fixable by the same Bash parser improvement that addresses the general file-write bypass. The key insight from this review: **SVX is a safety rail, not a containment boundary.** For installed users, it catches the 99% case (accidental destructive operations) effectively. For the 1% adversarial case, OS-level sandboxing is required. This framing is honest and correct — the same model `sudo` operates under.

**Grade: B-**
Strong architecture, real-world tested, correct thesis. The Bash bypass (including self-disablement) and false positive rate are critical issues. Grade holds because the rework plan exists but hasn't been executed. Moves to B+ when the Bash parser and threat model documentation ship.
