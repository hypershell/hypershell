# META — A task-row write that waited on a row lock still lands on a TimescaleDB hypertable

> **Harness feedback log** for this feature: the producer artifact of the factory's self-improvement
> loop. See [`.agents/factory/templates/META.md`](../../.agents/factory/templates/META.md) for the
> schema and the bar. Silence is the default.

- **slug:** hypertable-update-retry

## Friction findings

## F1 — Step 1/3 assume local `develop` in the main checkout; cycles run in worktrees off `origin/develop` · seen again
`origin=hs-feature:step3 severity=low category=instruction status=open target=.agents/skills/hs-feature/SKILL.md`
- **What happened:** Local `develop` was stale against `origin/develop` (`0e8275e`), and the main checkout held untracked files from other work. The cycle was cut with `git branch --no-track fix/hypertable-update-retry origin/develop` and a worktree under `.worktrees/`, so Step 1's "on `develop`, clean tree" check and Step 3's `git switch -c {branch} develop` were bypassed rather than followed.
- **Skill cause:** The same finding as `spec/schema-upgrade-step/META.md` F1 and `spec/writeback-prebuilt-update/META.md` F1, still open. The skill text still branches from the local ref and has no worktree path.
- **Recommended fix:** As recorded there: branch with `--no-track` from `origin/develop` after the fetch, and accept a fresh worktree at `origin/develop` as passing the pre-flight.
- **Confidence:** high · **Effort:** small

## F2 — No way to express an external wait (foreman runs, CI) in a TECH phase · seen again
`origin=hs-plan:step6 severity=low category=template status=open target=.agents/factory/templates/TECH.md`
- **What happened:** R7's red/green can be proved only on a TimescaleDB server the build machine does not have, so P6 is an external wait. The plan again hand-wrote the `blocked` + `blocked_reason` convention from `writeback-prebuilt-update` P5.
- **Skill cause:** Same as `spec/writeback-prebuilt-update/META.md` F4 and `spec/timescale/META.md` F3, still open: neither the skill nor the TECH template names an external-wait phase shape.
- **Recommended fix:** As recorded there.
- **Confidence:** high · **Effort:** small

## F3 — No guidance on evidence scripts the plan commits under `spec/{slug}/` · seen again
`origin=hs-plan:step8 severity=low category=missing-guidance status=open target=.agents/skills/hs-plan/SKILL.md`
- **What happened:** The plan commits `contention_run.py` and `check_evidence.py` beside PLAN/TECH, following `writeback-prebuilt-update`'s precedent rather than any instruction; "Never build" reads as forbidding them.
- **Skill cause:** Same as `spec/writeback-prebuilt-update/META.md` F5, still open.
- **Recommended fix:** As recorded there.
- **Confidence:** high · **Effort:** small

## F4 — A verify's schema-line guard matches benign uses of `__tablename__`, and fails silently
`origin=hs-build:P2 severity=low category=steering status=open target=.agents/skills/hs-plan/SKILL.md`
- **What happened:** P2's and P3's `verify:` refuse any added `src/` line matching `__tablename__` (meant to catch a schema change). The new helper's `StaleDataError` message and trace line used `cls.__tablename__`, so the gate failed with no output at all (the `! git diff … | grep -qE …` step is quiet), and the cause had to be found by running the chain's pieces by hand. The code was changed to `cls.__table__.name` to pass.
- **Skill cause:** `hs-plan` gives no guidance on scope-guard greps in verify commands: a guard written as a bare token match on a symbol the codebase also reads at runtime (`__tablename__`) misfires, and an `&&` chain of quiet `grep -q` steps gives no clue which step failed.
- **Recommended fix:** In `hs-plan`'s verify guidance, anchor schema guards to declarations (e.g. `^[-+]\s*__tablename__\s*=`, `mapped_column\(`), and have each scope check echo a label on failure (`|| { echo 'scope: schema line'; false; }`).
- **Confidence:** high · **Effort:** small
