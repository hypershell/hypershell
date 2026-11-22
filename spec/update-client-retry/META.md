# META — `hs update --delete`, the client's bundle keys, and terminal statuses

> **Harness feedback log** for this feature — the producer artifact of the factory's self-improvement
> loop. Written by the lifecycle skills (`hs-feature` / `hs-plan` / `hs-build` / `hs-review`) when the
> **skillset itself** costs something; read by `hs-publish` (surfaced in the PR) and applied by
> `/hs-harness`. This file is **orthogonal** to the `GOAL → PLAN → TECH → REVIEW` spine — it is about
> the *toolchain*, not the feature — and is retained on merge like the rest of `spec/{slug}/`.

- **slug:** update-client-retry

## What worked well

- `hs-feature` Step 4's `kind: fix` rule (criteria as observable broken→fixed behaviour, not the
  suspected mechanism) kept R6 free of `increment_group`'s `attempt >= attempts` detail, which the
  plan owns.
- `hs-plan` Step 3's one-brief-per-rabbit-hole fan-out found two defects the GOAL did not name: cmdkit's
  `KeyError` on a displaced `submit.bundlesize_env` (a live `hs client` crash), and a declaration-named
  tag overriding a retry's column, which shaped R7's design.

## Friction findings

<!-- Real findings are appended below this line by the lifecycle skills. -->

## F1 — No guidance for promoting several seeds into one GOAL under a new slug · seen again
`origin=hs-feature:step4 severity=low category=missing-guidance status=open target=.claude/skills/hs-feature/SKILL.md`
- **What happened:** Three seeds were promoted under `update-client-retry`, a slug that is none of their stems, on the foreman's brief.
- **Skill cause:** As `spec/contended-writes/META.md` F1 and `spec/config-set-and-unread/META.md` F1: Argument Parsing takes `{slug}` from one seed's stem and Step 4 describes one seed.
- **Recommended fix:** As recorded there.
- **Confidence:** high · **Effort:** small

## F2 — Step 1/3 assume local `develop` in the main checkout; cycles run in worktrees · seen again
`origin=hs-feature:step3 severity=low category=instruction status=open target=.claude/skills/hs-feature/SKILL.md`
- **What happened:** The worktree on `fix/update-client-retry` off `origin/develop` `6a0c28ea` existed before this step; Step 1's "on `develop`" check and Step 3's `git switch -c` were skipped on the brief's instruction.
- **Skill cause:** As `spec/contended-writes/META.md` F2; the skill text is unchanged.
- **Recommended fix:** As recorded there.
- **Confidence:** high · **Effort:** small

## F3 — Promoting an `unshaped` seed does not say to re-verify its evidence against `develop` · seen again
`origin=hs-feature:step4 severity=medium category=instruction status=open target=.claude/skills/hs-feature/SKILL.md`
- **What happened:** Re-running the seeds' claims on `6a0c28ea` (as the brief asked) found two defects beside them (the legacy `hs update` form drops action options; a retry drops cores, memory and walltime), showed one seed claim holds only for the Python API, and found the seeds' cited docs contradict each other. The skill's "no code reads" and allowed-tools (no `uv run`, no throwaway site) would have carried the seeds unchecked.
- **Skill cause:** As `spec/env-config-keys/META.md` F3 and `spec/config-set-and-unread/META.md` F3.
- **Recommended fix:** As recorded there.
- **Confidence:** high · **Effort:** small

## F4 — Step 4's ROADMAP marker rule does not say what to do when a Seed line has no marker
`origin=hs-feature:step4 severity=low category=instruction status=open target=.claude/skills/hs-feature/SKILL.md`
- **What happened:** Of the three adopted seeds' `ROADMAP.md` entries, one `**Seed:**` line carried `· *status: unshaped*` and two carried no marker. The skill says to update the marker "where that entry's Seed line carries" one, which reads as leaving the other two unmarked, so the index would show one of three seeds adopted. The marker was added to all three, on the brief's "update their ROADMAP markers".
- **Skill cause:** The rule is conditional on a marker the `/hs-roadmap` convention does not always write, so adoption is visible in the index only for some seeds.
- **Recommended fix:** Say "set the entry's `**Seed:**` line marker to `· *status: adopted:{slug}*`, adding it when absent", and have `/hs-roadmap` write the marker on every new Seed line.
- **Confidence:** high · **Effort:** small

## F5 — No factory helper bounds a verify step, and macOS has no `timeout` · seen again
`origin=hs-plan:step6 severity=low category=tooling status=open target=.claude/skills/hs-plan/SKILL.md`
- **What happened:** As `spec/config-set-and-unread/META.md` F4: this plan copies that cycle's `drive/bound.sh`; all five research briefs found no `timeout(1)` on the host and fell back to `perl -e 'alarm …'`.
- **Skill cause:** As recorded there.
- **Recommended fix:** As recorded there.
- **Confidence:** high · **Effort:** small

## F6 — The tests-first red-list tooling lives in other cycles' spec folders, unnamed by the skill · seen again
`origin=hs-plan:step6 severity=low category=missing-guidance status=open target=.claude/skills/hs-plan/SKILL.md`
- **What happened:** As `spec/config-set-and-unread/META.md` F5: P1 and P5 run `spec/relay-hardening/drive/red_check.sh` and `spec/contended-writes/drive/rest_green.sh`.
- **Skill cause:** As recorded there.
- **Recommended fix:** As recorded there.
- **Confidence:** high · **Effort:** small

## F7 — No method for dry-running a fix phase's verify before its code exists · seen again
`origin=hs-plan:step6 severity=medium category=missing-guidance status=open target=.claude/skills/hs-plan/SKILL.md`
- **What happened:** As `spec/listener-handshake-deadline/META.md` F5 (drives dry-run only on the base). Here every later-phase verify was emulated on a scratch copy of the tree with the design applied to a scratch `src` ahead of the editable install on `PYTHONPATH`. The drive's `uv run` from that scratch tree tried to build the scratch project (network refused, nothing installed), so the emulation ran the drive through `sh` instead; the docs build needed its own scratch venv for the same reason.
- **Skill cause:** Step 6 requires "a real `verify:` command" and the brief asks for every verify to be dry-run, but the skill gives no procedure for the green side of a phase that has no code yet, and its `uv run`-based examples break in a scratch tree.
- **Recommended fix:** Add the scratch-`src` emulation to Step 6 (export the tree with `git archive`, apply the design to the copy, put its `src` first on `PYTHONPATH`, run the drive through `sh` and the docs build with a scratch venv), and require both outcomes (red on base, green on the emulation) in `TECH.md`'s plan-time evidence.
- **Confidence:** high · **Effort:** small

## F8 — The whole-suite gate was dry-run without the PostgreSQL environment it requires
`origin=hs-build:P5 severity=medium category=instruction status=open target=.claude/skills/hs-plan/SKILL.md`
- **What happened:** P5's verify requires `HS_TEST_POSTGRES` and then runs the whole suite. With it set, as TECH says, `tests/test_upgrade_postgres.py` (28 cases: `HS_TEST_POSTGRES_TEMPLATES` unset, templates need Docker) and two `tests/test_timescale.py` cases (the local server has TimescaleDB while `HS_TEST_TIMESCALE` is empty) fail, on `1f688c7c` too. The plan-time evidence ran the whole suite sandboxed with no PostgreSQL, where those tests skip, so the gate was never proved runnable as written.
- **Skill cause:** `hs-plan` Step 6 asks for every verify to be dry-run but does not require the dry run to use the verify's own environment preconditions (here `HS_TEST_POSTGRES`), and TECH's "Running the verify commands here" names no setting for the suite's other PostgreSQL knobs (`HS_TEST_POSTGRES_TEMPLATES`, `HS_TEST_TIMESCALE`).
- **Recommended fix:** Require the whole-suite gate's dry run under the exact environment the verify asserts, and have TECH list every `HS_TEST_*` variable the suite reads with its value on the build host (or deselect, by name in the verify, the tests that host cannot run).
- **Confidence:** high · **Effort:** small

## F9 — Binding plan-gate rulings live in `PLAN.md`, which Step 2 forbids passing to the blind reviewer · seen again (other cycles)
`origin=hs-review:step2 severity=low category=missing-guidance status=open target=.claude/skills/hs-review/SKILL.md`
- **What happened:** S1–S5, the §11 bend for `max_retries` and the P5 gate ruling bind how R7, R8 and R9 are read. They are recorded only at the end of `PLAN.md`. The orchestrator extracted those two blocks alone and gave them to the reviewer as contract readings, as the brief directed, although Step 2 lists only `GOAL.md`, the diff, `invariants.md` and the rubric. This is the same finding as `spec/config-set-and-unread/META.md` F6 and `spec/env-config-keys/META.md` F6.
- **Skill cause:** no skill says where a gate ruling that changes a contract reading must live for the blind pass to see it.
- **Recommended fix:** require `hs-plan`'s gate to write each ruling that changes a reading into `GOAL.md` Clarifications, or sanction a verbatim "Plan-gate rulings" block as a Step 2 input. A fix that passes more of `PLAN.md` than the rulings would weaken blind-review integrity, and would be severity high.
- **Confidence:** high · **Effort:** small

## F10 — Step 2 does not say which copy of `invariants.md`/`AGENTS.md` grades a branch that edits them · seen again (other cycles)
`origin=hs-review:step2 severity=medium category=missing-guidance status=open target=.claude/skills/hs-review/SKILL.md`
- **What happened:** this diff edits §2, §3, §10 and §11 of `invariants.md` and the matching `AGENTS.md` passages. The reviewer was given the base copies only because the brief said so. This is the same finding as `spec/submit-input-checks/META.md`, `spec/relay-advertise/META.md` and `spec/zone-autoscale/META.md` F4.
- **Skill cause:** Step 2 says to give the reviewer "the full text of `invariants.md`" and does not say which copy, so the branch's own rule could become the yardstick for its code. That would weaken blind-review integrity, which is why this finding is severity medium.
- **Recommended fix:** say "the base copies (`git show {base}:…`) of `invariants.md`, `review-rubric.md` and `AGENTS.md`; the branch's edits to them are graded as diff".
- **Confidence:** high · **Effort:** small
