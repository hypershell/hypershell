# META — TimescaleDB provider: hypertables out of the box (TS1)

> **Harness feedback log** for this feature — the producer artifact of the factory's self-improvement
> loop. Written by the lifecycle skills (`hs-feature` / `hs-plan` / `hs-build` / `hs-review`) when the
> **skillset itself** costs something; read by `hs-publish` (surfaced in the PR) and applied by
> `/hs-harness`. This file is **orthogonal** to the `GOAL → PLAN → TECH → REVIEW` spine — it is about
> the *toolchain*, not the feature — and is retained on merge like the rest of `spec/timescale/`.
>
> **Silence is the default.** The bar for a finding is one test: *was this the **skill's** fault — not
> mine, not the task's?* A merely-hard task, a self-inflicted error, or a one-off content/code issue
> (that belongs in `GOAL.md` / `REVIEW.md`) is **not** a finding. The blind `hs-review` correctness
> reviewer never reads this file — it would leak author intent.

- **slug:** timescale

## What worked well

Brief, optional reinforcement: a part of a skill / the harness that materially helped, so `/hs-harness`
knows what **not** to change. One line each, naming the skill/step. Skip the section entirely if
nothing stands out.

- <what helped, and in which skill/step>
- hs-review rubric, "grade a seed as prose": it surfaced cycle 1's F4, a new seed misdescribing CI as the diff leaves it.
- hs-review Step 3, cycle 2+ defaulting to a fresh blind pass over the whole diff: it found a cross-list overlap that cycle 1 had dropped (F5), and seed prose that the base merge made stale again (F7).

## Friction findings

Zero or more findings, appended below — each a markdown **section** so appending is a low-corruption
operation and a stdlib parser reads them (`uv run python .agents/factory/bin/meta_status.py
spec/timescale/META.md`). Skills always write `status=open`; only `/hs-harness` flips it. `target` is a
best-guess file with **no line number** (re-derive the exact edit at apply time to avoid staleness). If
an equivalent finding already exists, append "· seen again" to its title instead of duplicating —
recurrence is signal, not bloat.

Field enums — `severity`: `high` (a safety / gate / correctness gap) `| medium | low`; `category`:
`instruction | steering | tooling | template | missing-guidance`; `status`: `open` (written by skills)
`| applied | rejected | deferred` (written by `/hs-harness`).

Schema (copy one block per finding, appending it **after** this fence — the fence is illustrative and
is skipped by the parser):

```markdown
## F1 — <one-line title of the skillset problem>
`origin=<skill>:<step> severity=<high|medium|low> category=<instruction|steering|tooling|template|missing-guidance> status=open target=<best-guess file>`
- **What happened:** <what the skill made you do, or fail to do>.
- **Skill cause:** <why this is the instructions' fault — not yours, not the task's>.
- **Recommended fix:** <the concrete change to the skill / template / script>.
- **Confidence:** <high|med|low> · **Effort:** <small|medium|large>
```

<!-- Real findings are appended below this line by the lifecycle skills. -->

## F1 — Step 3 branches from local `develop`, which may be stale · seen again
`origin=hs-feature:step3 severity=medium category=instruction status=open target=.agents/skills/hs-feature/SKILL.md`
- **What happened:** A recurrence of `spec/group-advance-retry-gate/META.md` F1 (and
  `spec/zone-queue-primitive/META.md` F1). Local `develop` was stale, so the cycle was opened with
  `git worktree add -b feature/timescale .worktrees/timescale origin/develop` instead of Step 3's
  `git switch -c {branch} develop`, and Step 1's "on `develop`" check could not apply.
- **Skill cause:** Unchanged from the original finding.
- **Recommended fix:** As in the original finding.
- **Confidence:** high · **Effort:** small

## F2 — No rule for R-ID numbering when a seed's R-IDs are partly discharged elsewhere
`origin=hs-feature:step4 severity=low category=missing-guidance status=open target=.agents/skills/hs-feature/SKILL.md`
- **What happened:** The `timescale` seed's R1 was discharged by another cycle
  (`schema-upgrade-step`), and this cycle promotes the remaining R2–R10. Step 4 says nothing about
  whether the GOAL renumbers from R1 or keeps the seed's numbers. The seed's numbers were kept, so
  the seed, the ROADMAP and the GOAL agree, and the GOAL deliberately has no R1. Whole-seed
  promotion (`adopted:{slug}`) was used, since nothing in the seed remains unassigned.
- **Skill cause:** A sibling of `spec/zone-queue-primitive/META.md` F2 and
  `spec/schema-upgrade-step/META.md` F2: Step 4 models promotion as whole-seed only, so a seed
  whose R-IDs are split across cycles has no stated numbering or status rule.
- **Recommended fix:** When the partial-promotion rule from those findings is written, add one
  line: a GOAL keeps the seed's R-ID numbers and names the ones discharged elsewhere; a seed whose
  remaining R-IDs all go to one cycle is promoted whole (`adopted:{slug}`).
- **Confidence:** med · **Effort:** small

## F3 — No way to express an external wait (CI evidence) in a TECH phase · seen again
`origin=hs-plan:step6 severity=low category=missing-guidance status=open target=.agents/factory/templates/TECH.md`
- **What happened:** A recurrence of `spec/schema-upgrade-step/META.md` F3 (with its F6). No
  TimescaleDB server exists locally, so every integration scenario is provable only by CI on a
  pushed draft PR. The plan again improvised run-B phases (P5, P7) whose `verify:` reads `gh run
  list`, gated on the last commit outside `spec/` (F6's recommendation) rather than `HEAD`, and
  `next_phase.py` will report them actionable although nothing local can satisfy them.
- **Skill cause:** Unchanged from the original finding: Step 6 and the TECH template have no field or
  convention for a precondition outside the FSM.
- **Recommended fix:** As in the original finding (`waits_on: "ci:<workflow>@<last code commit>"`,
  documented run-A/run-B split).
- **Seen again at build (hs-build:P6):** the foreman ordered P6 built before its `depends_on` P5
  (the CI wait), so the FSM now holds P6 `done` with P5 `pending`, and `next_phase.py` reports no
  warning for a done phase whose dependency is not done. A `waits_on` field would let the
  dependency be on code, not on the evidence phase.
- **Seen again at build (hs-build:P5):** the first CI run blocked P5 (attempt 1, the first block
  on this cause) on a test-fixture defect only a TimescaleDB server shows: its scheduler holds a
  session in the template database, so `CREATE DATABASE … TEMPLATE` fails. The fix landed while
  the FSM stayed at P5, recorded with `--record-attempt`; `hs-build` has no stated path for a fix
  found at an external-wait phase that touches no phase's checklist.
- **Confidence:** high · **Effort:** medium

## F4 — The planned Sphinx gate could never pass: its `grep -i warning` matches the build summary and a known import warning
`origin=hs-build:P4 severity=medium category=missing-guidance status=open target=.agents/skills/hs-plan/SKILL.md`
- **What happened:** P4's `verify:` piped `sphinx-build` through `grep -i 'warning'`, excluding only
  the `task_submit.rst`/`manual.rst` toctree lines. Sphinx's own summary (`build succeeded, 2
  warnings.`) and the `pkg_resources is deprecated` UserWarning that `sphinxcontrib.details` prints
  at import both match, so the gate was red on every tree, docs or no docs. It was amended twice
  through `set_phase.py --verify` (attempts 2) to exclude those two lines; a real Sphinx warning
  (a duplicate label, seen mid-build) still turns it red.
- **Skill cause:** `hs-plan` composes a docs gate by hand with no canonical, exercised form, and
  `AGENTS.md` § Docs lists only the toctree warnings as pre-existing, although
  `spec/task-card-view/TECH.md` already recorded the `pkg_resources` one.
- **Recommended fix:** One canonical sandbox-safe docs gate (e.g. a factory script that filters
  Sphinx `WARNING:` lines only, minus the listed pre-existing ones) that `hs-plan` cites instead of
  an inline pipeline; add the `pkg_resources` warning to `AGENTS.md` § Docs.
- **Confidence:** high · **Effort:** small

## F5 — hs-review Step 2 hands the blind reviewer `git log --oneline`, which leaks plan intent · seen again
`origin=hs-review:step2 severity=high category=instruction status=open target=.claude/skills/hs-review/SKILL.md`
- **What happened:** Step 2 says to give the reviewer `git log --oneline {base}..HEAD` with the diff.
  On this branch the subjects include "Record timescale plan gate rulings" and per-phase build
  subjects naming each design step ("P5: CI fixture — close template databases to TimescaleDB's
  scheduler"). That is the author's how, exactly what the `spec/` exclusion keeps out. The
  foreman had to override the skill ("no commit subjects, no `git log`"), as in another cycle's
  META F6.
- **Skill cause:** the curated-input list contradicts the skill's own blindness rule. The diff is
  filtered, but the commit log is not.
- **Recommended fix:** remove `git log` from Step 2's reviewer inputs and say why in the Safety
  Principles. Have the orchestrator keep the log to itself for the contract-drift check.
  `review-rubric.md`'s "What the reviewer sees" should list the log as excluded.
- **Seen again at review cycle 2 (hs-review:step2):** the foreman again overrode Step 2. The
  blind reviewer got the spec-excluded diff command and no `git log` or commit subjects.
- **Seen again at review cycle 3 (hs-review:step2):** the same override, a third time.
- **Confidence:** high · **Effort:** small

## F6 — The rubric's mandated CLI-drive tool, `temp_site.sh`, cleans up with `rm -rf`, so the review brief had to hand-roll site isolation
`origin=hs-review:step2 severity=low category=tooling status=open target=.agents/factory/bin/temp_site.sh`
- **What happened:** Step 2 and `review-rubric.md` tell the reviewer to drive the CLI through
  `.agents/factory/bin/temp_site.sh`. Its `trap 'rm -rf "$site"' EXIT` breaks the operating rules
  this cycle ran under (no `rm -rf`, scratch under a fixed `$TMPDIR` path, process groups with
  timeouts), and the foreman barred the script. The brief therefore spelled out the isolation by
  hand: `HYPERSHELL_CONFIG_FILE`, `HYPERSHELL_SITE`, `HYPERSHELL_DATABASE_FILE`,
  `HYPERSHELL_SERVER_PORT`, the site as cwd, and `UV_PROJECT`. It also had to say that the
  `timescale` provider needs a venv with the `postgres` extra, which the default sync lacks.
- **Skill cause:** the skill names a single tool for its executed-evidence spine and gives no
  fallback, and the tool's cleanup assumes a shell where `rm -rf` is allowed.
- **Recommended fix:** give `temp_site.sh` a keep-the-site mode (or a `--root DIR` with no
  deletion), and have it print the env block it sets. Add a fallback to the rubric's "Reviewer
  conduct" listing those variables, and name the extras a server provider needs.
- **Confidence:** med · **Effort:** small
