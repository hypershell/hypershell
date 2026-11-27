# PLAN — Replace the docs banner and What's new page with an index admonition

> **Status:** Draft for review · **Last updated:** 2026-11-11
> **Authoritative technical design.** The *how*. Vision/contract is [`GOAL.md`](GOAL.md);
> the phased executable roadmap is [`TECH.md`](TECH.md). No `research/`: `appetite: small`,
> `kind: docs`, no high-blast-radius file touched, and the GOAL hands no open question to planning
> (the admonition's wording and the PR-body section's shape are design choices made here).
>
> **Amended 2026-11-11:** R2 is withdrawn by maintainer ruling (GOAL Clarifications). The
> `whats_new` redirect key and its comment below are not built, and R8's grep expects no match.

## 1. Summary

Two text-only slices. **P1** changes the docs: delete `docs/whats_new.rst` and
`docs/_ext/site_notice.py`, strip the notice's wiring from `docs/conf.py` and `docs/_static/custom.css`,
add one `redirects` key, and add a "What's new" admonition to `docs/index.rst`. **P2** moves the
factory in lockstep: every file that asks for a *Not yet released* entry instead states one rule (prose
page + marker in the same commit, user-facing note in the PR's squash commit body), `hs-publish`
drafts that note, and `hs-release` Step 3a refreshes the admonition instead of curating the page. No
source under `src/hypershell/` changes.

## 2. Design

### P1 — docs

- **`docs/whats_new.rst`** — `git rm`. Drop `whats_new` from the *Intro* toctree in `docs/index.rst`
  (the toctree then opens on `getting_started`). Nothing else references the page or its
  `.. _whats_new:` label (checked: no `:ref:` to it outside `spec/`).
- **Redirect (R2)** — add `'whats_new': 'https://www.hypershell.org/blog/hypershell-2-9-0',` to the
  `redirects` map in `docs/conf.py`, and extend the comment above the map to say the What's new page
  moved too. `sphinx-reredirects` then writes `docs/_build/html/whats_new.html`, a meta-refresh stub
  whose `url=` is the target. The key is explicit, as the comment already requires.
- **Notice removal (R3)** — `git rm docs/_ext/site_notice.py` (the directory's only file, so `_ext/`
  disappears). In `docs/conf.py` remove: the `sys.path.insert(… '_ext')` line, its comment and the
  `from site_notice import announcement` import; the now-unused `import os` and `import sys`; the
  `'_ext'` entry in `exclude_patterns`; `'color-announcement-background'` in `dark_css_variables`;
  and the `site_notice = announcement(…)` block with its comment. In `docs/_static/custom.css` remove
  the `.announcement` / `.announcement-content` rules and their comment. Furo then never receives an
  `announcement` option, so no page renders `<div class="announcement">`, whatever the version.
- **Admonition (R4)** — in `docs/index.rst`, after the *Design elements* bullet list and before the
  `-------------------` rule that opens *Usage*, so it sits between "what it is" and "how to use it".
  Starting wording (the builder may tighten it; the R4 bounds are fixed):

  ```rst
  .. admonition:: What's new
      :class: tip

      *HyperShell* 2.9 is in pre-release. Highlights so far:

      * **Zones and relays:** place tasks and clients by name, and run one campaign across several sites.
      * **TimescaleDB and in-place upgrades:** a TimescaleDB provider with columnstore compression,
        and a 2.8 database upgraded in place by the first 2.9 process.
      * **Tasks from JSON,** safe re-submission, and per-executor task slots.
      * **Installing 2.9:** it is a pre-release, so ``pip install --pre hypershell``. Upgrade the
        server and every client together, and back up the database first.
      * **2.8.1 to 2.8.7:** fixes for cancelled tasks, retries and stranded task groups, and the run
        key kept off command lines.

      Read the release notes for `2.9.0 <https://www.hypershell.org/blog/hypershell-2-9-0>`__ and
      `2.8.1 to 2.8.7 <https://www.hypershell.org/blog/hypershell-2-8-x>`__.
  ```

  Five highlights (R4 allows three to five), each traced to a heading of the two posts (2.9.0:
  *Zones*, *Relays*, *TimescaleDB provider*, *In-place database upgrade*, *Upgrading from 2.8*;
  2.8.x: v2.8.1, v2.8.4, v2.8.5). Anonymous links (`__`) so the link text can't collide with a named
  target and warn under `-W`. No command is named, so the top-level-commands and no-`-k` rules hold
  trivially. Both URLs answered HTTP 200 on 2026-11-11 (curl).

### P2 — factory lockstep

**The one rule (R5),** stated in each file in that file's register, with the literal phrase
`squash commit body` unbroken on one line (P2's verify counts it):

> A user-visible change also updates, in the same commit, the prose page that describes it, marked
> `.. versionadded::` / `.. versionchanged::` with the release line. Its user-facing note goes in the
> PR's squash commit body, which `hs-publish` drafts.

| File | Edit |
|---|---|
| `AGENTS.md` (conventions bullet, L48–52) | Replace "plus an entry under *Not yet released* in `docs/whats_new.rst`" with the note-in-squash-commit-body clause; keep the `:pr:`-at-release sentence. `CLAUDE.md` is a symlink to it. |
| `AGENTS.md` § Docs (L514–516) | Replace the `docs/whats_new.rst` and site-notice sentences with: the user-facing note travels in the squash commit body; `hs-release` drafts the GitHub release notes from `git log` and refreshes the "What's new" admonition on `docs/index.rst` at each pre-release or full release. No site notice is described. |
| `.agents/factory/invariants.md` §12 | Same swap in the user-visible bullet; keep "The docs build passes with `-W`". |
| `.agents/factory/review-rubric.md` §3 | The §12 finding becomes "no prose update or no `versionadded`/`versionchanged` marker, or a docs build that warns under `-W`", plus the rule's note clause stated as context. Review runs before `hs-publish` writes the PR body, so the note is not a review-time finding. |
| `.agents/factory/templates/TECH.md` (Conventions) | Same swap. |
| `.agents/skills/hs-build/SKILL.md` (L79–82) | Same swap inside the same-commit parenthetical. |
| `.agents/skills/hs-review/SKILL.md` (L60–63) | "…whose prose page or `versionadded`/`versionchanged` marker is missing is an `AGENTS.md` §12 finding"; add that the user-facing note belongs to the squash commit body and is drafted at publish. |
| `.agents/skills/hs-publish/SKILL.md` (R6) | Step 2 body gains a **User-facing note** section, for a user-visible change only: one to three sentences in release-notes voice (what changed for the user, top-level commands only, no `-k`), written to be the squash commit body; omitted when nothing is user-visible. *Docs & completions* drops the `docs/whats_new.rst` clause; the `:pr:`-not-added-here sentence stays. Step 4a's `gh pr merge … --body "{one-line summary, or empty}"` becomes `--body "{user-facing note, or empty}"`; Step 4b's local squash commits with `-m "{title}" -m "{user-facing note}"` when there is one. |
| `.agents/skills/hs-release/SKILL.md` (R7) | Step 3a heading → `Docs: complete the markers and refresh the index admonition (pre-release & full-release)`. Item 1 (`:pr:`/`:commit:` markers) unchanged. Item 2 replaced: refresh the `docs/index.rst` "What's new" admonition — three to five highlights drawn from the squash-commit notes since the last tag and the current posts, top-level commands only, no `-k`; its two links point at the current line's release-notes post and the previous series' post; `curl` each link and keep a link that does not yet answer 200 as it was, listing it for the Step 7 pause. The move-and-reset steps, the `whats_new_X_Y.rst` copy and the notice note go. Item 3's commit subject → `[docs] Complete version markers and refresh the index admonition for X.Y.Z`. Step 7 and the matching Safety-principles bullet: draft notes from `git log <lasttag>..HEAD` subjects **and bodies**, since each squash body carries its PR's user-facing note. |

### Requirement → design map

| R-ID | Design element(s) that satisfy it |
|------|-----------------------------------|
| R1 | P1: `git rm docs/whats_new.rst`; drop the toctree entry; `-W` build passes. |
| R2 | P1: `'whats_new'` key in `docs/conf.py` `redirects` → stub `whats_new.html` with `url=https://www.hypershell.org/blog/hypershell-2-9-0`. |
| R3 | P1: `git rm docs/_ext/site_notice.py`; conf.py import/`sys.path`/`os`/`sys`/`exclude_patterns '_ext'`/`color-announcement-background`/`announcement` block removed; `.announcement` CSS removed; no built page has `class="announcement`. |
| R4 | P1: `.. admonition:: What's new` in `docs/index.rst` above *Usage*, five highlights, both release-note links (200). |
| R5 | P2: the one rule in `AGENTS.md` (bullet + § Docs), `invariants.md`, `review-rubric.md`, `templates/TECH.md`, `hs-build`, `hs-review`; § Docs loses the site-notice sentence. |
| R6 | P2: `hs-publish` *User-facing note* section, Step 4a/4b squash body carries it; *Docs & completions* drops `docs/whats_new.rst`; `:pr:` still not added at publish. |
| R7 | P2: `hs-release` Step 3a retitled and rewritten; item 1 unchanged; commit subject names the admonition. Step 7 reads commit bodies so the notes reach the release draft (the GOAL Outcome's "drafts the GitHub release notes from `git log`"). |
| R8 | P1 + P2 together; P2's verify greps every tracked file outside `spec/` and expects only the redirect key. |

## 3. Invariant gate (AGENTS.md constitution check)

Checked against [`.agents/factory/invariants.md`](../../.agents/factory/invariants.md) before design
(gate #1) and again against the design above (gate #2). §1–§11 (task lifecycle, queue, TLS, FSMs,
exit statuses, config, cluster, …) are untouched: no file under `src/`, `tests/` or `share/` changes,
and no high-blast-radius file is on the touch list. Only §12 is touched.

- **§12, docs same-commit rule** — the rule is *amended* by R5 under the maintainer's ruling (GOAL
  Clarifications 1–2): prose page + marker in the same commit stay; the *Not yet released* entry is
  replaced by the squash-commit-body note. The `-W` clause stays verbatim.
- **§12, version single-sourced** — the admonition names release lines (`2.9`, `2.8.1 to 2.8.7`) in
  prose, as the `versionadded` markers and `redirects` targets already do; it does not hardcode the
  package version (`|release|` stays the only version substitution).
- **§12, CLI change → `_include` + `share/`** — not triggered: no CLI change.
- **AGENTS.md § Docs, `-W` build** — P1's verify runs the exact CI command.
- **AGENTS.md § Docs, top-level commands only** — the admonition names no command; the `hs-publish`
  and `hs-release` text that governs future notes and highlights says top-level commands only and
  no `-k`.

### Deviation justifications

| Deviation | Why needed | Simpler alternative rejected because |
|-----------|-----------|--------------------------------------|
| `invariants.md` §12 and `review-rubric.md` §3 drop the `docs/whats_new.rst` check, and the review gate no longer grades a release-note entry | The page is deleted (R1); the note now lives in the PR body, which `hs-publish` writes after review (R5, R6) | Keeping the check would point every cycle at a deleted file; grading the note at review is impossible because the PR body does not exist yet. A rule change by maintainer ruling, not a bend, recorded here for visibility. |

## 4. Rabbit holes (resolved)

- **Does the redirect stub collide with a real page?** → No: the source page is deleted in the same
  phase, and `sphinx-reredirects` only writes stubs for keys with no source doc (the existing
  `tutorial/*`, `blog/*` keys are the precedent).
- **Does Furo still render an empty banner without the option?** → No: `furo/theme/furo/page.html`
  wraps the `<div class="announcement">` in `{% if theme_announcement %}`; with no option set, nothing
  renders. P1's verify greps the built HTML.
- **Release ordering for the admonition refresh** — `hs-release` Step 3a runs before Step 7 drafts the
  GitHub notes, and the hypershell.org post is written from those notes, so at the first pre-release
  of a new line the post can lag the commit. → Step 3a keeps any link that does not yet answer 200 and
  surfaces it at the Step 7 pause (design above).

## 5. Risks & open questions

- **Squash body in the GitHub UI.** When the maintainer merges from the web UI rather than through
  `hs-publish` Step 4a, GitHub's default squash body is not the note; configuring that default is a
  GOAL non-goal. The PR-body section is labelled for copy-paste, so the note survives either way.
- **`hs-release` Step 7 edit** (reading commit bodies) is not named by R7's text; it is the one-clause
  link that makes the ruling's "drafts the GitHub release notes from `git log`" see the notes. If
  review grades it scope creep, it can be dropped without breaking any R-ID.
- **External links at review.** R4's 200 check depends on hypershell.org being up; the curl calls
  need network access outside the sandbox.

## 6. Verification strategy

No CLI behaviour changes, so there is no `temp_site.sh` drive. Each phase's verify is mechanical:

- **P1:** the files are gone; `conf.py` + `custom.css` mention none of
  `whats_new|site_notice|announcement|_ext` except the redirect key; the admonition sits above *Usage*
  with both links; the CI docs command (`sphinx-build -W --keep-going -q -E -b html`) passes; the
  built `whats_new.html` carries `url=https://www.hypershell.org/blog/hypershell-2-9-0`; no built
  page contains `class="announcement`; both release-note URLs answer 200.
- **P2:** across every tracked file outside `spec/`, `whats_new|site_notice` matches only the redirect
  key (R8); no tracked file outside `spec/` says "not yet released" or "site notice"; the seven
  R5/R6 files each contain `squash commit body`; `hs-release` carries the new Step 3a heading and
  commit subject.
