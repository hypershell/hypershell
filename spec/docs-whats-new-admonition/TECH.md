---
slug: docs-whats-new-admonition
title: Replace the docs banner and What's new page with an index admonition
kind: docs
appetite: small
status: in_review
branch: feature/docs-whats-new-admonition
base: develop
current_phase: done
last_updated: '2026-11-11'
phases:
- id: P1
  name: 'Docs: drop the page and the notice, add the admonition'
  status: done
  satisfies:
  - R1
  - R3
  - R4
  depends_on: []
  parallel: false
  hammerable: false
  hill: downhill
  attempts: 1
  verify: 'test ! -e docs/whats_new.rst && test ! -e docs/_ext && test -z "$(grep
    -n -E ''whats_new|site_notice'' docs/index.rst)" && test -z "$(cat docs/conf.py
    docs/_static/custom.css | grep -E "whats_new|site_notice|announcement|''_ext''|_ext/")"
    && python3 -c "s=open(''docs/index.rst'').read(); a=s.find(''.. admonition:: What'');
    u=s.find(''\nUsage\n-----''); w=s[a:u]; assert 0 <= a < u, ''admonition not above
    Usage''; assert 3 <= w.count(''\n    * '') <= 5, ''highlight count''; assert w.count(''https://www.hypershell.org/blog/hypershell-2-9-0'')
    == 1 and w.count(''https://www.hypershell.org/blog/hypershell-2-8-x'') == 1, ''links'';
    assert ''hs task'' not in w and '' -k '' not in w, ''commands''" && uv run --python
    3.12 --group docs sphinx-build -W --keep-going -q -E -b html docs docs/_build/html
    && test -z "$(grep -rl ''class="announcement'' docs/_build/html)" && test "$(curl
    -sS -o /dev/null -w ''%{http_code}'' https://www.hypershell.org/blog/hypershell-2-9-0)"
    = 200 && test "$(curl -sS -o /dev/null -w ''%{http_code}'' https://www.hypershell.org/blog/hypershell-2-8-x)"
    = 200 && echo VERIFY_OK'
- id: P2
  name: 'Factory lockstep: one rule, the note in the squash body, hs-release refreshes
    the admonition'
  status: done
  satisfies:
  - R5
  - R6
  - R7
  - R8
  depends_on:
  - P1
  parallel: false
  hammerable: false
  hill: downhill
  verify: 'test -z "$(git ls-files -z -- '':!spec/'' | xargs -0 grep -s -I -l -E ''whats_new|site_notice'')"
    && test -z "$(git ls-files -z -- '':!spec/'' | xargs -0 grep -s -I -l -i -E ''not
    yet released|site notice'')" && test "$(grep -l ''squash commit body'' AGENTS.md
    .agents/factory/invariants.md .agents/factory/review-rubric.md .agents/factory/templates/TECH.md
    .agents/skills/hs-build/SKILL.md .agents/skills/hs-review/SKILL.md .agents/skills/hs-publish/SKILL.md
    | wc -l | tr -d '' '')" = 7 && grep -q ''^### Step 3a — Docs: complete the markers
    and refresh the index admonition'' .agents/skills/hs-release/SKILL.md && grep
    -q ''refresh the index admonition for X.Y.Z'' .agents/skills/hs-release/SKILL.md
    && echo VERIFY_OK'
review:
  last_reviewed_commit: ''
  verdict: none
  blocked_reason: ''
  cycle: 0
---
# TECH.md — Replace the docs banner and What's new page with an index admonition

The **context engine and finite-state machine** for building this feature. The YAML
frontmatter above is the resume ground-truth (read it with
`uv run python .agents/factory/bin/next_phase.py spec/docs-whats-new-admonition/TECH.md`); the
per-phase checklists below are the work. `hs-build` executes the next actionable phase, runs its
`verify:` command, updates state via `uv run python .agents/factory/bin/set_phase.py …`, and makes
one atomic code+state commit.

- **Vision / requirements (locked):** [`GOAL.md`](GOAL.md) — R-IDs are the contract.
- **Authoritative design:** [`PLAN.md`](PLAN.md) (§2 holds the admonition text and the per-file
  factory edits).
- **Backing research:** none (`appetite: small`, no fan-out trigger).

## Frontmatter field reference

- `status` (top): `planned | in_progress | blocked | in_review | done` (`done` is stamped by
  `hs-publish` after confirmation, just before landing — the terminal state of the retained record)
- `appetite`: `small | big` — caps phase count and build-iteration budget (circuit breaker).
- phase `status`: `pending | in_progress | done | blocked`
- `satisfies`: GOAL R-IDs this phase delivers (traceability anchor for `hs-review`).
- `depends_on`: phase ids that must be `done` first (a phase is actionable only when met).
- `parallel`: `true` only for genuinely independent, non-coupled work. Both phases here are `false`:
  P2's R8 grep needs P1's deletions in place.
- `hammerable`: `false` on both — the docs and the factory must move together, or the factory points
  at a deleted file.
- `hill`: `uphill` → `crest` → `downhill`.
- `attempts`: durable failed-verify counter, bumped by `set_phase.py --phase P<n> --record-attempt`.
- `verify`: the exact command that proves the phase. Both are folded YAML (`>-`); run the folded
  one-line string as is. Both `curl` calls in P1 reach www.hypershell.org, and `uv run` needs the
  sandbox off on macOS.
- `review.cycle`: completed review passes, auto-incremented by every `set_phase.py --verdict …`.

## Conventions (apply to every phase)

- Commit conventions, code style, and load-bearing invariants come from
  [`AGENTS.md`](../../AGENTS.md). Only `invariants.md` §12 is touched (PLAN §3).
- One phase per `hs-build` invocation; one atomic commit containing **both** the change and the
  `TECH.md` state change. Subjects: `[docs] Build docs-whats-new-admonition P<n>: …`.
- **No `Co-Authored-By` trailer.**
- This cycle changes the docs same-commit rule itself. From P2 on, the rule is: a user-visible change
  updates its prose page in the same commit with a `versionadded`/`versionchanged` marker, and its
  user-facing note goes in the PR's squash commit body. This cycle is docs-only and adds no marker.
- Never edit other cycles' files under `spec/` (GOAL non-goal); their `whats_new` references stay.

---

## Phase P1 — Docs: drop the page and the notice, add the admonition
**Satisfies:** R1, R3, R4 · **Depends on:** —
**Goal:** the built site has no What's new page, no banner on any page, and a "What's new"
admonition above *Usage* on the home page.

> **Amended 2026-11-11 (maintainer ruling via the foreman, GOAL Clarifications):** R2 is withdrawn
> ("Ignore the old 'whats_new' page, it was only live for a day"). No redirect stub and no
> `redirects` key; the verify no longer checks a built `whats_new.html`, and the `conf.py`/`custom.css`
> grep now expects no match at all.

- [x] `git rm docs/whats_new.rst`; remove `whats_new` from the *Intro* toctree in `docs/index.rst`.
- ~~`docs/conf.py`: add a `redirects` key for the What's new page.~~ Dropped with R2.
- [x] `git rm docs/_ext/site_notice.py`. In `docs/conf.py` remove the `_ext` `sys.path` line and its
      comment, the `from site_notice import announcement` import, the now-unused `import os` /
      `import sys`, `'_ext'` from `exclude_patterns`, `'color-announcement-background'`, and the
      `site_notice = announcement(…)` block with its comment.
- [x] `docs/_static/custom.css`: remove the `.announcement` and `.announcement-content` rules and
      their comment.
- [x] `docs/index.rst`: add the `.. admonition:: What's new` block from PLAN §2 after the *Design
      elements* list, before the `-------------------` rule that opens *Usage*. Bullets indented four
      spaces (`    * `), three to five of them; both links as anonymous references (`__`).
- **Verify:** the folded `verify:` above (file checks, admonition position and links, `-W` build,
  no `class="announcement`, both URLs 200). Prints `VERIFY_OK`.
- **Touches:** `docs/whats_new.rst` (deleted), `docs/_ext/site_notice.py` (deleted), `docs/conf.py`,
  `docs/_static/custom.css`, `docs/index.rst`.

## Phase P2 — Factory lockstep
**Satisfies:** R5, R6, R7, R8 · **Depends on:** P1
**Goal:** every factory file states the one rule, `hs-publish` drafts the user-facing note for the
squash commit body, `hs-release` Step 3a refreshes the admonition, and no tracked file outside
`spec/` names the page or the notice at all (R2's redirect-key exception went with R2).

Keep the literal phrase `squash commit body` unbroken on one line in each of the seven R5/R6 files,
and the new Step 3a heading and commit subject each on one line: the verify greps them.

- [x] `AGENTS.md` conventions bullet: swap the *Not yet released* clause for the note-in-squash-commit-body
      clause; keep the `:pr:`-at-release sentence. (`CLAUDE.md` is a symlink — no separate edit.)
- [x] `AGENTS.md` § Docs: replace the `docs/whats_new.rst` and site-notice sentences with the note
      rule and `hs-release`'s two release-time jobs (draft GitHub notes from `git log`; refresh the
      `docs/index.rst` admonition at each pre-release or full release).
- [x] `.agents/factory/invariants.md` §12, `.agents/factory/templates/TECH.md` Conventions,
      `.agents/skills/hs-build/SKILL.md` same-commit parenthetical: the same swap.
- [x] `.agents/factory/review-rubric.md` §3 and `.agents/skills/hs-review/SKILL.md`: the §12
      finding is a missing prose update or marker, or a `-W` warning; state the note rule as
      context (drafted at publish, not graded at review).
- [x] `.agents/skills/hs-publish/SKILL.md`: add the **User-facing note** body section (user-visible
      changes only; one to three sentences; top-level commands only, no `-k`; written to be the
      squash commit body); drop `docs/whats_new.rst` from *Docs & completions* (keep the `:pr:`
      sentence); Step 4a `--body "{user-facing note, or empty}"`; Step 4b adds `-m "{user-facing
      note}"` when there is one.
- [x] `.agents/skills/hs-release/SKILL.md`: Step 3a per PLAN §2 — new heading, item 1 unchanged,
      item 2 refreshes the admonition (highlights, two links, curl each, keep a not-yet-live link and
      raise it at Step 7), item 3 subject `[docs] Complete version markers and refresh the index
      admonition for X.Y.Z`; drop the move-and-reset, `whats_new_X_Y.rst` and notice text. Step 7 and
      the release-notes Safety bullet read commit subjects and bodies.
- **Verify:** the folded `verify:` above (R8 grep returns nothing; no "not yet
  released"/"site notice" outside `spec/`; seven files carry `squash commit body`; the Step 3a
  heading and subject are present). Prints `VERIFY_OK`.
- **Touches:** `AGENTS.md`, `.agents/factory/{invariants.md,review-rubric.md,templates/TECH.md}`,
  `.agents/skills/{hs-build,hs-review,hs-publish,hs-release}/SKILL.md`.

---

## How `hs-build` drives this

1. `next_phase.py` prints the next actionable phase (statuses are authoritative; the
   `current_phase` pointer is reconciled against them).
2. Pre-flight: clean tree, on `branch`, `base` reachable.
3. Execute every `[ ]` in the phase (consult `PLAN.md` for detail).
4. Run the phase's `verify:` command — never advance on a checkbox alone. `VERIFY_OK` must print.
5. Amend this file freely if reality diverges (regenerate frontmatter with `set_phase.py`; note the
   amendment in the commit body). STOP and escalate only on a **`GOAL.md` contradiction**.
6. Mark the phase `done`, advance `current_phase`, `--touch`; one `[docs]` commit; stop and report.
