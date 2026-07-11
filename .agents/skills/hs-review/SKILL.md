---
name: hs-review
description: >-
  Adversarial QA of a completed HyperShell feature branch. Delegates the correctness pass to a FRESH
  reviewer subagent that sees only GOAL.md + the branch diff + the AGENTS.md invariant checklist + the
  runnable repo — NOT PLAN.md/TECH.md (avoids grading-its-own-homework). The reviewer refutes each
  finding and cites executed commands; CONFIRMED findings loop back to /hs-build; the coupled core
  forces a human gate. Fourth step of the software factory (see .agents/factory/review-rubric.md).
disable-model-invocation: true
argument-hint: "[debate] [completeness] [status]"
allowed-tools: Read, Grep, Glob, Write, Agent, ReportFindings, AskUserQuestion, Bash(git status *), Bash(git branch *), Bash(git log *), Bash(git diff *), Bash(git rev-parse *), Bash(git add *), Bash(git commit *), Bash(uv run *)
---

# hs-review — adversarial QA (clean context)

## When to Use

Invoke `/hs-review` when a branch's `TECH.md` is fully built (`status: in_review`). **Best run in a
fresh session** — but the real guarantee comes from delegating scrutiny to freshly-spawned subagents
with curated inputs, so bias is removed even if this session is not clean. The reviewer grades the
diff against the **locked `GOAL.md`** and the **AGENTS.md invariants**, by **executed command**, not
opinion.

Operating manual: [`.agents/factory/review-rubric.md`](../../factory/review-rubric.md) and
[`.agents/factory/invariants.md`](../../factory/invariants.md). Read them before delegating.

## User Instructions

Additional instructions provided with the invocation: $ARGUMENTS

## Current state (injected at load)

- Branch: !`git branch --show-current`
- Base: `develop` (feature/fix branch base; confirm from `base:` in TECH.md during Step 1).
- Diffstat vs develop: !`git diff --stat develop...HEAD 2>/dev/null | tail -n 20`

## Argument Parsing

- `status` → report the current `review` verdict from `TECH.md` and any existing `REVIEW.md`; no work.
- `debate` → run the two-independent-reviewer variant (for high-risk / coupled-core diffs).
- `completeness` → also run the *separate* completeness sub-pass (may see `TECH.md`).

## Safety Principles

- **Blindness is the point.** The correctness reviewer subagent is given `GOAL.md`, the diff, the
  runnable repo, `invariants.md`, and `review-rubric.md` — and is **explicitly told NOT to read
  `PLAN.md`, `TECH.md`, or `research/`**. Only this skill (the orchestrator) reads `TECH.md`, and only
  for the `base`/`slug` metadata — it must not pass PLAN/TECH *content* into the reviewer prompt.
- **External verification is the spine.** Every finding must cite an executed command
  (`uv run pytest`, real CLI in a `temp_site`, docs build when touched). No assertion-only findings.
- **Refute before reporting.** Try to disprove each candidate; classify `CONFIRMED` (reproduced) vs
  `PLAUSIBLE` (needs human triage). Default to dropping when uncertain.
- **Scope is narrow:** correctness bugs, GOAL R-ID gaps, AGENTS.md invariant violations
  (auto-CRITICAL), and scope creep (changes mapping to no R-ID). **No style nits, no speculative
  hardening** — a gap-hunting reviewer manufactures gaps.
- **Read-only session.** This skill makes no source edits; it writes `REVIEW.md` and updates the
  `TECH.md` `review` block via `set_phase.py`.
- **Mandatory human gate** when any CONFIRMED finding touches the high-blast-radius core
  (`data/model.py`, `server.py`, `client.py`, `core/queue|tls|fsm|thread|signal.py`,
  `cluster/remote|ssh.py`) or a security/DB-lifecycle invariant — regardless of auto-loop.
- **Bounded loop:** ≤ 2–3 review↔build cycles; escalate to the human on non-convergence.

## Procedure

### Step 1 — Pre-flight
Confirm a feature/fix branch; resolve `{slug}` from the branch and confirm `base` from TECH.md
`base:` (defaults to `develop`). Capture the head SHA (`git rev-parse HEAD`). If `TECH.md` `status`
is not `in_review`/`done`, note it (the build may be incomplete) and ask whether to proceed.

### Step 2 — Delegate the correctness pass (fresh subagent)
Launch a fresh `general-purpose` reviewer via the `Agent` tool. Give it, inline, **only**:
- the full text of `spec/{slug}/GOAL.md` (the contract — R-IDs);
- the command to produce the diff: `git diff {base}...HEAD` (and `git log --oneline {base}..HEAD`);
- the full text of `invariants.md` and `review-rubric.md`;
- the instruction: work in the runnable repo, follow the refutation protocol, **run** the relevant
  `verify` commands / drive the CLI in a `temp_site`, and **do NOT read `PLAN.md`/`TECH.md`/`research/`**.
- required return: a structured findings list (severity, CONFIRMED/PLAUSIBLE, file:line, failure
  scenario, the executed evidence) + a requirement→evidence matrix (every R-ID: implemented? verified
  how?) + any unmapped (scope-creep) changes.

`debate`: launch **two** independent reviewers (one instructed to argue "ship", one "block") and
reconcile their findings.

### Step 3 — Collect, sanity-check, and report
Read the reviewer's returned findings. Do a light second-pass sanity check (drop anything not
backed by cited evidence). Then:
1. Write `spec/{slug}/REVIEW.md` from the template (verification run, requirement→evidence matrix,
   findings most-severe-first, human-gate triggers).
2. Call `ReportFindings` with the verified findings (most-severe first; empty array if clean),
   `verdict` = CONFIRMED/PLAUSIBLE per finding.

### Step 4 — Set verdict + route
- **Clean (no CONFIRMED):**
  `set_phase.py spec/{slug}/TECH.md --verdict approved --reviewed-commit {sha} --touch` → recommend
  `/hs-publish`.
- **CONFIRMED findings:**
  `set_phase.py spec/{slug}/TECH.md --top-status blocked --verdict changes-requested
  --reviewed-commit {sha} --blocked-reason "<short>" --touch` → recommend `/hs-build` to fix the
  named R-IDs/invariants. If any CONFIRMED finding hit the coupled core / a security-DB invariant,
  **STOP and require explicit human sign-off** before any further step.
- **PLAUSIBLE only:** surface to the human for triage; do not auto-block.

Then **commit the review artifacts** so the tree stays clean for the loop:
```
git add spec/{slug}/REVIEW.md spec/{slug}/TECH.md
git commit -m "WIP: review cycle {n} — {verdict}"
```
**No `Co-Authored-By` trailer.** Do not push.

### Step 5 — Optional completeness sub-pass (`completeness`)
Launch a **separate** fresh subagent that *may* read `TECH.md` and ask: was every planned phase
actually shipped? did scope balloon beyond the appetite? Keep it isolated from the correctness pass so
the plan never contaminates the correctness verdict. Append its notes to `REVIEW.md`.

### Final report
Verdict, CONFIRMED/PLAUSIBLE counts, human-gate status, R-ID coverage, and the recommended next step
(`/hs-build` to remediate, or `/hs-publish` when approved). Note the review cycle count; if it's the
2nd–3rd cycle without convergence, escalate to the human.

## Examples

- `/hs-review` — blind correctness pass; write `REVIEW.md`; set verdict; route.
- `/hs-review debate` — two independent reviewers for a coupled-core diff.
- `/hs-review completeness` — correctness pass **plus** the separate did-we-ship-everything sub-pass.
- `/hs-review status` — show the current verdict and existing findings; no work.

## Notes

- The blind reviewer sees `GOAL.md`, not `PLAN.md`/`TECH.md`: `GOAL.md` is *what/why* (legitimate
  ground truth); the plan is the author's *how* (grading it invites plan-sycophancy).
- Single-model review in a fresh context removes anchoring bias but not family-level self-preference —
  hence the executed-evidence spine and the human gate. This is risk-reduction, not proof.
