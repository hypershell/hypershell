# META — Submitted input is checked: `#` is part of the command, a group is an integer, and a group below the reached one is refused

> **Harness feedback log** for this feature — the producer artifact of the factory's self-improvement
> loop. Written by the lifecycle skills (`hs-feature` / `hs-plan` / `hs-build` / `hs-review`) when the
> **skillset itself** costs something; read by `hs-publish` (surfaced in the PR) and applied by
> `/hs-harness`. This file is **orthogonal** to the `GOAL → PLAN → TECH → REVIEW` spine — it is about
> the *toolchain*, not the feature — and is retained on merge like the rest of `spec/submit-input-checks/`.
>
> **Silence is the default.** The bar for a finding is one test: *was this the **skill's** fault — not
> mine, not the task's?* A merely-hard task, a self-inflicted error, or a one-off content/code issue
> (that belongs in `GOAL.md` / `REVIEW.md`) is **not** a finding. The blind `hs-review` correctness
> reviewer never reads this file — it would leak author intent.

- **slug:** submit-input-checks

## Friction findings

<!-- Real findings are appended below this line by the lifecycle skills. -->

## F1 — No guidance for promoting several seeds into one GOAL under a new slug · seen again
`origin=hs-feature:step4 severity=low category=missing-guidance status=open target=.claude/skills/hs-feature/SKILL.md`
- **What happened:** Three seeds promoted under `submit-input-checks`, a slug that is none of their stems. The seeds disagreed on `kind` (two `fix`, one `feature`) and on `status` (two `unshaped`, one `shaped`), so Argument Parsing's "frontmatter supplies `kind`" and Step 4's per-status rules gave no single answer; the invocation set `kind: fix`. One seed's ROADMAP line had no `· *status: …*` marker; one was added, as `spec/task-input-fidelity/` did. The template's single `Seed:` line became a `Seeds:` list by hand.
- **Skill cause:** Same as `spec/task-input-fidelity/META.md` F1: the promotion steps assume one seed whose stem is the slug.
- **Recommended fix:** As there; also say which seed's `kind` wins (or that the invocation must set it) and how a `shaped` seed's resolved clarifications are carried beside an `unshaped` seed's open ones.
- **Confidence:** high · **Effort:** small

## F2 — Step 1/3 assume local `develop` in the main checkout; cycles run in worktrees · seen again
`origin=hs-feature:step3 severity=low category=instruction status=open target=.claude/skills/hs-feature/SKILL.md`
- **What happened:** The foreman created the worktree and `fix/submit-input-checks` off `origin/develop` before this step; Step 1's "on `develop`" check and Step 3's `git switch -c` did not apply and were skipped on the invocation's instruction.
- **Skill cause:** Same as `spec/cluster-launch-failures/META.md` F2: the steps predate the worktree practice.
- **Recommended fix:** As there.
- **Confidence:** high · **Effort:** small

## F3 — No shared red-on-base harness; this plan reuses another cycle's `drive/red_check.sh` · seen again
`origin=hs-plan:step6 severity=low category=tooling status=open target=.claude/skills/hs-plan/SKILL.md`
- **What happened:** The tests-first phase proves its red list with `spec/relay-hardening/drive/red_check.sh` and copies `bounded.py` from `spec/relay-shutdown-tail/drive/` into this cycle's `drive/`; the PostgreSQL lines rely on that script's `pg:` prefix, found by reading it.
- **Skill cause:** Same as the earlier F4 findings of this name: Step 6 names neither tool, so each plan finds, copies or cross-references another cycle's spec folder.
- **Recommended fix:** As there: move `red_check.sh`, `red_outcome.py` and `bounded.py` into `.agents/factory/bin/` and name them in Step 6 and the TECH template.
- **Confidence:** high · **Effort:** small

## F4 — `verify:` guidance has no guard against a command that never returns · seen again
`origin=hs-plan:step6 severity=low category=missing-guidance status=open target=.claude/skills/hs-plan/SKILL.md`
- **What happened:** The base build hangs on two of this cycle's inputs (`group:nan` on SQLite, `group:x` on PostgreSQL), so every verify and every new test needed an explicit bound; the skill says nothing about bounding or dry-running verifies, which the invocation supplied.
- **Skill cause:** Same as the earlier findings of this name.
- **Recommended fix:** As there: Step 6 requires every `verify:` to run under a wall-clock bound and to be dry-run on the base before the commit, with the bound helper named.
- **Confidence:** high · **Effort:** small

**What worked well:** Step 3's numbered research paths let five briefs run in parallel with no write conflicts, and the digest step settled the briefs' disagreements in one place.

## F5 — Step 2 hands over "the full text of `invariants.md`" without saying which copy, when the branch edits it · seen again (other cycles)
`origin=hs-review:step-2 severity=medium category=missing-guidance status=open target=.agents/skills/hs-review/SKILL.md`
- **What happened:** this diff edits §3 of `.agents/factory/invariants.md` and `AGENTS.md`, naming its own `ensure_group`, `reached_group` and gate split. The reviewer was given the base copies only because the brief said so.
- **Skill cause:** Same as `spec/relay-topology/META.md` F6 and `spec/zone-autoscale/META.md` F4. The step is silent on the copy, so the branch's own rule could become the yardstick for its code. That would weaken blind-review integrity, which is why this finding is severity medium.
- **Recommended fix:** As there: Step 2 passes `git show {base}:.agents/factory/invariants.md` and `git show {base}:AGENTS.md`, and grades the branch's edits to them as diff.
- **Confidence:** high · **Effort:** small

## F6 — Binding plan-gate rulings live in `PLAN.md`, which Step 2 forbids passing to the blind reviewer · seen again (other cycles)
`origin=hs-review:step-2 severity=medium category=missing-guidance status=open target=.agents/skills/hs-review/SKILL.md`
- **What happened:** plan-gate rulings D1–D9 change how R5, R7, R3 and R4 read: the all-refused stream hang, old files refused on `--restart`, 32-bit groups, `--template`, `-t` in queue mode and JSON numbering from 0. They are recorded only at the end of `PLAN.md`. The orchestrator restated them in the reviewer's prompt as the brief directed, although Step 2 lists only `GOAL.md`, the diff, the invariants and the rubric.
- **Skill cause:** Same as `spec/env-config-keys/META.md` F6 and `spec/cluster-launch-followups/META.md` F6. No skill says where a gate ruling that changes a contract reading must live for the blind pass to see it.
- **Recommended fix:** As there: the plan gate appends contract-reading rulings to `GOAL.md`'s ruled block (or a `RULINGS.md` the reviewer may read), and Step 2 names that block as an input.
- **Confidence:** high · **Effort:** small
