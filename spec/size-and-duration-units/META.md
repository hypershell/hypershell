# META — Sizes and durations written with common units are read as written

> **Harness feedback log** for this feature — the producer artifact of the factory's self-improvement
> loop. Written by the lifecycle skills (`hs-feature` / `hs-plan` / `hs-build` / `hs-review`) when the
> **skillset itself** costs something; read by `hs-publish` (surfaced in the PR) and applied by
> `/hs-harness`. This file is **orthogonal** to the `GOAL → PLAN → TECH → REVIEW` spine — it is about
> the *toolchain*, not the feature — and is retained on merge like the rest of `spec/{slug}/`.
>
> **Silence is the default.** The bar for a finding is one test: *was this the **skill's** fault — not
> mine, not the task's?* A merely-hard task, a self-inflicted error, or a one-off content/code issue
> (that belongs in `GOAL.md` / `REVIEW.md`) is **not** a finding. The blind `hs-review` correctness
> reviewer never reads this file — it would leak author intent.

- **slug:** size-and-duration-units

## What worked well

- `hs-plan` Step 3's high-blast-radius and GOAL-deferred-unknowns triggers: the briefs found that the
  tag charset blocks `timeout:1:30:00` before `Task.new`, which a lean plan would have missed.

## Friction findings

Zero or more findings, appended below — each a markdown **section** so appending is a low-corruption
operation and a stdlib parser reads them (`uv run python .agents/factory/bin/meta_status.py
spec/{slug}/META.md`). Skills always write `status=open`; only `/hs-harness` flips it. `target` is a
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

## F1 — The skill's `next_phase.py` validation command runs uv in project mode · seen again (hs-build Step 1)
`origin=hs-plan:step-6 severity=low category=tooling status=open target=.agents/skills/hs-plan/SKILL.md`
- **What happened:** Step 6 (and the TECH template's header) says to validate with `uv run python
  .agents/factory/bin/next_phase.py …`; the planner ran `uv run --no-project --with pyyaml python …`
  instead, because in project mode uv syncs the project and leaves an untracked `uv.lock` in the tree
  the skill requires to stay clean.
- **Skill cause:** the factory scripts need only PyYAML, but the command the skill gives makes uv treat
  the repo as a project.
- **Recommended fix:** use `uv run --no-project --with pyyaml python .agents/factory/bin/<script>.py` in
  `hs-plan` Step 6, `templates/TECH.md`, and the other skills that call the factory scripts.
- **Confidence:** med · **Effort:** small

## F2 — A verify step that reads `HEAD` cannot see the uncommitted phase it gates
`origin=hs-build:P2 severity=low category=missing-guidance status=open target=.agents/skills/hs-plan/SKILL.md`
- **What happened:** P2's verify ends with `git merge-tree … 2.8.6 HEAD`. `hs-build` runs verify before
  the phase commit, so `HEAD` was P1 and the cherry-pick check would have passed without looking at
  P2's edits. The builder ran it against a temporary `git commit-tree` of the working tree instead,
  then again after the commit.
- **Skill cause:** `hs-plan` gives no rule that a verify command must test the working tree, and
  `hs-build` Step 4 says to run the verify as written before Step 7 commits.
- **Recommended fix:** in `hs-plan`, require verify commands to read the working tree; for a
  merge-tree check, show the temporary-commit form (`git add -A` into a scratch index, `git write-tree`,
  `git commit-tree -p HEAD`) instead of `HEAD`.
- **Confidence:** high · **Effort:** small
