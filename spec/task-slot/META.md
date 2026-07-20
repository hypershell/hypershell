# META — Expose a per-executor TASK_SLOT to tasks

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

- **slug:** task-slot

## What worked well

Brief, optional reinforcement: a part of a skill / the harness that materially helped, so `/hs-harness`
knows what **not** to change. One line each, naming the skill/step. Skip the section entirely if
nothing stands out.

- `hs-review` blind-correctness pass (Step 2): the reviewer, denied `TECH.md`, independently traced
  `manual.rst`'s `.. include::`s and found the man-page/`_alt`-snippet docs gap that TECH.md P3 had
  explicitly rationalized away as "unaffected" — the blindness + executed-evidence spine caught a real
  plan-sycophancy trap rather than inheriting the author's wrong conclusion.

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

## F1 — set_phase.py cannot update an existing phase's verify command
`origin=hs-build:P3 severity=medium category=tooling status=open target=.agents/factory/bin/set_phase.py`
- **What happened:** Remediation needed to strengthen P3's `verify` (the original `grep -rq TASK_SLOT a b` is OR-semantics and passed vacuously on `getting_started.rst` alone, never actually asserting the templates surface). `set_phase.py --verify` is gated behind `--add-phase` (`--name/--satisfies/--depends-on/--after/--verify require --add-phase`), so there is no scripted way to update the `verify` of an existing / reopened phase. I ran the stronger gate manually and documented it in the phase body, leaving the frontmatter `verify` field stale relative to the real gate.
- **Skill cause:** Remediation (hs-build Step 1.3) is a first-class path that *reopens* existing phases, and tightening a too-weak gate is a natural part of remediation — but the tooling only lets you set `verify` at phase-creation time, so the frontmatter (`next_phase.py`'s source of truth) and the actual gate drift on any reopened phase.
- **Recommended fix:** Let `set_phase.py --phase P<n> --verify "…"` update an existing phase's verify independent of `--add-phase` (same for `--name`).
- **Confidence:** high · **Effort:** small
