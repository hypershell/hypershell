# META — `hs cluster --no-db` leaves the database alone, and an autoscaling period below one second is refused

> **Harness feedback log** for this feature — the producer artifact of the factory's self-improvement
> loop. Written by the lifecycle skills (`hs-feature` / `hs-plan` / `hs-build` / `hs-review`) when the
> **skillset itself** costs something; read by `hs-publish` (surfaced in the PR) and applied by
> `/hs-harness`. This file is **orthogonal** to the `GOAL → PLAN → TECH → REVIEW` spine — it is about
> the *toolchain*, not the feature — and is retained on merge like the rest of `spec/cluster-nodb-and-zero-period/`.
>
> **Silence is the default.** The bar for a finding is one test: *was this the **skill's** fault — not
> mine, not the task's?* A merely-hard task, a self-inflicted error, or a one-off content/code issue
> (that belongs in `GOAL.md` / `REVIEW.md`) is **not** a finding. The blind `hs-review` correctness
> reviewer never reads this file — it would leak author intent.

- **slug:** cluster-nodb-and-zero-period

## Friction findings

Zero or more findings, appended below — each a markdown **section** so appending is a low-corruption
operation and a stdlib parser reads them (`uv run python .agents/factory/bin/meta_status.py
spec/cluster-nodb-and-zero-period/META.md`). Skills always write `status=open`; only `/hs-harness` flips it. `target` is a
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
- **What happened:** Two unshaped seeds (`cluster-no-db-initializes-sqlite`, `autoscale-zero-period-spins`) promoted under a slug that is neither stem. Argument Parsing takes `{slug}` from one seed's stem, Step 7 adds only `issues/{slug}.md`, and the template has one `Seed:` line; both statuses, both ROADMAP markers and a two-seed `Seeds:` line were done by hand.
- **Skill cause:** Same as `spec/cluster-launch-failures/META.md` F1 and `spec/task-input-fidelity/META.md` F1: the promotion steps assume one seed whose stem is the slug.
- **Recommended fix:** As there.
- **Confidence:** high · **Effort:** small

## F2 — Step 1/3 assume local `develop` in the main checkout; cycles run in worktrees · seen again
`origin=hs-feature:step3 severity=low category=instruction status=open target=.claude/skills/hs-feature/SKILL.md`
- **What happened:** The foreman created the worktree and `fix/cluster-nodb-and-zero-period` off `origin/develop` before this step; Step 1's "on `develop`" check and Step 3's `git switch -c` did not apply and were skipped on the invocation's instruction.
- **Skill cause:** Same as `spec/cluster-launch-failures/META.md` F2: the steps predate the worktree practice.
- **Recommended fix:** As there.
- **Confidence:** high · **Effort:** small

## F3 — The high-blast-radius research exception has no scale for an argument-handling edit
`origin=hs-plan:step3 severity=low category=instruction status=open target=.claude/skills/hs-plan/SKILL.md`
- **What happened:** A `small` fix with both root causes reproduced in shaping edits `server.py` only in `ServerApp`'s `-P` declaration, help string and `check_args()`. Step 3's high-blast-radius exception says "run the full fan-out … regardless of `appetite`/`kind`", while the invocation asked for no fan-out beyond what the skill allows. The planner pinned the touched contracts itself in one brief (`research/01`) and a digest.
- **Skill cause:** The exception keys on the file, not on the region touched, and "full fan-out" has no size; it does not say whether one planner-written brief that names the contracts pinned satisfies it when no FSM, thread, queue or database code is touched.
- **Recommended fix:** Keep the exception (the gate is not weakened), and say what satisfies it at small appetite: at least one brief per high-blast-radius file touched, naming the exact functions edited and the contracts pinned by probe; subagents optional when the edit is confined to argument parsing and help text.
- **Confidence:** med · **Effort:** small

## F4 — The deferral exception has no rule for a pre-existing defect whose only in-contract repair sits in a Non-goal area of the core
`origin=hs-review:step4 severity=low category=instruction status=open target=.agents/factory/review-rubric.md`
- **What happened:** A CONFIRMED pre-existing defect (a tiny positive configuration period still spins the autoscalers) contradicts the GOAL Outcome. Refusing or rounding the value fails R6, but a repair in the autoscalers' wait might satisfy every criterion while brushing a Non-goal and a high-blast-radius file. The exception's second condition ("cannot be repaired this cycle without failing that criterion") could be read either way, so the orchestrator left the deferral to the human instead of writing the seed.
- **Skill cause:** The exception weighs only GOAL criteria; it says nothing about a repair that satisfies the criteria but sits in a Non-goal or exceeds the appetite, nor who decides then.
- **Recommended fix:** In the rubric's *Verdict & loop* and hs-review Step 4, add: when the only repair that satisfies the criteria lies in a Non-goal area, beyond the appetite, or in the high-blast-radius core, record the finding as CONFIRMED, write no seed, and route the deferral-or-amend choice to the human gate.
- **Confidence:** med · **Effort:** small

## F5 — A cycle-2 blind reviewer has no sanctioned channel for the named findings or a ruling that amends GOAL outside GOAL.md
`origin=hs-review:step2 severity=low category=missing-guidance status=open target=.claude/skills/hs-review/SKILL.md`
- **What happened:** Cycle 2 had two jobs: grade the remediation of F1–F3, and apply a maintainer ruling that amends R6. That ruling is recorded in `PLAN.md`, and `GOAL.md` was not edited. Step 2 gives the reviewer "only" `GOAL.md`, the diff, `invariants.md` and the rubric. So the reviewer could see neither the findings it had to grade (they are in `REVIEW.md`, under the excluded `spec/`) nor the amended contract. The orchestrator had to restate both in the prompt, as binding readings the caller supplied.
- **Skill cause:** Step 2's input list does not cover cycle 2+. It names no input for the prior findings to verify, and none for a post-lock ruling that lives outside `GOAL.md`. A reviewer given the inputs exactly as listed would grade R6 as originally written and could not grade the remediation.
- **Recommended fix:** In Step 2, add a cycle-2+ input. Pass each prior finding's id, title, location and remedy target, without the earlier reviewer's reasoning. Pass every recorded ruling that amends a requirement, quoted verbatim as a contract amendment. Or require hs-build to write such a ruling into `GOAL.md` Clarifications, so the blind inputs carry it. Blindness to `PLAN.md`/`TECH.md` reasoning is unchanged.
- **Confidence:** med · **Effort:** small
