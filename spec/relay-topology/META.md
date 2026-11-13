# META — Relay site-topology autoscaling: fill and spread across sites

> **Harness feedback log** for this feature — the producer artifact of the factory's self-improvement
> loop. Written by the lifecycle skills (`hs-feature` / `hs-plan` / `hs-build` / `hs-review`) when the
> **skillset itself** costs something; read by `hs-publish` (surfaced in the PR) and applied by
> `/hs-harness`. This file is **orthogonal** to the `GOAL → PLAN → TECH → REVIEW` spine — it is about
> the *toolchain*, not the feature — and is retained on merge like the rest of `spec/relay-topology/`.
>
> **Silence is the default.** The bar for a finding is one test: *was this the **skill's** fault — not
> mine, not the task's?* A merely-hard task, a self-inflicted error, or a one-off content/code issue
> (that belongs in `GOAL.md` / `REVIEW.md`) is **not** a finding. The blind `hs-review` correctness
> reviewer never reads this file — it would leak author intent.

- **slug:** relay-topology

## What worked well

Brief, optional reinforcement: a part of a skill / the harness that materially helped, so `/hs-harness`
knows what **not** to change. One line each, naming the skill/step. Skip the section entirely if
nothing stands out.

- `hs-feature` Step 4, "Promoting an issue" (`shaped`): re-confirm, cite drift, adopt largely as written kept the promotion to the two amended R-IDs and the open questions, without reopening the seed's settled clarifications.

## Friction findings

Zero or more findings, appended below — each a markdown **section** so appending is a low-corruption
operation and a stdlib parser reads them (`uv run python .agents/factory/bin/meta_status.py
spec/relay-topology/META.md`). Skills always write `status=open`; only `/hs-harness` flips it. `target` is a
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

## F1 — Pre-flight requires branch `develop`; factory cycles open in a worktree detached at `origin/develop` · seen again
`origin=hs-feature:step-1 severity=low category=instruction status=open target=.agents/skills/hs-feature/SKILL.md`
- **What happened:** the cycle opened in a worktree detached at `origin/develop` (`22684fd`, equal to local `develop` this time). Step 1's "if the injected branch is not `develop`, STOP" cannot pass there, and Step 3's `git switch -c {branch} develop` names a ref the worktree is not on; the branch was created from `HEAD` on the foreman's instruction. Recurs from `spec/schema-upgrade-step/META.md` and `spec/zone-autoscale/META.md` F1.
- **Skill cause:** Steps 1 and 3 assume a checkout of `develop`, while parallel cycles run in detached worktrees.
- **Recommended fix:** in Step 1, accept a detached `HEAD` equal to `origin/develop` after the fetch; in Step 3, branch with `git switch --no-track -c {branch} origin/develop` (or `HEAD` when equal).
- **Confidence:** high · **Effort:** small

## F2 — Step 6 gives a phase one `verify:`, with no form for a split gate (run A built here, run B judged after an external run)
`origin=hs-plan:step-6 severity=low category=template status=open target=.agents/factory/templates/TECH.md`
- **What happened:** the brief required the multi-node phase's gate to be split: run A builds and rehearses the batch script, and run B judges the foreman's records. The frontmatter holds one `verify:` per phase, so P5 carries run B's command, and run A's gate and the `crest`/`in_progress` wait had to be written as prose in the phase body.
- **Skill cause:** Step 6 and the TECH template assume a phase's verify can run when the build finishes. They have no field or convention for an external-wait gate. The same gap was met at build time in `spec/relay/META.md` F6.
- **Recommended fix:** add an optional phase field (for example `verify_build:` beside `verify:`, or `external_wait: true`) that `next_phase.py`/`set_phase.py` understand. Document in Step 6 that a phase gated on an external run sets it.
- **Confidence:** med · **Effort:** small
