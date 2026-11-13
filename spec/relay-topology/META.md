# META — Relay site-topology autoscaling: fill and spread across sites

> **Harness feedback log** for this feature — the producer artifact of the factory's self-improvement
> loop. Written by the lifecycle skills (`hs-feature` / `hs-plan` / `hs-build` / `hs-review`) when the
> **skillset itself** costs something; read by `hs-publish` (surfaced in the PR) and applied by
> `/hs-harness`. This file is **orthogonal** to the `GOAL → PLAN → TECH → REVIEW` spine — it is about
> the *toolchain*, not the feature — and is retained on merge like the rest of `spec/relay-topology/`.
>
> **Silence is the default.** The bar for a finding is one test: *was this the **skill's** fault — not
> mine, not the task's?* A merely-hard task, a self-inflicted error, or a one-off content/code issue
> (that belongs in `GOAL.md` / `REVIEW.md`) is **not** a finding. The blind `hs-review` correctness
> reviewer never reads this file — it would leak author intent.

- **slug:** relay-topology

## What worked well

Brief, optional reinforcement: a part of a skill / the harness that materially helped, so `/hs-harness`
knows what **not** to change. One line each, naming the skill/step. Skip the section entirely if
nothing stands out.

- `hs-feature` Step 4, "Promoting an issue" (`shaped`): re-confirm, cite drift, adopt largely as written kept the promotion to the two amended R-IDs and the open questions, without reopening the seed's settled clarifications.
- `hs-review` Step 2's `spec/` exclusion with the base `invariants.md`: the blind reviewer still graded the branch's own `AGENTS.md`/`invariants.md` edits as claims, which is how RV3 surfaced.

## Friction findings

Zero or more findings, appended below — each a markdown **section** so appending is a low-corruption
operation and a stdlib parser reads them (`uv run python .agents/factory/bin/meta_status.py
spec/relay-topology/META.md`). Skills always write `status=open`; only `/hs-harness` flips it. `target` is a
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

## F1 — Pre-flight requires branch `develop`; factory cycles open in a worktree detached at `origin/develop` · seen again
`origin=hs-feature:step-1 severity=low category=instruction status=open target=.agents/skills/hs-feature/SKILL.md`
- **What happened:** the cycle opened in a worktree detached at `origin/develop` (`22684fd`, equal to local `develop` this time). Step 1's "if the injected branch is not `develop`, STOP" cannot pass there, and Step 3's `git switch -c {branch} develop` names a ref the worktree is not on; the branch was created from `HEAD` on the foreman's instruction. Recurs from `spec/schema-upgrade-step/META.md` and `spec/zone-autoscale/META.md` F1.
- **Skill cause:** Steps 1 and 3 assume a checkout of `develop`, while parallel cycles run in detached worktrees.
- **Recommended fix:** in Step 1, accept a detached `HEAD` equal to `origin/develop` after the fetch; in Step 3, branch with `git switch --no-track -c {branch} origin/develop` (or `HEAD` when equal).
- **Confidence:** high · **Effort:** small

## F2 — Step 6 gives a phase one `verify:`, with no form for a split gate (run A built here, run B judged after an external run) · seen again
`origin=hs-plan:step-6 severity=low category=template status=open target=.agents/factory/templates/TECH.md`
- **What happened:** the brief required the multi-node phase's gate to be split: run A builds and rehearses the batch script, and run B judges the foreman's records. The frontmatter holds one `verify:` per phase, so P5 carries run B's command, and run A's gate and the `crest`/`in_progress` wait had to be written as prose in the phase body.
- **Skill cause:** Step 6 and the TECH template assume a phase's verify can run when the build finishes. They have no field or convention for an external-wait gate. The same gap was met at build time in `spec/relay/META.md` F6.
- **Recommended fix:** add an optional phase field (for example `verify_build:` beside `verify:`, or `external_wait: true`) that `next_phase.py`/`set_phase.py` understand. Document in Step 6 that a phase gated on an external run sets it.
- **Confidence:** med · **Effort:** small

## F3 — No rule says who regenerates the man pages: the plan asked the build to, the release assumes the branch did, and neither happens
`origin=hs-build:P2 severity=low category=missing-guidance status=open target=.agents/skills/hs-release/SKILL.md`
- **What happened:** TECH's conventions (from `hs-plan`) asked each option's phase to update `share/man/man1/hs.1` in the same commit. That file is generated by `sphinx-build -b man` from `docs/manual.rst`, and no cycle edits it: Z3 (`22684fd`) changed `docs/_include` and left the man pages alone, and the 2.9.0a7 release commit rebuilt 100 lines of content. The build deferred the man page to the release rather than hand-edit a generated file, checked that `-b man` builds clean, and amended TECH.
- **Skill cause:** `hs-release` Step 4.4 expects the release diff of `share/man/man1` to show only the `.TH` line ("CLI content is already current on the branch"), but no lifecycle skill tells a cycle to regenerate the pages, and `hs-plan` carries the "man page in the same commit" phrase into TECH with no command for it.
- **Recommended fix:** choose one owner. Either have `hs-release` Step 4.4 expect content changes (the current practice), and have `hs-plan` drop the man page from TECH's same-commit list; or give `hs-build` the exact regeneration commands (`sphinx-build -b man` and the three copies) and the rule that the `.TH` line is left as it is.
- **Confidence:** med · **Effort:** small

## F4 — The docs gate's `uv run --python 3.12` recreates the shared `.venv`, flipping the interpreter under every later `uv run` · seen again (review cycle 1 remediation: the retuned P4 verify still carries it; run with its own `UV_PROJECT_ENVIRONMENT`) · seen again (review cycle 2 remediation, same workaround)
`origin=hs-build:P3 severity=low category=tooling status=open target=.agents/factory/templates/TECH.md`
- **What happened:** the docs gate (`uv run --python 3.12 --group docs sphinx-build …`, the P4 verify's last step, also used to check P3's docs) removed and recreated the worktree's `.venv` under CPython 3.12 while the project venv was 3.15. A pytest run sharing that venv then failed `test_main::test_version` (its `hs --version` subprocess reported 3.12), and every later `uv run` in the worktree stays on 3.12 until someone runs `uv sync --python <default>`.
- **Skill cause:** the verify template pins the docs build to another interpreter in the same project environment, with no note that it replaces `.venv`; a phase that runs the docs gate and then its tests (or the next phase's) silently changes the interpreter under test.
- **Recommended fix:** give the docs gate its own environment (`UV_PROJECT_ENVIRONMENT=.venv-docs uv run --python 3.12 --group docs …`), or say in the template that it must run last and be followed by `uv sync` before any other `uv run`.
- **Confidence:** high · **Effort:** small

## F5 — The review's base resolves to the stale local `develop` · seen again (other cycles) · seen again (review cycle 2)
`origin=hs-review:step-1 severity=medium category=instruction status=open target=.agents/skills/hs-review/SKILL.md`
- **What happened:** the injected diffstat and Step 1's default `base` use the local `develop`, which is stale here; the cycle's merge base is `origin/develop` `22684fd`. The graded diff, the drift check and the reviewer's brief used `origin/develop` only because the foreman named it. Same finding open in `spec/zone-autoscale/META.md` F6 and `spec/group-advance-strands-lower-group/META.md` F9.
- **Skill cause:** "Current state" runs `git diff --stat develop...HEAD`, and Step 1 takes `base` as a branch name with no fetch and no ancestry check.
- **Recommended fix:** resolve `{base}` to `git merge-base HEAD origin/{base}` after a fetch, and use that SHA in the diffstat, the drift check and the reviewer's diff command.
- **Confidence:** high · **Effort:** small

## F6 — Step 2 hands over "the full text of `invariants.md`" without saying which copy, when the branch edits it · seen again (other cycles) · seen again (review cycle 2: the remediation edits §11 again)
`origin=hs-review:step-2 severity=medium category=missing-guidance status=open target=.agents/skills/hs-review/SKILL.md`
- **What happened:** this branch edits `.agents/factory/invariants.md` and `AGENTS.md` (§11, naming its own `SiteScaler` and publisher design). The base copy went to the reviewer only because the foreman said so; the branch's edits were graded as diff, and one was false (RV3). Same finding open in `spec/zone-autoscale/META.md` F4.
- **Skill cause:** Step 2 and the rubric treat `invariants.md` and `AGENTS.md` as fixed inputs, while §12 makes a cycle edit them in lockstep, so the branch copy is both a graded claim and author intent.
- **Recommended fix:** in Step 2, hand over `git show {base}:.agents/factory/invariants.md` and the base `AGENTS.md`, and tell the reviewer that branch edits to either are part of the diff.
- **Confidence:** high · **Effort:** small

## F7 — Step 1's drift check flags the GOAL and plan gates' dated rulings as mid-build drift · seen again (other cycles)
`origin=hs-review:step-1 severity=low category=instruction status=open target=.agents/skills/hs-review/SKILL.md`
- **What happened:** `git log origin/develop..HEAD -- spec/relay-topology/GOAL.md` listed the shaping commit plus `d0c8fd7` (GOAL-gate rulings) and `f01dce0` (plan-gate ruling), both before the first build commit `d170337`. The skill says to confirm with the human before grading; the orchestrator, a subagent with no way to ask, recorded it in `REVIEW.md` and graded the amended contract. Same finding open in `spec/zone-autoscale/META.md` F5.
- **Skill cause:** the drift line is drawn at the shaping commit, not the first build commit, so every cycle whose gates record rulings trips it.
- **Recommended fix:** surface only `GOAL.md` commits after the first `hs-build` commit; list earlier gate commits in `REVIEW.md`'s pre-flight without stopping.
- **Confidence:** high · **Effort:** small

## F8 — Remediation mode gives no order for proving a fix red on the reviewed head, though drives run the worktree's own source · seen again (review cycle 2 remediation: the reviewed head's `relay/site.py` was swapped into the worktree for the red drive runs and the fix restored after they finished, by hand)
`origin=hs-build:remediation severity=low category=missing-guidance status=open target=.agents/skills/hs-build/SKILL.md`
- **What happened:** review cycle 1 asked for tests that go red on the reviewed head and green after. The process-level checks (drive modes, server and relay subprocesses) import the worktree's `src/`, so the red run had to come before any source edit and finish before it: a drive still running launches new relays and site clients mid-run, which would load the edited code. The ordering (tests and drive modes first, red run, then source) was set by hand.
- **Skill cause:** Step 1's remediation mode says to reopen phases and fix, with no step for demonstrating each finding red before the fix, nor a note that the worktree's source is live for every process a drive starts.
- **Recommended fix:** add to remediation mode: write the finding's tests and drive checks first, run them against the unchanged source (wait for every background drive to finish), record the red result in `TECH.md`, then edit the source.
- **Confidence:** med · **Effort:** small
