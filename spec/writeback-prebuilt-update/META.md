# META — Result writeback at or below 2.8.3's CPU cost

> **Harness feedback log** for this feature: the producer artifact of the factory's self-improvement
> loop. See [`.agents/factory/templates/META.md`](../../.agents/factory/templates/META.md) for the
> schema and the bar. Silence is the default.

- **slug:** writeback-prebuilt-update

## Friction findings

## F1 — Step 1/3 assume local `develop` in the main checkout; cycles run in worktrees off `origin/develop` · seen again
`origin=hs-feature:step3 severity=low category=instruction status=open target=.agents/skills/hs-feature/SKILL.md`
- **What happened:** Local `develop` was stale against `origin/develop`, and the main checkout held another session's untracked files. The cycle was cut with `git worktree add --no-track -b fix/writeback-prebuilt-update .worktrees/writeback-prebuilt-update origin/develop`, so Step 1's "on `develop`, clean tree" check and Step 3's `git switch -c {branch} develop` were both bypassed rather than followed.
- **Skill cause:** The same finding as `spec/schema-upgrade-step/META.md` F1, still open. The skill text still branches from the local ref and has no worktree path.
- **Recommended fix:** As recorded there: branch with `--no-track` from `origin/develop` after the fetch, and accept a fresh worktree at `origin/develop` as passing the pre-flight.
- **Confidence:** high · **Effort:** small

## F2 — The `fix`-kind phrasing rule has no case for a performance regression
`origin=hs-feature:step4 severity=low category=missing-guidance status=open target=.agents/skills/hs-feature/SKILL.md`
- **What happened:** Step 4 says a fix's criteria state the observable broken→fixed behaviour, never the mechanism. For a throughput regression the observable is CPU time or tasks/s, which no deterministic test can check, while the guard the maintainer asked for (statement reuse, cache misses flat over N) is mechanism-shaped. The GOAL had to split the contract three ways by hand: measured CPU recorded in `spec/` (R1), a deterministic proxy test whose subject the plan chooses (R4), and cluster evidence owned by the foreman (R6).
- **Skill cause:** The rule assumes a functional defect, where the observable behaviour is also what a test asserts. It says nothing about non-functional criteria, so where measured evidence goes and how a proxy guard may name a mechanism has to be invented each time.
- **Recommended fix:** Add a sentence to Step 4: for a performance fix, state the observable cost as a criterion backed by recorded measurement in `spec/{slug}/` (not CI), and add a separate deterministic guard criterion that may name the mechanism as a candidate subject, left to `hs-plan` to choose.
- **Confidence:** med · **Effort:** small

## F3 — No instruction for seeding a non-goal that is real deferred work
`origin=hs-feature:step7 severity=low category=missing-guidance status=open target=.agents/skills/hs-feature/SKILL.md`
- **What happened:** One non-goal (other per-task server overheads) is real deferred work. `AGENTS.md`'s deferral table says it gets an `issues/` seed and a `ROADMAP.md` entry. The skill's Step 4 sends deferred scope to Non-goals only, and Step 7 stages `issues/` and `ROADMAP.md` only when promoting. Writing a new seed and committing it with the GOAL was a judgement call.
- **Skill cause:** The size circuit-breaker and Non-goals guidance stop at "record the deferred scope in Non-goals". They never point at the deferral table, so a deferral made while shaping can be lost in the GOAL's prose.
- **Recommended fix:** In Step 4, say that a non-goal that is real work, not just a scope fence, gets an `issues/{slug}.md` seed (`status: unshaped`) and a `ROADMAP.md` entry per `AGENTS.md`, linked from the Non-goal. In Step 7, stage those two files alongside the GOAL.
- **Confidence:** med · **Effort:** small
