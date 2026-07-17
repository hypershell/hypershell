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

**Harness portability.** Runs on any harness — see [`factory/portability.md`](../../factory/portability.md).
Fallbacks: run the *Current state* commands yourself if not auto-injected; ask in plain text and STOP if
`AskUserQuestion` is unavailable; **if subagents are unavailable, perform the correctness pass yourself
in a clean context** (you lose delegated blindness — compensate with executed evidence, per the rubric);
and skip `ReportFindings` (`REVIEW.md` is the durable record).

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
  `PLAN.md`, `TECH.md`, `research/`, or `META.md`** (the last leaks author intent / harness notes, same
  reason as PLAN/TECH). Only this skill (the orchestrator) reads `TECH.md`, and only
  for the `base`/`slug`/`kind` metadata — it must not pass PLAN/TECH *content* into the reviewer prompt.
  **The diff must be blind too:** those artifacts are committed on the branch, so a plain
  `git diff {base}...HEAD` hands the reviewer PLAN/TECH/research (and any prior cycle's REVIEW.md) as
  added hunks — the `':(exclude)spec/'` pathspec below is load-bearing, not cosmetic.
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
Confirm a feature/fix branch; resolve `{slug}` from the branch, confirm `base` (defaults to `develop`),
and read `kind` (the commit `{category}`) from TECH.md. Capture the head SHA (`git rev-parse HEAD`). If `TECH.md` `status`
is not `in_review`/`done`, note it (the build may be incomplete) and ask whether to proceed.

### Step 2 — Delegate the correctness pass (fresh subagent)
Launch a fresh `general-purpose` reviewer via the `Agent` tool. Give it, inline, **only**:
- the full text of `spec/{slug}/GOAL.md` (the contract — R-IDs);
- the command to produce the diff: `git diff {base}...HEAD -- . ':(exclude)spec/'` (and
  `git log --oneline {base}..HEAD`) — never a bare `git diff {base}...HEAD`, which would leak the
  committed spec artifacts into the reviewer's context;
- the full text of `invariants.md` and `review-rubric.md`;
- the instruction: work in the runnable repo, follow the refutation protocol, **run** the relevant
  `verify` commands / drive the CLI in a `temp_site`, and **do NOT read `PLAN.md`/`TECH.md`/`research/`
  or `META.md`** (`META.md` is the harness self-improvement log — it leaks author intent, same reason
  as PLAN/TECH).
- the conduct rule: **no edits to tracked files** (revert any instrumentation before returning;
  `git status --porcelain` must be clean on hand-back), and the rubric's "Verdict & loop" section is
  the orchestrator's job — the reviewer must not write `REVIEW.md`, call `ReportFindings`, or run
  `set_phase.py`;
- required return: a structured findings list (severity, CONFIRMED/PLAUSIBLE, file:line, failure
  scenario, the executed evidence) + a requirement→evidence matrix (every R-ID: implemented? verified
  how?) + any unmapped (scope-creep) changes.

`debate`: launch **two** independent reviewers (one instructed to argue "ship", one "block") and
reconcile their findings.

### Step 3 — Collect, sanity-check, and report
Read the reviewer's returned findings. Confirm the reviewer left the tree clean
(`git status --porcelain` empty; if not, inspect and revert its leftovers before anything else).
Do a light second-pass sanity check (drop anything not backed by cited evidence). Then:
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

**Meta-note (orchestrator only · silence by default).** Reflect on the **review skillset itself** — not
the diff, not the code. *You (the orchestrator)* may record a finding; the blind reviewer never does,
and content/correctness issues belong in `REVIEW.md`, not here. You may also add a one-line
**What worked well** note when a part of the review skillset materially helped. The bar for a *finding*
is the one test: *was this the skill's fault — not mine, not the task's?* (an ambiguous rubric step, a curated-input/allowed-tools
mismatch, guidance that made the delegation misfire). If met, record it in `spec/{slug}/META.md` (create
from [`templates/META.md`](../../factory/templates/META.md) if absent, else append) — ≤3 terse findings,
next unused `F#`, always `status=open`, "· seen again" instead of duplicating; a fix that would weaken a
non-negotiable gate (blind-review integrity, executed-evidence spine, the human gate, an `invariants.md`
item) is `severity=high` and must say so. **Records only** — `/hs-harness` applies fixes later:
```markdown
## F<n> — <one-line title>
`origin=hs-review:<step> severity=<high|medium|low> category=<instruction|steering|tooling|template|missing-guidance> status=open target=<best-guess file>`
- **What happened:** <what the skill made you do, or fail to do>.
- **Skill cause:** <why it's the instructions' fault — not yours, not the task's>.
- **Recommended fix:** <the change to the skill/template/script>.
- **Confidence:** <high|med|low> · **Effort:** <small|medium|large>
```

Then **commit the review artifacts** so the tree stays clean for the loop:
```
git add spec/{slug}/REVIEW.md spec/{slug}/TECH.md   # + spec/{slug}/META.md if you recorded a meta-note
git commit -m "[{category}] Review {slug}: cycle {n} — {verdict}"
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
