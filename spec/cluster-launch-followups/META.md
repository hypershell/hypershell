# META — A locked database delays a cluster's decision; a `--no-db` cluster whose work is done never reports a failed launch

> **Harness feedback log** for this feature — the producer artifact of the factory's self-improvement
> loop. Written by the lifecycle skills (`hs-feature` / `hs-plan` / `hs-build` / `hs-review`) when the
> **skillset itself** costs something; read by `hs-publish` (surfaced in the PR) and applied by
> `/hs-harness`. This file is **orthogonal** to the `GOAL → PLAN → TECH → REVIEW` spine — it is about
> the *toolchain*, not the feature — and is retained on merge like the rest of `spec/cluster-launch-followups/`.
>
> **Silence is the default.** The bar for a finding is one test: *was this the **skill's** fault — not
> mine, not the task's?* A merely-hard task, a self-inflicted error, or a one-off content/code issue
> (that belongs in `GOAL.md` / `REVIEW.md`) is **not** a finding. The blind `hs-review` correctness
> reviewer never reads this file — it would leak author intent.

- **slug:** cluster-launch-followups

## Friction findings

Zero or more findings, appended below — each a markdown **section** so appending is a low-corruption
operation and a stdlib parser reads them (`uv run python .agents/factory/bin/meta_status.py
spec/cluster-launch-followups/META.md`). Skills always write `status=open`; only `/hs-harness` flips it. `target` is a
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

## F1 — No guidance for promoting several seeds into one GOAL under a new slug · seen again
`origin=hs-feature:step4 severity=low category=missing-guidance status=open target=.claude/skills/hs-feature/SKILL.md`
- **What happened:** Two unshaped seeds (`nodb-cluster-finish-race`, `autoscale-read-database-locked`) promoted under a slug that is neither stem. Argument Parsing takes `{slug}` from one seed's stem, Step 7 adds only `issues/{slug}.md`, and the template has one `Seed:` line; both statuses, both ROADMAP markers and a two-seed `Seeds:` line were done by hand.
- **Skill cause:** Same as `spec/cluster-launch-failures/META.md` F1 and `spec/cluster-nodb-and-zero-period/META.md` F1: the promotion steps assume one seed whose stem is the slug.
- **Recommended fix:** As there.
- **Confidence:** high · **Effort:** small

## F2 — Step 1/3 assume local `develop` in the main checkout; cycles run in worktrees · seen again
`origin=hs-feature:step3 severity=low category=instruction status=open target=.claude/skills/hs-feature/SKILL.md`
- **What happened:** The foreman created the worktree and `fix/cluster-launch-followups` off `origin/develop` before this step; Step 1's "on `develop`" check and Step 3's `git switch -c` did not apply and were skipped on the invocation's instruction.
- **Skill cause:** Same as `spec/cluster-launch-failures/META.md` F2: the steps predate the worktree practice.
- **Recommended fix:** As there.
- **Confidence:** high · **Effort:** small

## F3 — Research prototypes live in `$TMPDIR` and vanish before the build · seen again
`origin=hs-plan:step3 severity=low category=missing-guidance status=open target=.claude/skills/hs-plan/SKILL.md`
- **What happened:** The test-research subagent built red-on-base test prototypes in the session scratchpad, which a routine `/tmp` cleanup can purge before `/hs-build`. They were copied by hand into `research/prototypes/`, and renamed away from `test_*.py` with their `conftest.py` dropped: the repo sets no `testpaths`, so a bare `pytest` (the gate) would otherwise collect red prototypes from `spec/`.
- **Skill cause:** As `spec/zone-queue-primitive/META.md` F4: Step 3 names only `research/NN-topic.md` as research output, with no home or naming rule for runnable prototypes.
- **Recommended fix:** As there, plus: prototypes kept under `spec/{slug}/research/prototypes/` must not match pytest's `test_*.py`/`*_test.py` or ship a `conftest.py`; run them with `-p tests.conftest`.
- **Confidence:** high · **Effort:** small

## F4 — A coverage-only finding: "non-blocking" by the severity table, blocking by the loop rules · seen again
`origin=hs-review:step4 severity=high category=instruction status=open target=.agents/factory/review-rubric.md`
- **What happened:** R2's working-launcher clause has no test, although its behaviour is correct (drive G). Grading it meant choosing between two earlier cycles: `task-input-fidelity` called such a gap a non-blocking note (CONFIRMED needs observed wrong behaviour), and `contended-writes` called it CONFIRMED and blocking. This cycle followed the first and said so in `REVIEW.md`.
- **Skill cause:** As `spec/contended-writes/META.md` F8. The rubric's LOW row ("missing-but-non-blocking test coverage") and its rule that every CONFIRMED finding loops back still disagree. Step 4 does not settle which applies to a gap with no wrong behaviour.
- **Recommended fix:** As there. Any wording that exempts coverage-only findings from blocking needs the maintainer's ruling.
- **Confidence:** high · **Effort:** small
