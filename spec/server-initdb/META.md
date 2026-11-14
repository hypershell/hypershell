# META — `hs server --initdb` initializes the database

> **Harness feedback log** for this feature — the producer artifact of the factory's self-improvement
> loop. Written by the lifecycle skills (`hs-feature` / `hs-plan` / `hs-build` / `hs-review`) when the
> **skillset itself** costs something; read by `hs-publish` (surfaced in the PR) and applied by
> `/hs-harness`. This file is **orthogonal** to the `GOAL → PLAN → TECH → REVIEW` spine — it is about
> the *toolchain*, not the feature — and is retained on merge like the rest of `spec/server-initdb/`.
>
> **Silence is the default.** The bar for a finding is one test: *was this the **skill's** fault — not
> mine, not the task's?* A merely-hard task, a self-inflicted error, or a one-off content/code issue
> (that belongs in `GOAL.md` / `REVIEW.md`) is **not** a finding. The blind `hs-review` correctness
> reviewer never reads this file — it would leak author intent.

- **slug:** server-initdb

## Friction findings

Zero or more findings, appended below — each a markdown **section** so appending is a low-corruption
operation and a stdlib parser reads them (`uv run python .agents/factory/bin/meta_status.py
spec/server-initdb/META.md`). Skills always write `status=open`; only `/hs-harness` flips it. `target` is a
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

## F1 — Step 1/3 assume local `develop` in the main checkout; cycles run in worktrees · seen again
`origin=hs-feature:step3 severity=low category=instruction status=open target=.claude/skills/hs-feature/SKILL.md`
- **What happened:** The foreman cut `fix/server-initdb` in `.worktrees/server-initdb` off `origin/develop` before invoking the skill, so Step 1's "on `develop`" check and Step 3's `git switch -c` did not apply and were skipped by instruction.
- **Skill cause:** The steps predate the worktree practice every recent cycle uses.
- **Recommended fix:** As the open step-3 findings in earlier cycles' META files: make Step 3 a worktree under `.worktrees/{slug}` off `origin/develop`, and let Step 1 accept a pre-cut cycle branch.
- **Confidence:** high · **Effort:** small

## F2 — Promoting an `unshaped` seed carries its `file:line` evidence without re-verifying it
`origin=hs-feature:step4 severity=low category=missing-guidance status=open target=.claude/skills/hs-feature/SKILL.md`
- **What happened:** The seed (written 2026-11-03) cited eight `file:line` locations; six had drifted on current `develop`, as had the `ROADMAP.md` entry's, and one claim ("only checks the schema") was imprecise. Re-verifying them, and probing the behaviour, also surfaced an adjacent defect (`hs server --no-db` touching the database) that became a sign-off question.
- **Skill cause:** "Promoting an issue" tells a `shaped` seed to "re-confirm the scope still holds against current `develop`", but for `unshaped` it says only "carry the evidence (`file:line`, mechanism …) into Problem", which reads as copy-forward. Shaping-only guidance ("no big code reads") pushes the same way.
- **Recommended fix:** For both statuses, say: re-check each cited `file:line` and the seed's stated mechanism against current `develop` (a targeted read, not research), cite the current locations in Problem, and list drift in a Clarification.
- **Confidence:** med · **Effort:** small
