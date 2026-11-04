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
