# META — Serve the managed queues in-process on a threading primitive (zone, cycle 1)

> **Harness feedback log** for this feature — the producer artifact of the factory's self-improvement
> loop. Written by the lifecycle skills (`hs-feature` / `hs-plan` / `hs-build` / `hs-review`) when the
> **skillset itself** costs something; read by `hs-publish` (surfaced in the PR) and applied by
> `/hs-harness`. This file is **orthogonal** to the `GOAL → PLAN → TECH → REVIEW` spine — it is about
> the *toolchain*, not the feature — and is retained on merge like the rest of `spec/zone-queue-primitive/`.
>
> **Silence is the default.** The bar for a finding is one test: *was this the **skill's** fault — not
> mine, not the task's?* A merely-hard task, a self-inflicted error, or a one-off content/code issue
> (that belongs in `GOAL.md` / `REVIEW.md`) is **not** a finding. The blind `hs-review` correctness
> reviewer never reads this file — it would leak author intent.

- **slug:** zone-queue-primitive

## What worked well

- `hs-feature` Step 4, the `shaped` promotion rule ("re-confirm against current `develop`, cite
  drift"): it gave the verified correction to the seed's Problem 2 a clear home without reopening
  the seed's settled clarifications.
- `hs-review` Step 2, the blind brief with its executed-evidence demand: base-versus-feature probes
  surfaced two same-process regressions (REVIEW C1, C2) that no phase gate exercised.
- `hs-review` Step 2 again, cycle 3: a fresh blind pass with executed evidence found a deadlock
  (REVIEW C5) that two earlier cycles and every phase gate had missed. The orchestrator's own suite
  run, which stalled the same way, corroborated it independently.
- `hs-review` Step 2, cycle 4: a blind pass scoped to the remediation delta found two defects the
  remediation's own red-first tests did not cover (REVIEW C7, C8). Its byte-level trace showed that a
  remote client's decref is unchanged.
- `hs-review` Step 2, cycle 6: the blind reviewer and the orchestrator reached the same defect
  (REVIEW C10) by different executed methods, a forced collection at a new thread's first
  instruction and natural collections, and graded its exposure the same way independently.

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

## F1 — Step 3 branches from local `develop`, which may be stale · seen again
`origin=hs-feature:step3 severity=medium category=instruction status=open target=.agents/skills/hs-feature/SKILL.md`
- **What happened:** A recurrence of `spec/group-advance-retry-gate/META.md` F1. The cycle was
  opened in a detached worktree at `origin/develop` and branched with
  `git switch --no-track -c feature/zone-queue-primitive origin/develop`, the known workaround,
  instead of Step 3's `git switch -c {branch} develop`.
- **Skill cause:** Unchanged from the original finding: Step 3 branches from the local ref, and
  Step 1's "on `develop`" check cannot pass in a worktree.
- **Recommended fix:** As in the original finding.
- **Confidence:** high · **Effort:** small

## F2 — No way to promote part of a shaped seed and keep the rest alive as `shaped`
`origin=hs-feature:step4 severity=medium category=missing-guidance status=open target=.agents/skills/hs-feature/SKILL.md`
- **What happened:** The `zone` seed is delivered in three cycles, and this one carries only its
  R1. Step 4 offers only whole-seed promotion (`status: adopted:{slug}`). If the seed is marked
  that way, `/hs-roadmap` treats it as shipped once this cycle lands: its Step 4 either deletes the
  seed or rewrites it down to the remainder and resets it to `unshaped`. Either outcome throws away
  the shaping already agreed for R2–R17. The seed was therefore left `status: shaped`, with a
  carve note in its header, an annotation on R1, and a matching ROADMAP refs line. Nothing in the
  harness reads those markers.
- **Skill cause:** Step 4's status vocabulary and the size circuit-breaker's "pilot + follow-ups"
  split cover only the pilot. The follow-ups go into the GOAL's Non-goals, and `/hs-roadmap`
  re-shapes them as `unshaped` work. Nothing covers a seed already shaped as a multi-cycle program.
  This is a sibling of the cross-seed carve found in `spec/schema-upgrade-step/META.md` F2, where
  one seed's R1 was discharged by a different cycle.
- **Recommended fix:** Add a partial-promotion rule to Step 4. The seed keeps its status; it gains
  a machine-readable `carved: {R-IDs} -> {slug}` frontmatter list (or per-R-ID annotations); and
  the receiving GOAL names the R-IDs it carries. Teach `/hs-roadmap` to retire only the carved
  R-IDs when that cycle lands, and to keep a `shaped` seed `shaped`. Fold in `schema-upgrade-step`
  F2 when this is fixed.
- **Confidence:** high · **Effort:** small

## F3 — No convention for a phase whose evidence is produced outside the repo · seen again
`origin=hs-plan:step6 severity=medium category=missing-guidance status=open target=.agents/factory/templates/TECH.md`
- **What happened:** R9's multi-node clause can only be met by a run outside the repo. The work
  splits into a "run A" phase, which builds the drive and hands it back, and a "run B" phase, which
  collects the evidence. The FSM has no status or field for "waiting on external evidence", so a
  convention was written into this `TECH.md` by hand: `blocked` with a fixed `blocked_reason`, not
  counted against `attempts`, evidence committed under `spec/{slug}/evidence/`.
- **Skill cause:** Step 6 and the TECH template assume that every phase's `verify:` can pass inside
  one `hs-build` invocation. Neither says how to model an external wait, or whether `hs-review` may
  run while such a phase is blocked.
- **Recommended fix:** Add an "external wait" phase pattern to the TECH template and to `hs-build`:
  a phase flag (e.g. `external: true`), the evidence location convention, the `blocked` semantics,
  and whether review and publish may proceed past it.
- **Confidence:** high · **Effort:** small
- **Seen again (hs-review step1 + step4, 2026-11-02):** Step 1 says to ask before reviewing when
  `status` is not `in_review`/`done` (here `in_progress`, P7 pending), and Step 4 knows only
  approved, changes-requested and PLAUSIBLE triage. Neither covers a GOAL clause whose evidence is
  pending outside the repo. The review recorded R9's multi-node clause as a PLAUSIBLE finding
  ("pending external evidence") on the operator's instruction. Fix on the review side: a
  `pending-external` status in the REVIEW template's matrix that neither approves nor fails the
  clause, and a Step 1 rule that proceeds past a phase carrying the external-wait flag.

## F4 — Research prototypes live in `$TMPDIR` and vanish before the build · seen again · seen again
`origin=hs-plan:step3 severity=low category=missing-guidance status=open target=.agents/skills/hs-plan/SKILL.md`
- **What happened:** The research subagents built working prototypes in `$TMPDIR`: a serve loop, a
  benchmark harness, and a drive with a compare script. The briefs pointed at them. Those paths are
  ephemeral and machine-local, so they were copied by hand into `research/prototypes/` to keep them
  usable by `/hs-build`.
- **Skill cause:** Step 3 says research is read-only and writes only `research/NN-topic.md`. It
  says nothing about runnable prototypes, which a big-appetite plan often needs to keep.
- **Recommended fix:** In Step 3, allow `research/prototypes/` for runnable research code, with
  these rules: not collected by pytest, no internal identifiers, kept lean. Tell the briefs to save
  prototypes there, not in `$TMPDIR`.
- **Confidence:** med · **Effort:** small
- **Seen again (hs-build:P1-remediation-4, 2026-11-02):** the same loss, from `hs-review`. The
  cycle-4 triage rules "red-first: the reviewer's reproduction", and the brief pointed at the
  reviewer's probes in `$TMPDIR`; they were gone when the build started. The red-first tests were
  rebuilt from REVIEW.md's prose, which was detailed enough this time. `hs-review` Step 2 could keep
  a CONFIRMED finding's reproduction in the spec (e.g. `spec/{slug}/review/`), under the same rules.
- **Seen again (hs-review step3, cycle 5, 2026-11-02):** the brief asked to re-run cycle 4's
  reproductions, and the cycle-4 reviewer's probes were gone, so the orchestrator rebuilt them from
  REVIEW.md's prose. This cycle's probes, including C9's, live in `$TMPDIR` again, because Step 3
  lets the orchestrator write only `REVIEW.md`, `TECH.md` and `META.md`.

## F5 — A `verify:` that builds `HEAD` measures the previous commit, since hs-build verifies before it commits · seen again · seen again · seen again · seen again · seen again
`origin=hs-build:P5 severity=medium category=missing-guidance status=open target=.agents/skills/hs-build/SKILL.md`
- **What happened:** P4's and P5's gates (`bench_queue.py … --feature HEAD`) build a venv from the
  committed `HEAD`. hs-build runs the gate in Step 4 and commits in Step 7, so the gate measured the
  previous phase's commit. It was harmless here only because P4/P5 changed no `src/` code.
- **Skill cause:** Step 4 says "run the phase's `verify:`" without noting that a ref-based gate
  sees committed code only; neither hs-build nor the TECH template says how to verify a phase whose
  gate builds from git.
- **Recommended fix:** In Step 4, say that a gate building from a git ref must either run after a
  provisional commit (amended on red) or name the working tree; or have the harness accept
  `--feature WORKTREE`. Mirror the rule in the TECH template's `verify:` guidance.
- **Confidence:** high · **Effort:** small
- **Seen again (hs-build P1 review-5 remediation, 2026-11-02):** parity, compat and the throughput
  gate ran against the signed phase commit, which was then amended to record their results.

## F6 — hs-review grades `{base}...HEAD` with `{base}` a branch name, which a stale local ref breaks · seen again · seen again · seen again
`origin=hs-review:step1 severity=medium category=instruction status=open target=.agents/skills/hs-review/SKILL.md`
- **What happened:** A recurrence of `spec/group-advance-retry-gate/META.md` F3. `TECH.md` records
  `base: develop`; the local `develop` has merge base `aee41ae` with this branch, not the fork point
  `b819de6`. The skill's literal `develop...HEAD` (spec excluded) lists 56 files instead of 15. The
  review graded `origin/develop...HEAD` and ignored the injected diffstat.
- **Skill cause:** Unchanged from the original finding: Step 1 never resolves `{base}` to a commit.
- **Recommended fix:** As in the original finding.
- **Confidence:** high · **Effort:** small
- **Seen again (hs-review step1, cycle 2, 2026-11-02):** the injected diffstat again ran against
  the stale local `develop` (`222075b`). `origin/develop` has since moved on to `8cdf62a`, so even
  `origin/develop...HEAD` is no longer a usable literal; only the merge base `b819de6` grades the
  branch's own diff. The stale local `develop` must not be used.
- **Seen again (hs-review step1, cycle 3, 2026-11-02):** the foreman's brief had to pin the base to
  `b819de6` again; Step 1 still resolves `{base}` from the branch name.
- **Seen again (hs-review step1, cycle 4, 2026-11-02):** the brief pinned the base to `b819de6` and
  the delta to `d3e00f9..4904c97`; `TECH.md` still says `base: develop`, and local `develop` is stale.
- **Seen again (hs-review step1, cycle 5, 2026-11-02):** the brief again pinned the base to
  `b819de6`, this time with the delta `aa55e86..43d2d85`.

## F7 — Remediation mode has no place to record the human-gate triage, nor a path for an accepted PLAUSIBLE finding
`origin=hs-build:P1-remediation severity=low category=missing-guidance status=open target=.agents/skills/hs-build/SKILL.md`
- **What happened:** Review cycle 1 forced a human gate (C1, C2) and left three PLAUSIBLE findings
  for triage; one (P1) was accepted as a fix. Step 1.3 says to read `REVIEW.md` and reopen phases,
  but neither it, `hs-review` nor `templates/REVIEW.md` defines a triage section, who records the
  gate's passage, or how an accepted PLAUSIBLE joins the remediation. The build invented
  `## Triage (human gate) — cycle 1` in `REVIEW.md`. F5 recurred too: the P5 gate re-run needed a
  provisional commit object to build the uncommitted fix.
- **Skill cause:** Step 1.3 assumes every remediation item is a CONFIRMED finding and is silent on
  the human gate it follows.
- **Recommended fix:** Add a `## Triage` block to `templates/REVIEW.md` (finding, ruling, by whom,
  date) and tell Step 1.3 to require it filled before reopening phases when the gate was triggered,
  treating accepted PLAUSIBLE rows like CONFIRMED ones.
- **Confidence:** high · **Effort:** small

## F8 — Reviewer conduct covers the git tree, not the processes a base-build drive leaves behind · seen again
`origin=hs-review:step2 severity=medium category=missing-guidance status=open target=.agents/factory/review-rubric.md`
- **What happened:** Some of the blind reviewer's base-versus-feature drives timed out. It killed
  the base servers by process id, and their manager children and resource trackers survived as
  orphans (eight processes, three still listening on loopback). The reviewer's request to signal
  them was refused, so they were left for the human.
- **Skill cause:** The rubric's conduct rule asks only for a clean `git status --porcelain`.
  Neither Step 2's brief nor the rubric mentions processes. Nothing warns that a base build's
  server starts a manager child that outlives a parent killed by process id. `temp_site.sh` cleans
  the site, not the process tree.
- **Recommended fix:** Add to the rubric's Reviewer conduct, and to Step 2's brief: start every
  drive in its own session or process group, and on timeout kill the group, never a single process
  id. At hand-back, list any process still running from the scratch environments and report it.
  Name the base manager child as the known case.
- **Confidence:** high · **Effort:** small
- **Seen again (hs-review step2, cycle 3, 2026-11-02):** the process-group rules still had to be
  written into the reviewer brief by hand. With them, no drive was orphaned, but a background wait
  loop outlived the reviewer's hand-back until the orchestrator asked for it to be stopped. The
  hand-back check should cover background shells and monitors, not only drives; the orchestrator's
  own monitors needed the same check.
- **Seen again (hs-review step2, cycle 6, 2026-11-03):** the process-group, timeout and psutil-scan
  rules came from the foreman's brief and were again copied into the blind brief by hand; the rubric
  still has none of them.

## F9 — Cycle 2+ has no rule for a blind finding that re-raises an already-triaged one
`origin=hs-review:step3 severity=low category=missing-guidance status=open target=.agents/skills/hs-review/SKILL.md`
- **What happened:** The cycle-2 blind pass was kept blind to cycle 1's triage, as it should be. It
  found the root cause of cycle 1's P3 again, which triage had rejected as a reading of R9, and
  confirmed a new failure scenario for it (REVIEW C3). Steps 3 and 4 route every CONFIRMED finding
  to changes-requested. They don't say whether an earlier human ruling covers a re-found finding.
  The orchestrator graded it CONFIRMED, cross-referenced P3, and left the overlap to the human.
- **Skill cause:** Step 3's cycle 2+ rule covers appending and the blind-or-remediation mode, but
  not reconciling against earlier triage rulings. F7 notes that those rulings have no template
  home either.
- **Recommended fix:** In Step 3, for cycle ≥ 2, have the orchestrator, not the reviewer, compare
  each finding with the earlier `Triage` rows. If a ruling already covers the finding's scenario,
  record it as "covered by ruling {id}"; it does not block. A finding that adds a new failure
  scenario is graded normally and cross-referenced to the ruling.
- **Confidence:** med · **Effort:** small

## F10 — The verify gates require `temp_site.sh`, which a harness that blocks `rm` cannot run · seen again · seen again · seen again · seen again · seen again · seen again
`origin=hs-build:P1-remediation-2 severity=medium category=tooling status=open target=.agents/factory/bin/temp_site.sh`
- **What happened:** P1's `verify:` and Safety Principles' CLI drive both run
  `.agents/factory/bin/temp_site.sh`, whose cleanup is `trap 'rm -rf "$site"'`. Under a harness
  that blocks `rm` (this build's brief forbade the script for that reason), the gate as written
  cannot run. The build drove `hsx` by hand with `HYPERSHELL_SITE`, `HYPERSHELL_DATABASE_FILE` and
  `HYPERSHELL_CONFIG_FILE=''` under `$TMPDIR` and left the sites there.
- **Skill cause:** The non-negotiable CLI-drive gate depends on a destructive cleanup the skill
  never offers an alternative to; the frontmatter `verify:` hard-codes it.
- **Recommended fix:** Give `temp_site.sh` a keep-site mode (e.g. `TEMP_SITE_KEEP=1`, print the
  path, no `rm`), or move the cleanup into a Python helper that a sandbox permits, and say in
  hs-build Step 4 which form to use when `rm` is unavailable. The gate itself stays as strong.
- **Confidence:** high · **Effort:** small
- **Seen again (hs-review step2, cycle 3, 2026-11-02):** the rubric's Reviewer conduct, which Step 2
  hands to the blind reviewer verbatim, tells it to drive the CLI through `temp_site.sh`. The brief
  had to override that with environment-isolated sites.
- **Seen again (hs-review step2, cycle 4, 2026-11-02):** the blind brief again had to override the
  rubric's `temp_site.sh` instruction with `HYPERSHELL_SITE`/`HYPERSHELL_DATABASE_FILE`/`HYPERSHELL_CONFIG_FILE=''`.
- **Seen again (hs-review step2, cycle 5, 2026-11-02):** the same override, in the blind brief again.
- **Seen again (hs-build P1 review-5 remediation, 2026-11-02):** the `hsx` drive again ran in
  environment-isolated sites instead of `temp_site.sh`.
- **Seen again (hs-review step2, cycle 6, 2026-11-03):** the blind brief again overrode the rubric's
  `temp_site.sh` instruction with environment-isolated sites.

## F11 — Step 7's `git add -A` commits whatever else is in a shared worktree · seen again
`origin=hs-build:P1-remediation-3 severity=medium category=instruction status=open target=.agents/skills/hs-build/SKILL.md`
- **What happened:** During this remediation another agent's docs build wrote into this worktree
  (`share/man/man1/*.1`). Step 7's `git add -A` would have folded those edits into the phase
  commit; the foreman had to forbid it and require staging by explicit path, with a
  `git diff --cached --stat` check before committing.
- **Skill cause:** Step 7 assumes the tree holds only this phase's work, and Step 1's clean-tree
  check runs only at the start, so changes that appear mid-build are never noticed.
- **Recommended fix:** In Step 7, stage the phase's files by path (the TECH "Touches" list plus the
  spec files), then check `git diff --cached --stat` against that list and STOP on anything else;
  re-run `git status --porcelain` before committing and report unexpected paths.
- **Confidence:** high · **Effort:** small
- **Seen again (hs-build P1 review-5 remediation, 2026-11-02):** the foreman's brief again required
  explicit-path staging in place of Step 7's `git add -A`.

## F12 — The rubric does not say how to classify a reproduction whose trigger is simulated configuration rather than an injected fault
`origin=hs-review:step3 severity=medium category=missing-guidance status=open target=.agents/factory/review-rubric.md`
- **What happened:** The finding (REVIEW C9) needs a host name with two A records. Neither this
  review nor the blind reviewer could add one to the host's resolver, so both supplied the name's
  answer in-process and ran the code under test unmodified. They reproduced the stall, one run
  with natural collections. Cycle 4 had classed P4, an injected connection failure,
  PLAUSIBLE. Which side of that line C9 falls on decides whether it auto-loops, and the
  orchestrator had to argue it in the finding.
- **Skill cause:** The refutation protocol defines CONFIRMED as "reproduced with observed wrong
  behaviour". It is silent on what may be simulated: the code under test, the environment around
  it, or neither.
- **Recommended fix:** In the rubric's refutation protocol, add one rule. A reproduction that runs
  the code under test unmodified, with only the environment simulated (resolver answers, clock,
  peer behaviour), counts as CONFIRMED when the simulated environment is a valid configuration;
  the simulation must be named in the evidence. A reproduction that injects a fault into the code
  path stays PLAUSIBLE unless the fault occurs naturally.
- **Confidence:** med · **Effort:** small

## F13 — Remediation mode has no path for a triage ruling whose own acceptance criteria fail
`origin=hs-build:P1-remediation-5 severity=medium category=missing-guidance status=open target=.agents/skills/hs-build/SKILL.md`
- **What happened:** The cycle-5 ruling prescribed a mechanism (the thread rule) and acceptance
  criteria (the existing C5 tests pass). Built exactly as ruled, the C5 test
  `test_dropped_client_never_stalls_server[tls]` stalled in 2 of 14 runs: the accept loop's inline
  TLS handshake waits on a client thread that the ruled rule leaves off the server side. The build
  had to choose between stopping and extending the ruled rule by one case. It extended the rule,
  flagged the extension in TECH and in the commit, and left ratification to the judge.
- **Skill cause:** Step 3 lets the build amend `TECH.md` freely and stops only on a `GOAL.md`
  contradiction. It says nothing about a triage ruling, which sits between the two: binding like a
  contract on the mechanism, but falsifiable by its own tests.
- **Recommended fix:** In Step 3 remediation mode, add a rule. When a ruled fix fails the ruling's
  own acceptance criteria, implement the smallest extension that meets them, without changing wire,
  auth or the remote-client contract. Mark the extension in `TECH.md` and in the commit body as
  "beyond the ruling", with the red evidence of the ruling as written. Report it first in the final
  report for the judge to ratify or reject. Any extension that would touch those surfaces is a STOP.
- **Confidence:** med · **Effort:** small

## F14 — Step 4 has no route for a maintainer's pre-agreed fallback on a cycle's findings
`origin=hs-review:step4 severity=low category=missing-guidance status=open target=.agents/skills/hs-review/SKILL.md`
- **What happened:** The cycle-5 triage authorized cycle 6 with a pre-agreed fallback: LOW or MEDIUM
  findings with library-only exposure are seeded and the feature proceeds to publish; anything else
  stops it. Step 4 routes every CONFIRMED finding to `changes-requested` and `blocked`, and its only
  other route, the deferral exception, needs pre-existing behaviour, which a finding new in the delta
  is not. The orchestrator recorded the mechanical verdict and left the fallback to the judge, with
  the classification argued in REVIEW.md.
- **Skill cause:** Step 4 knows two dispositions, block or defer pre-existing behaviour. A human
  ruling that pre-commits the disposition of a class of findings has no home, so the recorded verdict
  and the agreed outcome can disagree.
- **Recommended fix:** In Step 4, when the latest `Triage` section states a pre-agreed fallback,
  have the orchestrator classify each CONFIRMED finding against it (severity, CLI-reachable or
  library-only, with reasons). It still records `changes-requested`, and names in REVIEW.md's
  Human-gate section whether the fallback's conditions are met, so the judge applies it in one step
  (seed, `ROADMAP.md` entry, verdict flip) rather than re-deriving it.
- **Confidence:** med · **Effort:** small
