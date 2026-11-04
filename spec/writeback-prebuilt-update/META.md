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

## F4 — No way to express an external wait (CI run, cluster hand-back) in a TECH phase · seen again
`origin=hs-plan:step6 severity=low category=template status=open target=.agents/factory/templates/TECH.md`
- **What happened:** R6 is a foreman hand-back and R3's PostgreSQL/TimescaleDB green comes only from CI on a PR the foreman opens. P5 had to be authored as an ordinary `pending` phase whose checklist tells `hs-build` to set itself `blocked` and stop, and whose `verify:` pins the final fix as the last commit touching `src/` (not `HEAD`) so committing the evidence does not invalidate it.
- **Skill cause:** The same gap as `spec/schema-upgrade-step/META.md` F3/F6, `spec/timescale/META.md` F3 and `spec/writeback-scoped-conditional/META.md` F3, all still open: the TECH template has no external-wait phase kind and no guidance on pinning evidence to code rather than `HEAD`.
- **Recommended fix:** As recorded there. Add an `external: true` (or `awaits:`) phase field that `next_phase.py` reports as waiting rather than actionable. In Step 6, say that evidence gates compare against the last code commit, not `HEAD`.
- **Confidence:** high · **Effort:** small

## F5 — No guidance on evidence or trial scripts the plan commits under `spec/{slug}/`
`origin=hs-plan:step4 severity=low category=missing-guidance status=open target=.agents/skills/hs-plan/SKILL.md`
- **What happened:** R1 needs a rerunnable in-process measurement, and R6 needs a checker for the foreman's numbers. Following the group cycle's `port-trial.sh` precedent, the plan wrote and committed `cpu_bench.py` and `check_evidence.py` under `spec/writeback-prebuilt-update/` and ran the bench on the base. The skill's "Never build", its artifact list (PLAN, TECH, research, META) and its `allowed-tools` cover none of this, so whether a planner may write such a script, run it, and commit it was a judgement call each time.
- **Skill cause:** The skill assumes every `verify:` is a pytest or CLI command. It has no case for performance or port evidence, where the verify gate is a script that has to exist before any phase can name it.
- **Recommended fix:** In Step 4, allow `spec/{slug}/` evidence or trial scripts as plan artifacts when a GOAL criterion needs recorded measurement or a port trial. Require them to run offline, under a kill timer and from extracted trees or worktrees, and to be run once on the base to show the bar can fail. List them in Step 8's staging and add `git archive`/`python` to `allowed-tools`.
- **Confidence:** med · **Effort:** small
