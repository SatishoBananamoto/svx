# BRIDGE — svx ↔ caliber integration design (v1)

> Every svx assessment is a falsifiable prediction. This bridge grades svx
> against reality automatically, turning normal agent work into calibration
> evidence — proof the safety layer works, by accumulated track record
> instead of assertion.
>
> Design owner: Fable session 2026-06-10. Status: design accepted, building.
> caliber-side notes: ~/caliber/INTEGRATIONS.md (svx section).

## The claim being tested (v1 — deliberately narrow)

When svx assesses a command and lets it proceed, it implicitly claims:
*"I understood this command; it will complete without error."*

The bridge makes that claim explicit and graded:

- **Predict (PreToolUse, existing hook):** after `verify()`, if the bridge
  is enabled and the verdict allows execution, record a caliber prediction
  with `signed=True` (exercises the commitment scheme — the prerequisite
  GAUGE set for unlocking Trust Card verification).
- **Verify (PostToolUse, new hook branch):** when the tool result arrives,
  grade the prediction: did the command error?

**Confidence mapping (a hypothesis, not a constant):**

| risk_level | confidence |
|------------|-----------|
| none       | 0.95 |
| low        | 0.90 |
| medium     | 0.80 |
| high       | 0.70 |
| critical   | 0.60 |

If svx says 0.95 and only 80% of those commands run clean, the Trust Card
shows it — and this table gets tuned from evidence. The mapping being
wrong is a *result*, not a failure.

## What is deliberately NOT in v1

- **Effect-level grading** (file created/deleted, N lines changed, graded
  by post-execution snapshot diff) — v2. Strictly better data, much more
  machinery.
- **BLOCK verdicts** — blocked commands never run, so the claim is
  unverifiable. No prediction recorded. Unverifiable claims pollute.
- **Edit/Write tools** — v1 covers Bash only; Edit/Write grading belongs
  with effect-level claims in v2.

## Privacy: claims must travel safely

Trust Cards get shared; commands can contain secrets. Predictions carry:

- claim: `svx: <program> (<category>) completes without error` —
  parsed metadata only, **never raw command text**
- prediction notes: `cmd:<sha256-prefix-12>` for local correlation with
  the audit log (which already stores raw text, locally, same trust domain)
- domain: `svx-<category>` (e.g. `svx-git`, `svx-file_delete`)

## Mechanics

- **Pending map:** `.svx/session.json` gains a `pending_predictions` map
  keyed by sha256(command), holding `[prediction_id, recorded_at]` FIFO
  lists (same command can repeat in-session). Pruned by the existing
  session TTL machinery — entries whose PostToolUse never arrives age out
  and the prediction honestly stays unverified.
- **PostToolUse branch:** `svx hook` reads `hook_event_name`; on
  `PostToolUse` + Bash it looks up the command hash and grades.
  Outcome parsing is defensive: explicit error markers in
  `tool_response` grade incorrect; absence of any recognizable signal
  leaves the prediction unverified rather than guessing.
- **Agent identity:** per-project feed agent `svx-<project-dir-name>`,
  store at the caliber default (`~/.caliber`). Feed agents verify at
  machine speed: their integrity reports are EXPECTED to show
  INSTANT_VERIFICATION — that is what an event feed looks like, and the
  per-agent naming keeps it from contaminating human-paced records.
- **Opt-in:** `.svx/config.yaml` key `caliber_bridge: true`. Off by
  default (svx D-002 ethos: opt-in per project).
- **Fail-open everywhere** (svx D-003): caliber not installed, store not
  writable, any bridge exception — the hook proceeds as if the bridge
  did not exist. A safety layer must never break the workflow to feed
  its own scorecard.
- **`svx enable`** wires both PreToolUse and PostToolUse hooks; the
  PostToolUse handler is a cheap no-op when the bridge is disabled.

## Why this design (decision log)

| Decision | Why |
|----------|-----|
| Hash in notes, metadata in claim | Trust Cards travel; secrets in commands must not. |
| No predictions for BLOCK | Unverifiable claims inflate `total_predictions` with permanent unverified noise. |
| Per-project feed agent name | Isolates machine-paced records; INSTANT_VERIFICATION stays meaningful for human-paced agents. |
| signed=True | Generates real commitment-scheme usage — the stated precondition for caliber's Trust Card verification work. |
| Confidence table is tunable | The bridge's purpose is to find out the table is wrong and by how much. |

## Build plan (chunk by chunk)

1. `bridge.py` module: config gate, claim construction, predict-on-assess,
   pending map read/write. Tests with caliber as test dependency.
2. PostToolUse branch in `_cmd_hook` + grading + pending prune. Tests.
3. `svx enable` PostToolUse wiring + README/SENTINEL docs. End-to-end test:
   assess → execute → verify → caliber card shows the feed agent.
