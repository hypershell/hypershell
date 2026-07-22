# Harness change log (`hs-harness`)

The cross-job ledger of every harness self-improvement **decision** — the *act* side of the factory's
self-improvement loop. `/hs-harness` appends one entry per **applied** / **rejected** (and notable
**deferred**) finding, and **reads this file before applying**: a proposed fix that reverts a recent
change, or repeats a previously-rejected one, is flagged to the human rather than silently re-applied.
This is the loop's **anti-thrash memory**. (Findings themselves live in each feature's
`spec/{slug}/META.md`; this file is the durable record of what was *done* about them.)

Entry format — one section per decision, newest at the bottom:

```markdown
## {YYYY-MM-DD} — {slug} {F#}: {one-line title}
`decision=applied|rejected|deferred commit={sha|—} target={file}`
- **Rationale:** what was changed (and why it generalizes) / why rejected (overfit, stale, would-weaken-a-gate) / why deferred.
```

Read `origin`/`severity`/`category` from the finding in `META.md`; this ledger records the *outcome*.

---

<!-- Decisions are appended below this line by /hs-harness. -->

## 2026-07-16 — harness-refinements F1: Blind review isn't blind (diff leaks spec/)
`decision=applied commit=dea6742 target=.agents/skills/hs-review/SKILL.md`
- **Rationale:** the reviewer's diff command now excludes `spec/` (`':(exclude)spec/'`) — the committed PLAN/TECH/research (and prior REVIEW.md cycles) arrived inside a bare `git diff base...HEAD`, structurally defeating blindness on every review. Strengthens a gate; generalizes to every future review.

## 2026-07-16 — harness-refinements F11: Rubric ownership + reviewer tree hygiene
`decision=applied commit=b43ccef target=.agents/factory/review-rubric.md`
- **Rationale:** the rubric is handed verbatim to the reviewer subagent, so its orchestrator-only sections are now labeled and a Reviewer-conduct section requires a clean tree on hand-back — prevents the subagent acting on verdict/loop instructions or stranding a dirty tree.

## 2026-07-16 — harness-refinements F2: Sticky `approved` verdict at publish
`decision=applied commit=fd8b9cf target=.agents/skills/hs-publish/SKILL.md`
- **Rationale:** publish now asserts `git diff {last_reviewed_commit}..HEAD -- . ':(exclude)spec/'` is empty — the field existed and nothing consumed it, so any post-approval code commit shipped as "approved". The exclude tolerates the review's own artifact commit by design. Strengthens a gate.

## 2026-07-16 — harness-refinements F15: CRLF breaks the frontmatter fence scanner
`decision=applied commit=acc53d0 target=.agents/factory/bin/_fsm.py`
- **Rationale:** `rstrip("\r\n")` at the fence comparison; verified a CRLF-converted real TECH.md now parses. Covered by the F3 test file.

## 2026-07-16 — harness-refinements F4: Circuit breakers ran on session memory
`decision=applied commit=b2173ba target=.agents/factory/bin/set_phase.py`
- **Rationale:** per-phase `attempts` (`--record-attempt`) and auto-incremented `review.cycle` make both breakers file-backed, matching methodology principle 2 (files + git are the substrate); `next_phase.py` warns at ≥3 attempts. Verified live: counters increment, warning fires, old files without the fields stay valid.

## 2026-07-16 — harness-refinements F5: Remediation hand-edited YAML
`decision=applied commit=a551ac7 target=.agents/factory/bin/set_phase.py`
- **Rationale:** `--add-phase` (id/name/satisfies/depends-on/verify/--after) routes the one remaining hand-edit through the validated canonical serializer; hs-build's remediation step now forbids hand-editing. Verified: insert position, safe defaults, duplicate-id refusal (exit 2), unknown `--after` (exit 3).

## 2026-07-16 — harness-refinements F16: No aggregate status view
`decision=applied commit=c35dd33 target=.agents/factory/bin/next_phase.py`
- **Rationale:** `--all` walks `spec/*/TECH.md` and emits one summary row each (slug/status/verdict/cycle/next), reporting per-file errors instead of dying — specs are retained on merge, so the portfolio only grows. Single-path mode unchanged.

## 2026-07-16 — harness-refinements F6: verify CLI drives hit the real database
`decision=applied commit=e78e457 target=.agents/factory/bin/temp_site.sh`
- **Rationale:** new `temp_site.sh` mirrors the pytest fixture's env isolation (HYPERSHELL_SITE + DATABASE_FILE in a mktemp dir, cleaned on exit); every template/rubric/skill example now wraps CLI drives in it. Verified live: a 6-task `hsx -N2` cluster ran hermetically and the site was removed on exit.

## 2026-07-16 — harness-refinements F3: Factory state substrate had zero tests
`decision=applied commit=538ff2d target=tests/test_factory_fsm.py`
- **Rationale:** 27 unit tests pin split/dump round-trip, validate rejections, compute_next transitions and warnings, set_phase mutations (incl. the new F4/F5 flags), the META parser, and lint every committed spec artifact in CI. Deliberate, flagged extension of the `.agents/`-only remit: toolchain tests belong in `tests/` per repo convention.

## 2026-07-16 — harness-refinements F7: GOAL lock convention-only; cycle-2 undefined
`decision=applied commit=70be8e1 target=.agents/skills/hs-review/SKILL.md`
- **Rationale:** review pre-flight now surfaces any post-shape GOAL.md commits (drift check), and cycle 2+ appends a dated REVIEW.md section instead of overwriting — codifies exactly what the dogfood improvised. Strengthens a gate.

## 2026-07-16 — harness-refinements F8: Branch+PR flow contradicted practice
`decision=applied commit=3cfcd07 target=.agents/skills/hs-harness/SKILL.md`
- **Rationale:** direct-to-develop is now the default (matching all prior `[harness]` commits); `pr` opts into the branch+PR flow; pre-merge runs are declared preview-only because status flips are impossible while META.md lives only on the feature branch.

## 2026-07-16 — harness-refinements F9: FSM had no terminal transition
`decision=applied commit=be03e00 target=.agents/skills/hs-publish/SKILL.md`
- **Rationale:** publish stamps `--top-status done` right after human confirmation (a spec-only commit the staleness gate ignores), so retained records stop reading `in_review` forever.

## 2026-07-16 — harness-refinements F10: Injection mislabeled; allowlist gaps
`decision=applied commit=630340b target=.agents/skills/hs-feature/SKILL.md`
- **Rationale:** the "Untracked GOAL.md" injection now lists only untracked files (the `ls` fallback listed every retained spec, growing forever); every skill's injected sub-commands (`ls`/`head`/`tail`, `git ls-files`) are now covered by its allowed-tools and settings.json, so a permission-blocked injection can't silently blank the Current-state block.

## 2026-07-16 — harness-refinements F12: `skip review` misread as skipping /hs-review
`decision=applied commit=7eed19e target=.agents/skills/hs-build/SKILL.md`
- **Rationale:** renamed to `no-pause` with `skip review` kept as a documented deprecated alias for one cycle.

## 2026-07-16 — harness-refinements F13: Onboarding page drifts silently
`decision=applied commit=d391dab target=.agents/skills/hs-harness/SKILL.md`
- **Rationale:** post-apply verification (Step 6) now includes a getting-started.html staleness check whenever a change alters the factory's shape. (Checked for this run: the page references none of the renamed/changed specifics.)

## 2026-07-16 — harness-refinements F14: `git checkout *` allowed silent tree discard
`decision=applied commit=07d26c8 target=.agents/skills/hs-feature/SKILL.md`
- **Rationale:** dropped from all three frontmatters (`git switch` covers every real use); portability.md now states the allowlists' honest scope (accident-protection, not confinement). Also added the missing `git pull` allowance hs-publish's local mode always needed.

## 2026-07-20 — task-slot F1: set_phase.py couldn't update a reopened phase's verify
`decision=applied commit=4793cba target=.agents/factory/bin/set_phase.py`
- **Rationale:** `--name/--verify/--satisfies/--depends-on` were gated behind `--add-phase`, so remediation (which reopens phases) could not tighten a too-weak gate without hand-editing YAML — leaving the frontmatter `verify` (next_phase.py's source of truth) stale vs the real gate. `--phase` now mutates the phase body in place (symmetric with `--add-phase`); name/verify are un-blankable, empty `--satisfies/--depends-on` clears the list, and `--after` stays add-phase-only. hs-build's remediation step now points at the scripted retune path; extends F4/F5 (file-backed set_phase flags), reverts nothing.

## 2026-07-22 — deflake-group-retry-test F1: research-skip misfired for diagnostic fixes
`decision=applied commit=2625f94 target=.agents/skills/hs-plan/SKILL.md`
- **Rationale:** Step 3 skipped the research fan-out on `kind: fix`/`appetite: small`, but those are proxies for "is the root cause known?" — a diagnostic fix (unreproducible failure, `— diagnosis-gated` GOAL) needs the fan-out most, since the investigation is the deliverable. Added a carve-out: unknown root cause / GOAL-requested diagnosis runs the full fan-out regardless of kind/appetite; an explicit `skip research` stays a human override. Strengthens rigor, weakens no gate; generalizes to any bug whose cause is unknown. No ledger collision (Step 3 untouched before).

## 2026-07-22 — deflake-group-retry-test F2: temp_site.sh cwd leaked verify-drive files into the repo
`decision=applied commit=c19e873 target=.agents/factory/bin/temp_site.sh`
- **Rationale:** the helper isolated env but never cd'd into the site, so a relative write in a verify/cross-check drive (`sh -c "… > t.in; …"`) escaped into the working tree, where hs-build's `git add -A` swept it into the atomic commit. Fix contains cwd inside the site. The finding's literal `cd "$site"` was **broken** — re-derived and empirically confirmed it breaks `uv run` project discovery (uv walks up from cwd; a /tmp site has no pyproject.toml); the applied fix captures the repo root into `UV_PROJECT` before `cd` so `uv run` still resolves the project. Verified live: relative write lands in the site, `uv run hs` works, nothing leaks. Extends F6 (`e78e457`, which created temp_site.sh); reverts nothing. Deferred the optional hs-build Step-7 `Touches:` guard — `Touches:` is free-form prose and the leak is moot once writes are contained.
