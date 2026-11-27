# META — TLS client reaches the server on the address family it listens on

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

- **slug:** tls-client-address-family

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

## F1 — Blast-radius research exception has no depth scale for an already-diagnosed fix
`origin=hs-plan:step3 severity=low category=instruction status=open target=.agents/skills/hs-plan/SKILL.md`
- **What happened:** a `kind: fix`, `appetite: small` change to one function in `core/queue.py`, with the
  root cause already diagnosed in the GOAL, tripped Step 3's high-blast-radius exception ("run the full
  fan-out … regardless"). The planner had to decide alone what "full" means here, and settled on two
  sequential briefs plus a digest.
- **Skill cause:** the exception says when to fan out but not how much. "Full fan-out" reads as the
  `appetite: big` procedure (parallel agents, one per rabbit hole), which is out of proportion when the only
  unknowns are the touched contracts. The exception's own rationale is to pin those contracts.
- **Recommended fix:** in Step 3's high-blast-radius exception, scale depth to the unknowns: with a known
  root cause, a fan-out scoped to the touched contracts (one brief per touched high-blast-radius
  file/contract, which may be written inline) satisfies it; the parallel `appetite: big` fan-out is for when
  the mechanism is also unknown. This keeps the trigger itself and does not weaken the gate.
- **Confidence:** med · **Effort:** small

## F2 — hs-build commits with `git add -A` while plans and conventions say stage by explicit path
`origin=hs-build:P1 severity=low category=instruction status=open target=.agents/skills/hs-build/SKILL.md`
- **What happened:** Step 7 prescribes `git add -A` (and Step 6 says the META note "rides in this
  phase's atomic `git add -A` commit"), while this cycle's `TECH.md` Conventions, written by `hs-plan`,
  say "Stage by explicit path". The builder had to pick one; it staged by explicit path.
- **Skill cause:** two factory documents give opposite staging rules for the same commit. `git add -A`
  also sweeps in any stray untracked file in the worktree, which the clean-tree pre-flight only checks
  at the start of the run.
- **Recommended fix:** in Step 7 (and the Step 6 reference), stage the phase's `Touches:` paths plus
  `spec/{slug}/` explicitly, and run `git status --porcelain` before committing to confirm nothing else
  changed.
- **Confidence:** med · **Effort:** small

## F3 — A GOAL criterion whose evidence lives in TECH.md has no route past review blindness
`origin=hs-review:step-2 severity=low category=missing-guidance status=open target=.agents/skills/hs-review/SKILL.md`
- **What happened:** R6 requires `TECH.md` to state how the cherry-pick check was made, but the blind pass may not read `TECH.md`. The skill names no way to grade that criterion, so the orchestrator ran one narrow grep after the blind pass and recorded it in REVIEW.md.
- **Skill cause:** Step 2 forbids `TECH.md` to the reviewer, and Step 3 gives the orchestrator no step for R-IDs that point at `TECH.md`. hs-feature also lets such an R-ID be shaped.
- **Recommended fix:** Add a Step 3 rule. For an R-ID whose evidence is a statement in `TECH.md`, the orchestrator greps for that statement only after the blind pass returns, and records it in REVIEW.md. Optionally, hs-feature should steer such R-IDs toward the commit or PR body. Blindness is unchanged, because the reviewer still never reads `TECH.md`.
- **Confidence:** high · **Effort:** small

## F4 — temp_site.sh deletes with `rm -rf`, against AGENTS.md's `del, not rm` rule
`origin=hs-review:step-2 severity=low category=tooling status=open target=.agents/factory/bin/temp_site.sh`
- **What happened:** The rubric tells the reviewer to drive the CLI through `temp_site.sh`. Its exit trap runs `rm -rf "$site"`, which this environment forbids, so the reviewer reproduced its isolation by hand in each probe.
- **Skill cause:** Factory tooling breaks the repository's own environment rule (AGENTS.md: "`del`, not `rm`").
- **Recommended fix:** Use `del` when it is on `PATH`, else `rm -rf`, or leave the site in `$TMPDIR` and print its path. Keep the env isolation unchanged.
- **Confidence:** high · **Effort:** small

## F5 — No route for an orchestrator that is already the fresh blind context · seen again (hs-review:step-2, cycle 2)
`origin=hs-review:step-2 severity=low category=instruction status=open target=.agents/skills/hs-review/SKILL.md`
- **What happened:** The foreman spawned this session as the blind reviewer and told it to run the skill as written. Step 2 says to delegate to a fresh subagent, the agent harness says not to re-delegate the whole assignment, and the portability fallback covers only "subagents unavailable". The pass ran inline, by judgment.
- **Skill cause:** The skill assumes the orchestrator is a non-blind session, and it does not say when an inline pass is allowed.
- **Recommended fix:** Allow the inline correctness pass whenever the session has not read `PLAN.md`, `TECH.md` content, `research/` or `META.md`, and require REVIEW.md to say which mode ran. This does not weaken blindness. It states the condition delegation exists to secure.
- **Confidence:** med · **Effort:** small

## F6 — No review mode for a delta that lands after an approved verdict
`origin=hs-review:step-3 severity=low category=missing-guidance status=open target=.agents/skills/hs-review/SKILL.md`
- **What happened:** Cycle 1 approved, then a CI follow-up changed one test file. The foreman scoped cycle 2 to that delta (`{last_reviewed_commit}..HEAD`, spec-excluded) and asked for a check that `src/` was unchanged. Step 3 names only two modes for a later cycle: a fresh blind pass over the full `{base}...HEAD` diff, or verification of the named findings' remediation. There were no findings to remediate, so REVIEW.md had to describe a third mode in its own words.
- **Skill cause:** The skill assumes a later cycle follows `changes-requested`. It has no route for re-review after `approved`, when commits land past `review.last_reviewed_commit`. Nor does it give the diff command for that case.
- **Recommended fix:** Add a "delta" mode to Step 3: diff `{last_reviewed_commit}..HEAD -- . ':(exclude)spec/'`, require a stated check of which product paths the delta touches, and fall back to a full pass when it touches a high-blast-radius file. Name the mode in the section header line.
- **Confidence:** med · **Effort:** small
