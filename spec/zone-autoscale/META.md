# META — Zone-aware autoscaling (zone, cycle 3)

> **Harness feedback log** for this feature — the producer artifact of the factory's self-improvement
> loop. Written by the lifecycle skills (`hs-feature` / `hs-plan` / `hs-build` / `hs-review`) when the
> **skillset itself** costs something; read by `hs-publish` (surfaced in the PR) and applied by
> `/hs-harness`. This file is **orthogonal** to the `GOAL → PLAN → TECH → REVIEW` spine — it is about
> the *toolchain*, not the feature — and is retained on merge like the rest of `spec/zone-autoscale/`.
>
> **Silence is the default.** The bar for a finding is one test: *was this the **skill's** fault — not
> mine, not the task's?* A merely-hard task, a self-inflicted error, or a one-off content/code issue
> (that belongs in `GOAL.md` / `REVIEW.md`) is **not** a finding. The blind `hs-review` correctness
> reviewer never reads this file — it would leak author intent.

- **slug:** zone-autoscale

## What worked well

Brief, optional reinforcement: a part of a skill / the harness that materially helped, so `/hs-harness`
knows what **not** to change. One line each, naming the skill/step. Skip the section entirely if
nothing stands out.

- `hs-feature` Step 4, "Promoting an issue" (`shaped`): adopt as written, re-confirm against `develop`, cite drift. It kept a
  three-cycle seed's settled clarifications out of re-litigation and pointed the work at line drift.
- `hs-plan` Step 3, briefs written to `research/NN-topic.md` by parallel subagents: three briefs (signal,
  relay seam, launcher/verify) came back with file:line findings and a rehearsed drive, so the plan
  synthesized from files, not transcripts.

## Friction findings

Zero or more findings, appended below — each a markdown **section** so appending is a low-corruption
operation and a stdlib parser reads them (`uv run python .agents/factory/bin/meta_status.py
spec/zone-autoscale/META.md`). Skills always write `status=open`; only `/hs-harness` flips it. `target` is a
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

## F1 — Step 3 branches from local `develop`, which Step 1 allows to be behind `origin`
`origin=hs-feature:step-3 severity=low category=instruction status=open target=.agents/skills/hs-feature/SKILL.md`
- **What happened:** the run started in a worktree detached at `origin/develop`; local `develop` was two commits behind. Step 3's `git switch -c {branch} develop` would have started the cycle on the stale base, so the branch was created from `HEAD` instead.
- **Skill cause:** Step 1.2 says a behind `develop` is "not fatal" and only to note it, but Step 3 hard-codes `develop` as the start point, so the note never changes the outcome and the GOAL's drift citations can be taken against code the branch does not contain.
- **Recommended fix:** in Step 3, branch from the commit the pre-flight confirmed (`origin/develop` after the fetch, or `HEAD` when it equals it), or make Step 1.2 fast-forward `develop` before branching.
- **Confidence:** med · **Effort:** small

## F2 — No rule for R-ID numbering when a seed is promoted in several cycles
`origin=hs-feature:step-4 severity=low category=missing-guidance status=open target=.agents/skills/hs-feature/SKILL.md`
- **What happened:** the `zone` seed's remaining R13–R17 had to become this GOAL's R-IDs. The seed says its numbering "is kept so citations elsewhere still resolve"; the template numbers from `R1`; Z2 renumbered with the seed ID in brackets. The precedent was followed (R1–R5 with `[seed R13]`…), by inference.
- **Skill cause:** "Promoting an issue" covers `shaped` seeds but not a seed split across cycles, so the numbering convention lives only in an earlier GOAL.
- **Recommended fix:** add one line to Step 4: when promoting part of a multi-cycle seed, number from `R1` and give the seed's R-ID in brackets on each criterion.
- **Confidence:** med · **Effort:** small

## F3 — No rule for a gate question that arises during planning when the rest of the plan stands
`origin=hs-plan:step-4 severity=low category=missing-guidance status=open target=.agents/skills/hs-plan/SKILL.md`
- **What happened:** two R-IDs disagreed at one point. R3 says `--zone` is appended to every launch; R4 says the zone-less case is identical to today. One design choice decides it, and the rest of the plan does not depend on it. The skill says to STOP only for markers in `GOAL.md` and for an unavoidable invariant conflict. So the plan was committed with a `[NEEDS CLARIFICATION]` in PLAN §2.5 and the question was raised at the sign-off gate, by inference.
- **Skill cause:** Steps 4–6 do not say whether a plan may carry a scoped `[NEEDS CLARIFICATION]` to the gate, or whether `hs-build` must refuse to start while one is open.
- **Recommended fix:** add to Step 4 or 9: a design question confined to named phase items may be committed as a `[NEEDS CLARIFICATION]` in PLAN §5 with the proposed reading, listed in the Step 9 report. `hs-build` stops before any phase whose checklist names it until it is resolved.
- **Confidence:** med · **Effort:** small
