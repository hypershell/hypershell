---
name: hs-build
description: >-
  Resume and execute a HyperShell feature's phased roadmap. Discovers spec/{slug}/TECH.md from the
  current feature/fix branch, reads the FSM via next_phase.py, implements the next phase (default one
  at a time), runs that phase's verify command, updates state via set_phase.py, and makes one atomic
  code+state commit. May amend TECH.md freely as reality dictates; only a GOAL.md contradiction
  forces a stop. The /continue-style driver of the software factory (see .agents/factory/methodology.md).
disable-model-invocation: true
argument-hint: "[status | dry run | phase P3 | through P5 | next 2 | bundle | skip review]"
allowed-tools: Read, Write, Edit, Grep, Glob, Bash(git status *), Bash(git branch *), Bash(git rev-parse *), Bash(git log *), Bash(git diff *), Bash(git add *), Bash(git commit *), Bash(uv run *), Bash(uv sync *), Bash(seq *)
---

# hs-build — execute the roadmap (resume-and-implement)

## When to Use

Invoke `/hs-build` on a feature/fix branch whose `spec/{slug}/TECH.md` exists, to execute the next
slice of the roadmap without re-explaining the project. The rhythm is **one-phase-then-stop**: scale
up with arguments when you trust the next chunk, scale down to a dry run when you don't. `TECH.md`
frontmatter is the resume ground-truth; `PLAN.md` is the authoritative design; `GOAL.md` R-IDs are the
locked contract; `research/` holds detail. Track progress **only** in `TECH.md` (via the scripts).

Reference: [`methodology.md`](../../factory/methodology.md),
[`invariants.md`](../../factory/invariants.md), and `AGENTS.md` (the constitution).

## User Instructions

Additional instructions provided with the invocation: $ARGUMENTS

## Current state (injected at load)

- Branch: !`git branch --show-current`
- Tree: !`git status --porcelain | head -n 20`
- Commits on branch (vs develop): !`git log --oneline develop..HEAD 2>/dev/null | head -n 15`
- FSM: resolved in **Step 1** by running `uv run python .agents/factory/bin/next_phase.py spec/{slug}/TECH.md` (a load-time injection can't strip the branch prefix to form `{slug}`).

## Argument Parsing

Parse `$ARGUMENTS` case-insensitively; if ambiguous, STOP and ask.

- `status` / `report` → summarize FSM state (via `next_phase.py`, Step 1) + last commit; no work.
- `dry run` / `plan only` / `preview` → identify the target phase and load context, but make **no**
  edits/commits — report the plan (checklist items, files, verify command, expected commit). 
- `phase P<n>` / `at P<n>` → execute that phase regardless of `current_phase`.
- `through P<n>` / `up to P<n>` → execute forward, stopping after `P<n>` completes.
- `next N` / `N phases` → execute the next `N` incomplete phases (each its own commit).
- `bundle` → collapse the run into a single commit (only for tightly-coupled phases).
- `skip review` → continue past the natural phase-boundary stop (use sparingly).
- No arguments → default next-phase-then-stop.

## Safety Principles

- **`next_phase.py` is the resume ground-truth**, re-run fresh every invocation (in Step 1). If it
  reports the FSM invalid or emits a `warnings` about pointer/status drift, **reconcile before
  acting** — do not guess.
- **On a feature/fix branch only** — never `develop`/`master`. Clean tree required (non-empty →
  STOP: commit, stash, or discard first).
- **A phase is the unit of work.** Execute every `[ ]` item in the target phase, not just the first.
  A phase is `done` only when all items are satisfied **and** its `verify:` command passes.
- **Verify by driving the CLI, not just tests.** Run the phase's `verify:` command; for behavior,
  exercise the real flow (e.g. `seq 100 | uv run hsx -t 'echo {}' -N4` then inspect `uv run hs list`
  / `hs info`). **Exit 0 is necessary but not sufficient** — assert a concrete post-condition (task
  count / final states / a known stdout token); a run that "completed" but left wrong task state is a
  FAIL. A red gate is a STOP condition — do not mark the phase done or advance state.
- **Amend `TECH.md` freely; GOAL is locked.** When reality diverges from the plan (a phase is wrong,
  needs splitting, or a new phase is required), rewrite `TECH.md` — regenerate frontmatter with
  `set_phase.py`, edit phase bodies as needed — and **note the amendment in the commit body**. But if
  the work contradicts a `GOAL.md` requirement (an R-ID), **STOP and escalate to the human** — never
  silently drift the contract.
- **Honor `AGENTS.md`**: code conventions (incl. declarative comments — no spec `R#`/`P#` ids in
  source), `exit_status` ranges, the task-lifecycle predicates, the
  same-commit rule (a CLI/feature change updates `docs/_include/*.rst` + `share/` completions), and
  the `cmdkit.app.exit_status` constants. Consult `invariants.md` for the footguns the phase touches.
- **Circuit breaker.** If a phase fails its verify gate across repeated attempts, or stays
  `hill: uphill` across builds (unknowns unresolved), **stop-and-re-shape**: STOP and recommend
  `/hs-plan` (or human input) rather than looping. Respect the appetite.
- **`develop`/`master` are off-limits; never push, squash, force-push, or open PRs** — that is
  `/hs-publish`. **No `Co-Authored-By` trailer.**

## Procedure

### Step 0 — status / dry-run (when requested)
`status`: run `next_phase.py` (Step 1), report the FSM + last commit, and stop. `dry run`: do
Steps 1–2, then report the plan that *would* run and stop (no edits/commits).

### Step 1 — Pre-flight
1. Clean tree on a feature/fix branch (from the injection). STOP otherwise.
2. Resolve `{slug}` from the branch, then run
   `uv run python .agents/factory/bin/next_phase.py spec/{slug}/TECH.md` and read its output. If it
   errored or warned of drift, reconcile (`set_phase.py --current …`) or STOP and report.
   `uv sync --quiet` if deps may have changed.
3. **Remediation mode.** If the FSM shows `top_status: blocked` or `review.verdict:
   changes-requested`, a prior `/hs-review` requested changes: read `spec/{slug}/REVIEW.md`, then make
   the fixes actionable by amending `TECH.md` — **prefer reopening** the existing phase(s) whose
   `satisfies` covers the failing R-IDs (`set_phase.py --phase P<n> --phase-status in_progress`,
   script-safe); only if a fix maps to no existing phase, carefully append a new remediation phase and
   re-validate with `next_phase.py`. Set `--top-status in_progress`, then proceed. If a finding
   contradicts a `GOAL.md` R-ID (not just the plan), STOP and escalate instead.

### Step 2 — Identify target phase + load context
1. Target = the `next_phase` output from Step 1 (or the argument-selected phase). Confirm its
   `depends_on` are `done`; if not, STOP.
2. Read `spec/{slug}/PLAN.md` and the relevant `research/` for the detail behind the phase's
   checklist. Read the actual files the phase will touch **before** editing them.

### Step 3 — Implement the phase
Execute every `[ ]` item to AGENTS.md conventions. Sanity-check as you go
(`uv run python -c "import hypershell..."`; drive the CLI for behavior). If implementation reveals a
real correction to `TECH.md`/`PLAN.md`, amend `TECH.md` (Step 5 rules); if it reveals a `GOAL.md`
contradiction, STOP and escalate.

### Step 4 — Verify gate
Run the phase's `verify:` command (plus any CLI drive). "Green" means the **asserted post-condition
held** — the observed output (task counts, final states, a known token) is correct — not merely that the
command exited 0. Green → proceed. Red → STOP; do not mark done or advance state; consider the circuit
breaker.

### Step 5 — Update `TECH.md` (the resume contract)
1. Check off the phase's `[ ]` items in the body.
2. Advance state with the script (regenerate — never hand-edit YAML):
   ```
   uv run python .agents/factory/bin/set_phase.py spec/{slug}/TECH.md \
       --phase {id} --phase-status done --current {next_id_or_done} --touch
   ```
   For a mid-phase amendment, edit phase bodies and use `set_phase.py` for any status/pointer/hill
   change. If all phases are now done, also `--top-status in_review`.

### Step 6 — Meta-note (self-improvement loop · silence by default)
Before committing, reflect on the **skillset itself** — not the task, not the code. Write nothing
unless the bar is met.

**The bar (one test):** *was this the skill's fault — not mine, not the task's?* **Qualifies:** you
hand-fixed a command this skill gave (wrong flag/path, a bare `python`/`python3` that should be `uv run
python`, unquoted YAML); a genuinely ambiguous instruction; a verify gate that passed/failed
misleadingly (e.g. "exit 0" hid a wrong final state); an allowed-tools/step mismatch. **Stay silent
for:** a merely hard task; your own error against clear guidance; a one-off content/code issue (→
`REVIEW.md` at review time, not here); a vague preference.

If (and only if) the bar is met, record it in `spec/{slug}/META.md` (create from
[`templates/META.md`](../../factory/templates/META.md) if absent, else append). You may also add a
one-line **What worked well** note when a part of this skill materially helped. Caps: **≤3 findings**,
terse; if an equivalent finding already exists, append "· seen again" rather than duplicating
(recurrence across phases is exactly the signal `/hs-harness` acts on); a fix that would weaken a
non-negotiable gate (tests, the CLI-drive verify, an `invariants.md` item) is `severity=high` and must
say so. **Records only** — `/hs-harness` applies fixes later, human-reviewed. Use the next unused `F#`;
always write `status=open`; append the finding as a section **outside** any code fence:
```markdown
## F<n> — <one-line title>
`origin=hs-build:{id} severity=<high|medium|low> category=<instruction|steering|tooling|template|missing-guidance> status=open target=<best-guess file>`
- **What happened:** <what the skill made you do, or fail to do>.
- **Skill cause:** <why it's the instructions' fault — not yours, not the task's>.
- **Recommended fix:** <the change to the skill/template/script>.
- **Confidence:** <high|med|low> · **Effort:** <small|medium|large>
```
`hs-build` is **the richest source** and runs **per phase across separate invocations** — appending to
the file (not memory) is exactly how you preserve a finding a context reset would erase. The note rides
in this phase's atomic `git add -A` commit below.

### Step 7 — Commit (atomic code + state)
```
git add -A
git commit -m "[{category}] Build {slug} {id}: {phase name}"
```
`{category}` is the `TECH.md` `kind` (feature|fix|refactor), available from the Step 1 `next_phase.py`
output — the same house style as `hs-feature`/`hs-plan`. There is **no `WIP:` prefix**: every branch
commit is squashed into the single PR-title commit at `hs-publish`, so subjects only need to read well
in the PR's commits tab. For a remediation commit (a phase reopened by `hs-review`), keep the `{id}`
and describe the fix, e.g. `[feature] Build {slug} P1: F1 — full covering index (R17)`. Body only for
non-obvious decisions or to record a `TECH.md` amendment. **No co-author trailer.** Do not push.
`bundle` → one commit for the whole run.

### Step 8 — Continue or stop
Default / at a phase boundary: stop and report. `through`/`next`/multi: loop to Step 2 with the next
phase until the stop condition, a phase boundary (unless `skip review`), or a STOP from Steps 3–4.

### Final report
Phases completed (ids + one-line summaries), any `TECH.md` amendments made (and why), any `[ ]`
deferred, the new `current_phase` + statuses, verify-gate results, and open questions/blockers. When
the FSM is fully `done` (`status: in_review`), recommend a **clean-session** `/hs-review`.

## Examples

- `/hs-build` — next incomplete phase, run its verify, one clean `[category]` commit, stop + report.
- `/hs-build status` — FSM state + last commit; no work.
- `/hs-build dry run phase P4` — report what P4 would do (items, files, verify, commit); no edits.
- `/hs-build through P3` — run each incomplete phase up to P3, one commit each, stopping at boundaries.

## Notes

- Never advance state on a checkbox alone — the `verify:` command is the gate.
- Keep `current_phase` accurate even when stopping mid-phase on a STOP condition (do not advance past
  a partially-done phase).
- This skill never ships to `develop`/`master`, squashes, or force-pushes — that's `/hs-publish`. Out
  of scope (`merge`, `push`, `open PR`, `bump version`) → STOP and point at `/hs-publish`.
