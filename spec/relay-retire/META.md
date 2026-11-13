# META — Retire a superseded relay, and wake the main thread for signals

> **Harness feedback log** for this feature — the producer artifact of the factory's self-improvement
> loop. Written by the lifecycle skills (`hs-feature` / `hs-plan` / `hs-build` / `hs-review`) when the
> **skillset itself** costs something; read by `hs-publish` (surfaced in the PR) and applied by
> `/hs-harness`. This file is **orthogonal** to the `GOAL → PLAN → TECH → REVIEW` spine — it is about
> the *toolchain*, not the feature — and is retained on merge like the rest of `spec/{slug}/`.

- **slug:** relay-retire

## Friction findings

<!-- Real findings are appended below this line by the lifecycle skills. -->

## F1 — No guidance for a sibling seed that the promoted GOAL subsumes · seen again
`origin=hs-feature:step4 severity=low category=missing-guidance status=open target=.claude/skills/hs-feature/SKILL.md`
- **What happened:** This cycle promotes two unshaped seeds at once (`relay-superseded-instance-retire`, primary, and `main-thread-signal-wake`). Step 4 and Step 7 cover one seed (`issues/{slug}.md`, whose stem is the slug); I had to find the `writeback-scoped-conditional` precedent to mark the second seed `adopted:relay-retire` too and update both ROADMAP markers.
- **Skill cause:** "Promoting an issue" and the Step 7 `git add issues/{slug}.md` line assume one seed whose stem equals the slug; neither the argument parsing nor the commit step names a second seed or a slug that differs from the seed stem.
- **Recommended fix:** As in `spec/writeback-scoped-conditional/META.md` F1, plus: allow several `issues/*.md` arguments (first is primary), let the slug differ from the primary seed's stem, and make Step 7 add every adopted seed.
- **Confidence:** high · **Effort:** small

## F2 — Step 1/3 assume local `develop` in the main checkout; cycles run in worktrees · seen again
`origin=hs-feature:step3 severity=low category=instruction status=open target=.claude/skills/hs-feature/SKILL.md`
- **What happened:** Step 3 says `git switch -c {branch} develop` in the current checkout, but the cycle was opened as `git worktree add -b fix/relay-retire .worktrees/relay-retire develop` so the main checkout stays on `develop`; `git worktree` is not in the skill's allowed-tools.
- **Skill cause:** The steps and allowed-tools predate the worktree practice every recent cycle uses.
- **Recommended fix:** Make Step 3 a worktree under `.worktrees/{slug}` and add `Bash(git worktree *)` to allowed-tools (same as the open step-3 findings in earlier cycles' META files).
- **Confidence:** high · **Effort:** small

## F3 — No step for recording GOAL-gate rulings before planning
`origin=hs-plan:step1 severity=low category=missing-guidance status=open target=.claude/skills/hs-plan/SKILL.md`
- **What happened:** The GOAL reached `hs-plan` with two Clarifications left "for the maintainer's sign-off at this gate", and the gate judge's confirmations came with the invocation. The skill has no step for recording them or for the GOAL lock; I followed the `relay-topology` precedent (`d0c8fd7`, "Record … GOAL-gate rulings") by hand: amend each Clarification's attribution, add a "Ruled at the GOAL gate" paragraph, and commit it apart from the plan.
- **Skill cause:** Step 1 only checks that `GOAL.md` is committed and free of `[NEEDS CLARIFICATION]`; it does not cover Clarifications marked for sign-off at the gate, nor say that the gate's rulings are recorded in `GOAL.md` in their own commit before the plan commit.
- **Recommended fix:** In Step 1, add: "If Clarifications are marked for sign-off at this gate, record the gate's rulings (attribution + a dated 'Ruled at the GOAL gate' line) and commit `GOAL.md` alone (`[{category}] Record {slug} GOAL-gate rulings`) before research; STOP if no ruling was given."
- **Confidence:** high · **Effort:** small

## F4 — No place to record plan-gate rulings handed to the first build
`origin=hs-build:P1 severity=low category=missing-guidance status=open target=.claude/skills/hs-build/SKILL.md`
- **What happened:** The P1 invocation carried the plan-gate rulings (deviation rows approved, clarifications accepted) with an instruction to record them "as the skill directs". `hs-build` has no step for it, and Step 1 requires a clean tree before any work; I followed the `relay-topology` precedent (`f01dce0`) by hand: mark PLAN.md's status approved, note the acceptance under the deviation table and §5, and commit that alone before the build commit.
- **Skill cause:** Neither `hs-plan` (which ends at the draft) nor `hs-build` (which starts at the FSM) owns the plan gate, so the rulings' home and commit are left to precedent.
- **Recommended fix:** In `hs-build` Step 1, add: "If the invocation carries plan-gate rulings and PLAN.md still reads `Draft for review`, record them (status line `Approved at the plan gate (who, date)`, a dated 'Ruled at the plan gate' paragraph) and commit PLAN.md alone (`[{category}] Record {slug} plan-gate rulings`) before building." Same family as F3.
- **Confidence:** high · **Effort:** small

## F5 — The injected diffstat and Step 1 base read the local `develop`, which is stale in a worktree cycle · seen again
`origin=hs-review:step1 severity=low category=instruction status=open target=.claude/skills/hs-review/SKILL.md`
- **What happened:** "Current state" runs `git diff --stat develop...HEAD` and Step 1 says the base defaults to `develop`; in this worktree the local `develop` ref lags `origin/develop` (`fb89670`), so the foreman had to tell the reviewer to fetch and diff against `origin/develop` instead.
- **Skill cause:** The skill names the local branch where every cycle's base is the remote one; the same assumption as F2, in `hs-review`.
- **Recommended fix:** Inject `git fetch origin develop` and diff against `origin/{base}` (merge base) in "Current state" and Step 2's diff command; say so in Step 1.
- **Confidence:** high · **Effort:** small

## F6 — The required docs build replaces the worktree's `.venv` when it holds another Python · seen again
`origin=hs-review:step2 severity=medium category=tooling status=open target=.agents/factory/review-rubric.md`
- **What happened:** The rubric requires the docs build with `-W`; `AGENTS.md` gives it as `uv run --python 3.12 --group docs sphinx-build …`. Run in the worktree, uv removed and recreated `.venv` on 3.12, which broke a full-suite run going on beside it (it was stopped and re-run, and `.venv` re-synced). Run in the sandbox, `sphinx_sitemap` also fails on a socket bind.
- **Skill cause:** Neither the rubric nor `AGENTS.md` § Docs says that the `--python 3.12` pin re-creates the project environment, or how to keep it apart.
- **Recommended fix:** Give the docs build its own environment in the rubric and `AGENTS.md` (`UV_PROJECT_ENVIRONMENT=$TMPDIR/docs-venv uv run --python 3.12 --group docs sphinx-build …`), and note that it needs the sandbox off for `sphinx_sitemap`.
- **Confidence:** high · **Effort:** small

## F7 — A later cycle cannot re-run the probes that confirmed the earlier cycle's findings
`origin=hs-review:step3 severity=low category=missing-guidance status=open target=.agents/factory/review-rubric.md`
- **What happened:** Cycle 2 had to grade whether RV1 and RV2 were fixed. Cycle 1's reproductions were scratch probes, not kept, and the builder's new regression tests cannot run against the reviewed cycle-1 source (their modules import the remediation's new names), so cycle 2 rebuilt both probes from the REVIEW.md prose to show red on `f98614f` and green on the head.
- **Skill cause:** The conduct rules forbid the reviewer writing tracked files other than REVIEW/TECH/META, so a CONFIRMED finding's reproduction is always discarded; neither the skill nor the rubric says how a cycle 2+ shows that a named finding is fixed (red on the reviewed commit, green on the head).
- **Recommended fix:** Let the orchestrator keep each CONFIRMED finding's probe beside REVIEW.md (e.g. `spec/{slug}/review/RV{n}_probe.py`, self-contained, runnable with `PYTHONPATH` on an exported `src`), and add to the cycle 2+ step: re-run each named finding's probe against the reviewed commit and the head, and record both results.
- **Confidence:** med · **Effort:** small

## F8 — No rule for an `hs-review` that is itself the fresh subagent
`origin=hs-review:step2 severity=low category=instruction status=open target=.claude/skills/hs-review/SKILL.md`
- **What happened:** Both cycles were run by `hs-review` spawned by the foreman as a fresh subagent with only the task statement. Step 2 says to delegate the correctness pass to another fresh subagent; the fallback covers only "subagents unavailable", so each cycle took the fallback by judgment and said so in REVIEW.md's Mode line.
- **Skill cause:** Step 2 assumes `hs-review` runs in the foreman's (possibly biased) session; it does not say that a skill invocation already in a clean, curated context may run the pass itself.
- **Recommended fix:** In Step 2 / Harness portability, add: "If this invocation is itself a fresh subagent given only the task statement, run the correctness pass here under the same blindness rules (read TECH.md frontmatter only; META.md only after the verdict), and record that in REVIEW.md's Mode line." This keeps blind-review integrity; it does not weaken it.
- **Confidence:** high · **Effort:** small
