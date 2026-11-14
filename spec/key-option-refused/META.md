# META — Refuse the queue key on a command line

> **Harness feedback log** for this feature — the producer artifact of the factory's self-improvement
> loop. Written by the lifecycle skills (`hs-feature` / `hs-plan` / `hs-build` / `hs-review`) when the
> **skillset itself** costs something; read by `hs-publish` (surfaced in the PR) and applied by
> `/hs-harness`. This file is **orthogonal** to the `GOAL → PLAN → TECH → REVIEW` spine — it is about
> the *toolchain*, not the feature — and is retained on merge like the rest of `spec/key-option-refused/`.
>
> **Silence is the default.** The bar for a finding is one test: *was this the **skill's** fault — not
> mine, not the task's?* A merely-hard task, a self-inflicted error, or a one-off content/code issue
> (that belongs in `GOAL.md` / `REVIEW.md`) is **not** a finding. The blind `hs-review` correctness
> reviewer never reads this file — it would leak author intent.

- **slug:** key-option-refused

## Friction findings

Zero or more findings, appended below — each a markdown **section** so appending is a low-corruption
operation and a stdlib parser reads them (`uv run python .agents/factory/bin/meta_status.py
spec/key-option-refused/META.md`). Skills always write `status=open`; only `/hs-harness` flips it. `target` is a
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

## F1 — Step 1/3 assume local `develop`; cycles are cut in a worktree off `origin/develop` · seen again
`origin=hs-feature:step1 severity=low category=instruction status=open target=.agents/skills/hs-feature/SKILL.md`
- **What happened:** The foreman pre-created `fix/key-option-refused` from `origin/develop` in a worktree under `.worktrees/`; Step 1's "on `develop`" check and Step 3's `git switch -c {branch} develop` had to be set aside by the brief.
- **Skill cause:** The skill knows only the main-checkout flow, though the worktree flow is now the norm for foreman runs.
- **Recommended fix:** Accept a pre-created `{branch}` at the head of `origin/develop` in a worktree as a valid Step 1/3 state (verify top-level, branch name, clean tree, base commit), and skip the switch.
- **Confidence:** high · **Effort:** small

## F2 — No way to promote part of a seed and keep the rest alive · seen again
`origin=hs-feature:step4 severity=low category=missing-guidance status=open target=.agents/skills/hs-feature/SKILL.md`
- **What happened:** The seed holds a 2.8 half (warn) and a 2.9 half (refuse); only the 2.9 half is this cycle. Promotion marks the whole seed `adopted:key-option-refused`, so the 2.8 half survives only as a Non-goal and is retired with the seed when this lands.
- **Skill cause:** Promotion is all-or-nothing; there is no step to split off or re-seed the unpromoted part.
- **Recommended fix:** When a promotion leaves part of a seed out, have the skill write that part as its own `issues/` seed (or a ROADMAP line) in the same commit.
- **Confidence:** med · **Effort:** small

## F3 — No guidance for a docs build in a `verify:` that leaves the worktree's environment alone
`origin=hs-plan:step6 severity=low category=missing-guidance status=open target=.agents/factory/templates/TECH.md`
- **What happened:** The docs `-W` build and the man-page proof needed their own environment (`UV_PROJECT_ENVIRONMENT=$TMPDIR/docs-venv uv run --python 3.12 --group docs …`) and the sandbox off; the foreman had to supply that command in the brief. Earlier TECH files (e.g. `server-auth-variant-collision`) use `uv run --python 3.12 --group docs sphinx-build …`, which re-creates the worktree's `.venv` under 3.12 in the middle of a build.
- **Skill cause:** Step 6 and the TECH template show only pytest and CLI-drive verify examples; nothing says how a docs build in `verify:` should run, so a planner copies the last TECH that did one.
- **Recommended fix:** Add a docs-build `verify:` example to the TECH template (separate `UV_PROJECT_ENVIRONMENT` under `$TMPDIR`, output under `$TMPDIR`, a note that `sphinx_sitemap` needs the sandbox off), and a man-page grep recipe for cycles that prove a docs change through `-b man` (roff writes `\-k` and `\fB…\fP`, so a plain grep misses).
- **Confidence:** high · **Effort:** small
