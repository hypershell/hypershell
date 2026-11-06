# REVIEW — Mark what is new in the docs, now that the published site tracks `develop`

> Adversarial QA by `hs-review`, run in an isolated/clean context. The correctness pass grades the
> branch diff against [`GOAL.md`](GOAL.md) + the AGENTS.md invariants **only** — it does not see
> `PLAN.md`/`TECH.md`. Every finding cites an **executed** command, not an assertion.

- **Reviewed commit:** 93c1e5f  ·  **Base:** develop (`92753f0`)  ·  **Date:** 2026-11-06
- **Verdict:** changes-requested
- **Cycle:** 1 of ≤3

## Verification run

- `uv run --python 3.12 --group docs sphinx-build -W --keep-going -q -E -b html docs …` → exit 0, in
  the worktree environment and in a fresh project environment.
- Gate test: a temporary ``:ref:`no_such_label_xyz` `` in `docs/whats_new.rst` → `WARNING: undefined
  label`, exit 1; reverted.
- `sphinx-build -b man -W` → exit 0; no marker, `:pr:` or notice text in `hs.1`.
- All 45 rendered markers extracted with their links; every `:pr:` checked with `gh pr view`
  (merged into `develop`, title matches); first-carrying pre-release checked with `git merge-base
  --is-ancestor` against tags `2.9.0a1`–`a6`.
- `gh api repos/…/commits/<sha>/pulls` → no PR for `961fb22`, `210a93e`, `27db1b0`, `ee03014`, so
  a `:commit:` link is the only link possible for those four.
- `announcement()` exercised for `2.9.0a6`, `2.9.0rc1`, `2.9.0.dev3`, `2.9.0a6.dev1` (notice),
  `2.9.0`, `2.9.0.post1` (none), `2.9.1.dev0`, `3.0.0a1`.
- An AST comparison of `__init__` parameters at `2.8.5` and `origin/develop` (F1).
- The notice text appears once on `index`, `cli/relay`, `api/data`, `whats_new`, `manual`, `search`
  and `genindex`.

## Requirement → evidence matrix

| R-ID | Implemented by (file/commit) | Verified how | Status |
|------|------------------------------|--------------|--------|
| R1 | `030fc64`: markers across `docs/`; `:pr:` and `:commit:` extlinks (`9518f78`, `030fc64`) | strict build, link and ancestry checks, spot checks of 15 features against 2.8.5 source; no false markers | ❌ F1 |
| R2 | `9e7d7ce`: `docs/_ext/site_notice.py`, `docs/conf.py`, `_static/custom.css` | `announcement()` across eight versions; rendered on seven pages | ✅ (F3 low) |
| R3 | `9e7d7ce`: `docs/whats_new.rst` | toctree, cautions, attributions, refs resolve, "Not yet released" true at `92753f0` | ✅ (F2 to triage) |
| R4 | `9518f78`: `docs` job in `tests.yml`; three warnings fixed | job triggers and pins match; gate test fails on a broken ref | ✅ |
| R5 | `93c1e5f`: AGENTS.md, invariants §12, rubric, TECH template, build/review/publish/release skills | read for consistency across and within skills | ✅ (F4 low) |
| R6 | — (conditioned on landing) | live root and `cli/relay.html` 200; `whats_new.html` 404 before landing | ⏳ post-landing |

Unmapped changes: none of consequence. The seed's Problem lists "the template environment rules"
among unpublished 2.9 material, though #81 was backported to 2.8.5.

**R6 post-landing check must observe:** `/whats_new.html` 200 with "What's New in 2.9"; the notice
on `/`, `/cli/relay.html` and `/api/data.html` with a working link; "Added in version 2.9.0" on
`/cli/relay.html` linking `pull/82`.

## Findings

### [MEDIUM/CONFIRMED] F1 — API parameters from #48 carry no marker
- **Where:** `docs/api/cluster.rst:54-55`; `docs/api/server.rst` (the `ServerThread` autoclass).
- **Failure scenario:** a 2.8.5 reader of the API reference sees `from_json` on `ServerThread` and the
  four cluster classes, and `template` on `ServerThread`, as stable-release parameters.
- **Evidence:** AST signature diff 2.8.5 → develop: `ServerThread + [template, from_json]`; each
  cluster class `+ [from_json, zone]` (`AutoScalingCluster` also `evict_after`). `git log
  -S"from_json: bool = False"` → `a71abd8` (#48). The only cluster marker names `zone` (#78);
  `api/server.rst` has none.
- **Requirement:** R1.

### [LOW/CONFIRMED] F3 — the notice link is root-absolute
- **Where:** `docs/_ext/site_notice.py:32` (`href="/whats_new.html"`).
- **Failure scenario:** correct on the live site, which is served at the root; broken in a build
  opened from disk or served under a path.
- **Evidence:** rendered `cli/relay.html` carries `/whats_new.html`; live `curl -sI` 200 at root.
- **Requirement:** R2.

### [LOW/CONFIRMED] F4 — the rubric grades the docs rule both auto-CRITICAL and HIGH
- **Where:** `.agents/factory/review-rubric.md:34` (scope item 3, "auto-CRITICAL … includes the docs
  same-commit rule") against the severity table at `:69` (§12 is HIGH).
- **Failure scenario:** two reviewers grade the same missing marker at different severities.
- **Evidence:** text of the two lines; `invariants.md` puts the rule in §12 and `hs-review` calls it a
  §12 finding.
- **Requirement:** R5.

### [LOW/PLAUSIBLE] F2 — What's new lists features and cautions but not 2.9-only fixes
- **Where:** `docs/whats_new.rst`.
- **Failure scenario:** a reader doesn't learn of user-visible 2.9 fixes: stale or duplicate results
  refused and logged at WARNING (#70), the open-file reserve refusing a connection near the
  descriptor limit with a warning (#76), the TimescaleDB 2.29 lost-write workaround (#75), cheaper
  writeback (#73).
- **Evidence:** `apply_results` and `RESERVE: Final[int] = 32` absent at 2.8.5, present on develop.
- **Requirement:** R3 ("every user-visible change"). Interpretation call.

## Human-gate triggers

- None. No finding touches the coupled core or a security/DB-lifecycle invariant; the diff has no
  `src/` change.

## Orchestrator triage

- F1, F3, F4: remediate (P2, P3, P4 reopened).
- F2: accepted as in scope. R3 says every user-visible change, and a short *Fixes* group is cheaper
  than the argument. Remediated in P3.
- The seed's "template environment rules" line is corrected in the seed. `GOAL.md` is locked and the
  same clause there stays: it does not change any R-ID.

## Review cycle 2 — changes-requested (2026-11-06)

- **Reviewed commit:** ff5cc61 (graded diff outside `spec/` identical to `c067ecc`) · **Mode:** fresh
  blind pass over the full spec-excluded diff.
- **Contract drift:** `ff5cc61` adds one dated clarification to `GOAL.md`: a change that reached
  `develop` as a direct commit is linked with `:commit:`. That is Geoffrey's ruling (chat,
  2026-11-06), not a silent drift.
- **Cycle-1 findings:** F1, F3 and F4 verified fixed (the AST comparison now finds every autodoc'd
  parameter change marked except F3 below; the notice link is absolute; the rubric reads HIGH).

### Verification run

- Strict HTML build exit 0; a temporary broken ref → exit 1, reverted; `-b man -W` clean, no HTML
  leakage. All 14 `:pr:` links → the right merged PR; the four `:commit:` targets resolve and have
  no PR. Every pre-release attribution checked with `git merge-base --is-ancestor`.
- Throwaway-site runs comparing `uvx hypershell==2.8.5` with `develop`:
  - a resource-error task: `-3` on 2.8.5, `-1002` on develop;
  - a task that kills itself with `SIGHUP`: `-1`, one attempt, no retry under `-r 2`, on 2.8.5.
- Notice logic exercised for a6, dev, rc, a7.dev3 (notice) and 2.9.0, 2.9.1, post1 (none).

### Findings

- **[MEDIUM/CONFIRMED] F5 — What's new omits the exit-code remap.** `27db1b0` (2.9.0a1) moved the
  template-error code from -2 to -1001 and the resource-error code from -3 to -1002. Older rows keep
  -2 and -3 and now read as SIGINT and SIGQUIT under `--signal`. Neither a change entry nor an upgrade
  caution says so. (R3)
- **[MEDIUM/CONFIRMED] F6 — false marker: `SIGHUP` cancellation is 2.8.5 behaviour.**
  `docs/cli/task_update.rst:18-20` and the What's new "Signals and cancellation" entry present it as
  2.9. A 2.8.5 run records -1 and does not retry. (R1 non-goal)
- **[MEDIUM/CONFIRMED] F7 — client API markers name only `zone`.** #78 also changed `bundlesize` on
  `run_client` and `ClientThread`: the default is now `None`, and the value is the size the client
  requests. `docs/api/client.rst:26`, `:44`. (R1)
- **[MEDIUM/CONFIRMED] F8 — `hs-release` Step 3a.1 would mislink the `:commit:` markers.** It
  completes every marker "with no `:pr:`", and its `-S'<marker line>'` search finds this docs
  cycle's commit, so the three `:commit:`-only markers would get this PR's number. (R5)
- **[LOW/CONFIRMED] F9 — What's new omits two 2.9.0a2 fixes:** the clean `cron` extra hint
  (`f4448a3`), and the fork descriptor leak and lockless-filesystem fallback in logging
  (`ede1487`). (R3)
- **[LOW/CONFIRMED] F10 — the notice doesn't say the site can describe unreleased work,** as the
  GOAL clarification says it should. (R2)
- **[LOW/CONFIRMED] F11 — `hs-review` and `invariants.md` still call every invariant violation
  auto-CRITICAL,** while the rubric grades §12 HIGH. (R5)
- **[LOW/PLAUSIBLE] F12 — at a full release, `hs-release` Step 3a.2 says to start "a fresh page"**
  without saying which file is live. The notice and the harness all point at `whats_new.rst`. (R5)

### Human-gate triggers

- None: docs and harness only.

### Orchestrator triage

All eight are taken. F12 is resolved by keeping `docs/whats_new.rst` as the live page for the
development line and copying it to a per-line record at a full release. This is cycle 2 of ≤3.

## Review cycle 3 — changes-requested (2026-11-06)

- **Reviewed commit:** 7b06e71 · **Mode:** fresh blind pass over the full spec-excluded diff.
- **Cycle-2 findings:** F5–F12 verified fixed. No false markers. Every pre-release attribution
  checked across 21 commits; the mixed-version refusal and the exit-code remap reproduced in
  throwaway sites.

### Verification run

- Strict HTML build and `-b man -W` exit 0; broken ref → exit 1, reverted; the three base warnings
  reproduce on a `git archive origin/develop` build and are gone on the branch.
- Introspection of every autodoc'd `__init__`, function, lifecycle method and autodata, 2.8.5 vs
  develop (F1, F4); throwaway-site probes of `submit_from` with dict sources and `ServerThread.start`
  on a bound port, on both versions.
- `gh api …/commits/<sha>/pulls` for every `:commit:` link (F2); a scratch-clone simulation of the
  `hs-release` lookup with two same-version markers in one file (F3).

### Findings

- **[HIGH/CONFIRMED] F13 — `api/submit.rst` has no marker for #48's dict sources.** `source` on
  `submit_from`, `SubmitThread` and `LiveSubmitThread` takes `Iterable[str | dict]` on develop and
  `Iterable[str]` on 2.8.5; a dict source with a template field fails on 2.8.5 and works on develop.
  (R1)
- **[MEDIUM/CONFIRMED] F14 — two What's new fixes link commits that came through PRs.** `f4448a3` is
  #56's merge and `ede1487` is #54's. The GOAL clarification allows `:commit:` only with no PR. (R3)
  The cycle-2 remediation introduced this.
- **[MEDIUM/PLAUSIBLE] F15 — `hs-release` Step 3a.1's `-S'<marker line>'` lookup returns every
  commit that added a same-version marker to the file,** so with two in one file before a release
  the first result is wrong for the older one. Simulated in a scratch clone. (R5)
- **[LOW/CONFIRMED] F16 — `ServerThread.start` now raises a queue start failure in the caller
  (#76),** documented by the autodoc'd docstring and unmarked; 2.8.5 hangs on a bound port. (R1)
- **[LOW/CONFIRMED] F17 — the `uuid7` requirement for the TimescaleDB provider in `install.rst`
  (#71) is unmarked.** (R1)
- **[LOW/PLAUSIBLE] F18 — `hs-release` Step 3a.2 says "under a heading for this version",** but the
  page groups by topic and gives the pre-release inline. (R5)

Unmapped: excluding `_include` from the build removes about 70 fragment pages (`/_include/*.html`)
that are live today and linked from no built page. Noted for the PR body.

### Human-gate triggers

- None: docs and harness only. **Escalated anyway:** this is the third cycle without convergence
  (`hs-review` bound), so the next step is Geoffrey's.

## Review cycle 4 — approved (2026-11-06)

- **Mode:** a scoped delta check, chosen by Geoffrey at the three-cycle bound (chat, 2026-11-06). A
  fresh blind reviewer checked only F13–F18 against the fix, `627b2f1`, using
  `git diff 7b06e71 627b2f1`.
- **Result:** all six fixed, with nothing new introduced. Evidence:
  - `gh pr view` merge commits for #48, #54, #56, #71 and #76;
  - `git merge-base --is-ancestor` attributions;
  - the strict HTML and man builds, with the new markers rendered;
  - the `git blame` lookup tested in three scratch repos (two same-version markers in one file,
    adjacent same-shaped sections, an identical directive directly above another), all correct.
- **One older finding, fixed by the orchestrator after the check:** [LOW/CONFIRMED] `hs-release`
  Step 3a.1 said squash subjects end `(#NN)`. Seven merges into `develop` don't, and that is how
  F14 happened. In `3a28887` the step looks up the commit's PR with `gh api …/commits/<sha>/pulls`.
  Checked by hand: `ede1487` → [54], `f4448a3` → [56], `27db1b0` → []. The change is one
  sentence of harness text and adds no new check.
- **R6** stays post-landing. After merge, the live site must show:
  - `/whats_new.html` returns 200;
  - the notice on `/` and on nested pages, with a working link;
  - `/cli/relay.html` reads "Added in version 2.9.0" and links `pull/82`;
  - `/_include/*.html` now returns 404 (expected, since the build excludes `_include`).
