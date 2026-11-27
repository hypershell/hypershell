# REVIEW — Replace the docs banner and What's new page with an index admonition

> Adversarial QA by `hs-review`, run in an isolated/clean context. The correctness pass grades the
> branch diff against [`GOAL.md`](GOAL.md) + the AGENTS.md invariants **only** — it does not see
> `PLAN.md`/`TECH.md` (avoids grading-its-own-homework / plan-sycophancy). Every finding cites an
> **executed** command, not an assertion.

- **Reviewed commit:** 093eaa7  ·  **Base:** origin/develop (5f22298)  ·  **Date:** 2026-11-11
- **Verdict:** approved
- **Cycle:** 1 of ≤3 — mirrors `review.cycle` in `TECH.md` (escalate to human on non-convergence)

Run by hand from `hs-review` as it stands at the base (`origin/develop`), since this cycle edits
the skill, the rubric and the invariants. The blind reviewer got GOAL.md, the spec-excluded diff
command, and the base `invariants.md` and `review-rubric.md` (confirmed byte-identical to
`origin/develop`). It did not read PLAN, TECH, META or research.

**Contract drift.** `GOAL.md` moved once after shaping, in the P1 commit `0c27f86`: R2 (the
`whats_new.html` redirect) and R8's exception for its key were withdrawn by a maintainer ruling
relayed by the foreman on 2026-11-11 ("Ignore the old 'whats_new' page, it was only live for a
day"), recorded under Clarifications. The review grades that contract. R2 is not graded.

## Verification run

- `uv run --python 3.12 --group docs sphinx-build -W --keep-going -q -E -b html docs docs/_build/html`
  (the command in `.github/workflows/tests.yml:215`) → exit 0. The only output is the
  `pkg_resources` UserWarning from `sphinxcontrib/details` and "matplotlib is not installed, social
  cards will not be generated"; neither is a Sphinx warning.
- `grep -rl -E 'class="announcement|announcement-content' docs/_build/html` → no match, at version
  `2.9.0a7` (a pre-release, the case the old notice fired on). `docs/_build/html/whats_new.html`
  does not exist, and no built page links `whats_new`.
- `curl -sS -o /dev/null -w '%{http_code}'` → 200 for `https://www.hypershell.org/blog/hypershell-2-9-0`
  and `https://www.hypershell.org/blog/hypershell-2-8-x`.
- `git grep -n -E 'whats_new|site_notice' -- . ':(exclude)spec/'` → no output (rc 1).
- `git grep -n -i -E "what.s new|not yet released|site notice"` outside `spec/` → only the
  admonition (`docs/index.rst:52`), `AGENTS.md:517` and `hs-release/SKILL.md:167`, all three the
  new rule.
- Admonition claims checked against both posts and the code:
  - "the first 2.9 process upgrades a 2.8 database in place": the 2.9.0 post says so (lines
    83-84, 330-331). In the code, every command that opens the database goes through
    `ensuredb()`/`checkdb()`/`initdb()`, and each calls `upgradedb()` (`data/__init__.py:91,118`;
    callers in `server.py:1666`, `task.py`, `submit.py:1404-1406`, `cluster/__init__.py:560-562`,
    `relay/__init__.py:898`). No explicit `hs initdb` is needed for a 2.8 SQLite or PostgreSQL
    database. TimescaleDB's "run `hs initdb` first" applies to a new TimescaleDB database, which
    2.8 never had. Accurate.
  - "pin each task to its own core or GPU": each executor owns a fixed slot `0..N-1`
    (`client.py:1314`), exposed as `{slot}`/`{slot_count}` (`client.py:781`) and
    `TASK_SLOT`/`TASK_SLOT_COUNT` (`client.py:515-516`). The user pins with it
    (`taskset -c {slot}`, `CUDA_VISIBLE_DEVICES={slot}`). That is the post's own wording (line
    419, "Pin each task to its own core, or its own GPU") and the feature's stated purpose.
    Accurate as a statement of what the feature lets you do. Not a finding (see the note below).
  - Zones and relays, TimescaleDB with compression, tasks from JSON, safe re-submission, the
    install line, and the 2.8.1-2.8.7 fixes all match the posts, and `--zone`, `relay`,
    `--from-json`, `--repeat`/`--update` and `compress_after` exist in the code.

## Requirement → evidence matrix

| R-ID | Implemented by (file/commit) | Verified how | Status |
|------|------------------------------|--------------|--------|
| R1 | `docs/whats_new.rst` deleted; Intro toctree in `docs/index.rst` (0c27f86) | `-W` build exit 0; no `whats_new.html`; no `:doc:`/`:ref:` to it | ✅ |
| R2 | Withdrawn 2026-11-11 (maintainer ruling) | not graded | — |
| R3 | `docs/_ext/site_notice.py` deleted; `docs/conf.py` import, `sys.path`, `announcement`, `color-announcement-background`, `_ext` exclude; `.announcement` rules in `docs/_static/custom.css` (0c27f86) | no `announcement` class in built HTML at `2.9.0a7`; `git grep '_ext\b\|_ext/'` empty | ✅ |
| R4 | `docs/index.rst:52-69`, above *Usage* (0c27f86) | five bullets; both links 200; no `hs` subcommand, no `-k`; claims checked against posts and code | ✅ |
| R5 | `AGENTS.md:48-52`, `AGENTS.md:508-518`, `invariants.md` §12, `review-rubric.md`, `templates/TECH.md`, `hs-build`, `hs-review` (093eaa7) | read each; one rule (prose page + marker, same commit; user-facing note in the squash body); § Docs no longer describes a notice | ✅ |
| R6 | `hs-publish/SKILL.md:84-92`, Steps 4a/4b (093eaa7) | User-facing note section for the squash body; *Docs & completions* no longer cites the page; `:pr:` still not added at publish | ✅ |
| R7 | `hs-release/SKILL.md` Step 3a, Safety Principles, Step 7 (093eaa7) | heading and commit subject name the admonition; marker step unchanged; move/reset, `whats_new_X_Y.rst` copy and notice note gone; Step 7 reads squash bodies and raises any link not yet 200; step references resolve | ✅ |
| R8 | all of the above | `git grep -E 'whats_new\|site_notice'` outside `spec/` empty | ✅ |

Unmapped changes (possible scope creep): none. The `hs-release` Safety Principles line and Step 7
now read squash bodies as well as subjects, and `hs-publish` Step 4b carries the note into the
local squash commit. Both serve the Outcome's "drafts the GitHub release notes from `git log`".

## Findings

None. No CONFIRMED or PLAUSIBLE finding survived refutation.

Dropped by the reviewer under refutation: the P1 commit alone leaves the factory pointing at the
deleted page (P2 fixes it on the same branch, which squash-merges); the P2 commit is `[docs]`
though it edits `.agents/` (intermediate subjects do not reach `develop`); a patch release skips
Step 3a, so "upgrade to 2.8.7" can go stale (R7 scopes the refresh to pre-releases and full
releases).

Notes for the maintainer, not findings:

- **"Task files:" label.** The third bullet files per-executor slots under "Task files", though
  slots are a client feature. GOAL's proposed label was "Tasks from JSON, safe re-submission, and
  per-executor task slots". Wording only; the claims are accurate.
- **The 2.9.0 post points at the removed page.** `hypershell.org` `content/blog/hypershell-2-9-0.md`
  lines 530-531 say the docs "carry a *What's New in 2.9* page", and lines 645-646 send readers
  there for the full change list. Once this lands, that page is gone. Editing the posts is a GOAL
  non-goal and the maintainer ruled the old page can be ignored, so this is a follow-up for the
  hypershell.org repo.

## Human-gate triggers

- None. No CONFIRMED finding; nothing in the diff touches the high-blast-radius core or a
  security/DB-lifecycle invariant.

## Optional completeness sub-pass (separate reviewer; may see TECH.md)

- Not run (not requested).
