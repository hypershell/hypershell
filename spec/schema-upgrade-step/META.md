# META — Open 2.8 databases in 2.9: an idempotent schema-upgrade step

> **Harness feedback log** for this feature — the producer artifact of the factory's self-improvement
> loop. Written by the lifecycle skills (`hs-feature` / `hs-plan` / `hs-build` / `hs-review`) when the
> **skillset itself** costs something; read by `hs-publish` (surfaced in the PR) and applied by
> `/hs-harness`. This file is **orthogonal** to the `GOAL → PLAN → TECH → REVIEW` spine — it is about
> the *toolchain*, not the feature — and is retained on merge like the rest of `spec/schema-upgrade-step/`.
>
> **Silence is the default.** The bar for a finding is one test: *was this the **skill's** fault — not
> mine, not the task's?* A merely-hard task, a self-inflicted error, or a one-off content/code issue
> (that belongs in `GOAL.md` / `REVIEW.md`) is **not** a finding. The blind `hs-review` correctness
> reviewer never reads this file — it would leak author intent.

- **slug:** schema-upgrade-step

## Friction findings

Zero or more findings, appended below — each a markdown **section** so appending is a low-corruption
operation and a stdlib parser reads them (`uv run python .agents/factory/bin/meta_status.py
spec/schema-upgrade-step/META.md`). Skills always write `status=open`; only `/hs-harness` flips it. `target` is a
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

## F1 — Step 3 branches from local `develop`, which may be stale; `origin/develop` start point sets a wrong upstream
`origin=hs-feature:step3 severity=medium category=instruction status=open target=.agents/skills/hs-feature/SKILL.md`
- **What happened:** The local `develop` was behind `origin/develop`, so the cycle was opened in a detached worktree at `origin/develop` and branched with `git switch -c fix/schema-upgrade-step origin/develop`. That deviates from Step 3, and it set the new branch's upstream to `origin/develop`, which had to be removed by hand with `git branch --unset-upstream`.
- **Skill cause:** Step 1 treats "`develop` is behind" as a note ("not fatal"). Step 3 then branches from that stale local ref. Step 1's literal "on `develop`" check cannot pass in a worktree, which is the normal way to run parallel cycles. Neither step mentions that a remote-ref start point turns on upstream tracking.
- **Recommended fix:** In Step 3, branch with `git switch --no-track -c {branch} origin/develop` after the Step 1 fetch. In Step 1, accept a detached `HEAD` equal to `origin/develop` (or a fresh worktree created there) as satisfying the "on develop" pre-flight.
- **Confidence:** high · **Effort:** small

## F2 — No guidance for carving one R-ID out of a shaped seed into a different cycle
`origin=hs-feature:step4 severity=low category=missing-guidance status=open target=.agents/skills/hs-feature/SKILL.md`
- **What happened:** This cycle discharges `timescale` R1 while the rest of that seed stays a future cycle. "Promoting an issue" covers only whole-seed adoption (`status: adopted:{slug}`), so the partial discharge is recorded only in this GOAL's Related materials, and the seed still reads `shaped` with R1 intact.
- **Skill cause:** The `status:` vocabulary and the Step 4 promotion rules have no partial state and no instruction for marking a carved-out R-ID on the seed, or for deferring that marking to `/hs-roadmap`.
- **Recommended fix:** Add a "carving a requirement" paragraph: record the discharge in the receiving GOAL, and either annotate the seed's R-ID (`*(carved into {slug})*`) in the same commit or explicitly leave it to `/hs-roadmap`. Teach `/hs-roadmap` to find and retire carved R-IDs when the receiving cycle lands.
- **Confidence:** med · **Effort:** small
