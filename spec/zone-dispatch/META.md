# META — Zone-aware, demand-driven dispatch (zone, cycle 2)

> **Harness feedback log** for this feature — the producer artifact of the factory's self-improvement
> loop. Written by the lifecycle skills (`hs-feature` / `hs-plan` / `hs-build` / `hs-review`) when the
> **skillset itself** costs something; read by `hs-publish` (surfaced in the PR) and applied by
> `/hs-harness`. Silence is the default.

- **slug:** zone-dispatch

## What worked well

- `hs-plan` Step 3's high-blast-radius exception plus one-brief-per-file research: six parallel
  briefs surfaced a cross-cutting conflict (the in-memory server and `hs submit --queue` still use
  `scheduled` remotely) from two independent angles (01, 03), before it could reach the design.

## Friction findings

## F1 — hs-plan Step 8's `git add -A spec/{slug}` is the same broad staging as hs-build's · seen again · seen again (hs-build Step 7, P1–P2)
`origin=hs-plan:step-8 severity=low category=instruction status=open target=.agents/skills/hs-plan/SKILL.md`
- **What happened:** The foreman required explicit-path staging; Step 8 prescribes
  `git add -A spec/{slug}`, so the planner had to depart from the skill text.
- **Skill cause:** Same assumption as `spec/zone-queue-primitive/META.md` F11 (hs-build Step 7):
  the skill assumes nothing else writes under the staged path in a shared worktree.
- **Recommended fix:** Stage `spec/{slug}/PLAN.md`, `TECH.md`, `META.md` and `research/*.md` by
  path and check `git diff --cached --stat` before committing; fix both skills together.
- **Confidence:** high · **Effort:** small

## F2 — No guidance for planning on a base that lacks a prerequisite the GOAL depends on
`origin=hs-plan:step-6 severity=medium category=missing-guidance status=open target=.agents/skills/hs-plan/SKILL.md`
- **What happened:** The GOAL's R11 needs `data/upgrade.py` (#69), and its sequencing ruling needs
  `writeback-scoped-conditional`; neither is in the branch's base (Z1's head). The planner had to
  invent a "build precondition" section, a fail-fast guard in P1's `verify:`, and a
  `blocked_reason` for it.
- **Skill cause:** Step 1 checks the branch and GOAL but not that the base contains what the GOAL
  depends on; Step 6 has no pattern for a pre-build rebase gate (unlike the external-wait pattern
  Z1 introduced for evidence).
- **Recommended fix:** Add to Step 6 (and the TECH template) an optional `requires:` list on the
  top-level frontmatter (refs or paths that must be present), checked by `next_phase.py`, which
  reports `blocked: awaiting base` instead of the first phase. Keep it out of `verify:` so a missing
  prerequisite never counts as a red gate.
- **Confidence:** med · **Effort:** medium

## F3 — A verify line that benches `--feature HEAD` cannot run before the phase's own commit
`origin=hs-build:P2 severity=medium category=instruction status=open target=.agents/skills/hs-build/SKILL.md`
- **What happened:** P2's verify runs `bench_queue.py parity --feature HEAD`, which builds the
  *committed* tip; Step 4 (verify) comes before Step 7 (commit), so the gate would bench the
  previous phase. The build staged the phase and benched a throwaway `git commit-tree` object.
- **Skill cause:** Steps 4 and 7 assume every gate reads the working tree; the harness's gates
  read refs. Neither the skill nor the TECH template says how to gate a ref-based bench.
- **Recommended fix:** In Step 4, say: when a verify names `HEAD` as a build ref, stage the phase
  by path and pass `$(git commit-tree $(git write-tree) -p HEAD -m candidate)` in its place; or
  have plans write such gates against a `CANDIDATE` placeholder the skill fills in.
- **Confidence:** high · **Effort:** small

## F4 — The plan's list of pinned tests to re-point missed two other pinning test files
`origin=hs-build:P2 severity=medium category=missing-guidance status=open target=.agents/skills/hs-plan/SKILL.md`
- **What happened:** N3 named the five `test_server_fsm.py` tests that pin the replaced posting,
  but `tests/test_retry_gate.py` (drives `load_bundle` on a queue-less scheduler, expects PACK)
  and `tests/test_queue_gc.py` (pins four server objects per connected client) also pin it. The
  ruling allows only the five, so the build had to reshape the design (PACK kept, own-dispatcher
  fallback, lazy dispatcher proxy) and found it only from a red full suite.
- **Skill cause:** hs-plan's invariant gate checks prose invariants, not the tests that pin the
  mechanism a plan replaces; nothing asks the planner to grep the suite for those internals.
- **Recommended fix:** Add to hs-plan's Step 3 (high blast radius): grep `tests/` for each
  state name, method and constant the plan removes or reshapes, and list every hit in TECH's
  open rulings, not only the obvious file.
- **Confidence:** high · **Effort:** small

