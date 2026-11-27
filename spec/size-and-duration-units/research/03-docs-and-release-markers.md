# 03 — Docs, help text and release markers

Read-only research for `hs-plan`. Line numbers are on this branch (develop) unless marked `2.8.6:`.

## 1. Marker and release-note convention for a fix in 2.8.7 and 2.9.0a7

**Rules.** `AGENTS.md:46-51` (same-commit rule) and § Docs `AGENTS.md:505-516`: a user-visible change
gets `versionadded`/`versionchanged` "naming the release line it ships in (`2.9.0`, never an alpha)"
plus a *Not yet released* entry in `docs/whats_new.rst`; `:pr:` is added by `hs-release` Step 3a,
which links *every* unlinked marker regardless of version (`.agents/skills/hs-release/SKILL.md:157`).
The baseline rule from the markers cycle: "behaviour backported to 2.8.4 or 2.8.5 is not 2.9 and is
not marked"; "An unmarked item is in the stable release" (`spec/docs-version-markers/markers.md:8-11,
76-87`; `GOAL.md:78`). No `versionchanged:: 2.8.x` marker exists anywhere on develop (grep: none);
44 markers are all `2.9.0`.

**Precedent — #92 (heart monitor `:memory:`), in 2.8.6 and develop.** Develop `290a7e2` touched
`docs/whats_new.rst` only: it replaced "Nothing is pending" with a *Not yet released → Fixes* entry
ending "This also ships in 2.8.6." (`docs/whats_new.rst:199-204`). No marker. The master cherry-pick
`0c3e062` has the same message but no `docs/whats_new.rst` — the file **does not exist on 2.8.6**, so
the cherry-pick's modify/delete conflict was resolved by dropping it. Earlier backports (#74, #80,
`0f789eb`, `a8f06fb`, `efedf96`) carried `_include/*_help.rst`/`templates.rst` edits onto master but
never markers; on develop they appear as upgrade cautions "This also shipped in 2.8.4/2.8.5, so it
matters only when upgrading from …" (`whats_new.rst:56-66`, added in `7005186`).

**Back-merge.** `5a54e86` makes a patch back-merge `git merge -s ours` after a per-file verdict table
(`SKILL.md:222-248`); develop's tree, whats_new included, is unchanged. `2acdfe7` (2.8.6) predates
it but resolved every conflict to develop's side and left the tree identical. So whats_new on develop
is written once, by the develop commit; nothing flows back from master.

**2.8.7 release notes.** Master has no whats_new; a patch release's notes are the GitHub release body
drafted at Step 7 from `git log <lasttag>..HEAD` (`SKILL.md:205-210`). R9's sentence must therefore be
in the squash commit **body** (it is reused verbatim in the cherry-pick) and checked in the Step 7
draft.

**Recommendation.**
- *develop:* one *Not yet released → Fixes* entry under the existing #92 one (`whats_new.rst:199-204`),
  in its style, carrying R9 — e.g. "**Memory sizes and timeouts read as written.** `16G`, `16GiB`,
  `512M` are powers of 1024, as `16GB` always was … timeouts take `1h30m`, `1.5h` or `1:30:00` …
  Before, a size with a short or binary unit (`16G`, `16GiB`) was read as that many bytes; tasks
  already stored that way are not corrected. This also ships in 2.8.7." (No `(2.9.0a7, :pr:)`;
  Step 3a adds it.)
- *Marker:* not `2.9.0` — false once 2.8.7 ships, and contradicts the "unmarked = stable" rule. Either
  (a) no marker, the #92 precedent, or (b) `.. versionchanged:: 2.8.7` with one clause (first of its
  kind; Sphinx renders "Changed in version 2.8.7", invisible to `check_markers.py`, which only scans
  `2.9.0`, `check_markers.py:15,57`). (b) better serves a 2.8.6 reader of the develop-built site, who
  would otherwise try `-W 1h30m` and fail; it needs the maintainer's nod as a new convention. If (b):
  beside the help include, after the existing 2.9.0 markers — `docs/cli/submit.rst:26`,
  `server.rst:22`, `client.rst:25`, `cluster.rst:25`; in `_include/config_param_ref.rst` only inside
  `.. only:: html` (that file is in the man page via `manual.rst:247`; pattern at `:411-414`); and
  at the rotate prose in `docs/logging.rst:358`.
- *2.8 cherry-pick:* expect conflicts in `docs/whats_new.rst` (modify/delete — resolve with
  `git rm docs/whats_new.rst`) and, if (b), in `docs/cli/*.rst` (2.8.6 lacks the 2.9.0 marker block
  adjacent to the insertion — drop those hunks; master docs are not published). Keep any marker out
  of `_include/*_help.rst` (never hand-marked) so the snippets apply clean.

## 2. Where each option and tag is documented

Help snippets are **hand-maintained**, not generated: nothing in the repo references
`*_help.rst` except the `.. include::` lines; `test_completions.py` only lints zsh bracket escaping.

| Item | Source `APP_HELP` (develop / 2.8.6) | Docs |
|---|---|---|
| submit `-m`, `-W` | `submit.py:1242-1243` / `888-889` | `_include/submit_help.rst:101-112` (examples `'4GB','512MB'`, `memory:8GB`, `timeout:3600`) |
| server `-m`, `-W` | `server.py:1564-1565` / `1359-1360` ("Memory in bytes", "in seconds") | `_include/server_help.rst:84-101` |
| client `-M`, `-T`, `-W` | `client.py:1568-1570` / `1501-1503` | `_include/client_help.rst:101-128` (`-M` metavar is *SIZE* here) |
| cluster `-m`, `-M`, `-T`, `-W` | `cluster/__init__.py:103,105,109,110` / `96,98,102,103` | `_include/cluster_help.rst:250-288` |
| inline `memory:`/`timeout:` | — | only the examples above, `submit_desc.rst:17` (generic "resource limits or tags inline") and `config_param_ref.rst:443`; no tag reference page exists |
| config `task.memory`, `task.timeout` | — | `config_param_ref.rst:439-451` ("in bytes"; timeout has no unit) |
| config `client.memory`, `client.timeout` | — | `config_param_ref.rst:365-384` |
| log rotate | — | `config_param_ref.rst:69-73`, `logging.rst:358-369` (both say `KB`/`MB`/`GB`/`TB` only; cron example `0 1 * * 0` is digit-leading) |

Out of scope (non-goal options): relay `[site] .timeout` `config_param_ref.rst:640`; usage lines
(`[-W SEC]`) may keep `SEC` or change metavar — `submit.py:1208`, `server.py:1528`,
`client.py:1528`, `cluster/__init__.py:57`.

**Man pages:** built by Sphinx (`-b man`) from `docs/manual.rst`, which includes every
`_help.rst`/`_desc.rst` and `config_param_ref.rst`. They are regenerated at release Step 4
(`SKILL.md:181-188`) — the 2.8.4/2.8.5 bump commits absorbed `_help.rst` content (13- and 5-line man
diffs despite Step 4's "only `.TH`" check). Precedent fixes did **not** touch `share/man`; don't.
**Completions:** `share/zsh/site-functions/_hs:480-481,520-522,563-564,620-625` and
`share/bash_completion.d/hs:558-559,591-601,668-669,722-738` offer `1GB…32GB` and bare seconds —
still valid. Only zsh descriptions "Memory in bytes" (`:563`) and "…in seconds" (`:564`) go stale;
optional, and they cherry-pick cleanly if edited alone.

## 3. Will docs hunks apply on 2.8.6?

Same text on 2.8.6, no develop hunk within 3 lines → **clean**: every memory/timeout paragraph in
`submit_help`, `server_help`, `client_help`, `cluster_help`; `config_param_ref` rotate
(`2.8.6:70-73`), client `.timeout/.memory` (`2.8.6:323-346`) and task `.memory/.timeout`
(`2.8.6:385-402`) blocks byte-identical; `logging.rst` rotate prose identical (`2.8.6:336-348`).
`APP_HELP` blocks identical in server, client, cluster. **Submit `APP_HELP` differs**: develop has
`--zone` at `submit.py:1245`, one line after `-g`; editing only the `-m`/`-W` lines leaves `-g` as
the unchanged separator — don't touch `-g`. Code note for 01/02: `data/model.py:530` (`timeout`
line) sits directly above develop-only fingerprint code (`:531`), unlike `2.8.6:352` — an edit there
is adjacent to a develop change and may conflict. `core/types.py` is identical on both lines.

## 4. Docs verify command

CI (`.github/workflows/tests.yml:215`) and recent TECH verify lines
(`spec/docs-version-markers/TECH.md:21`):
`uv run --python 3.12 --group docs sphinx-build -W --keep-going -q -E -b html docs docs/_build/html`.
Master has no docs CI job (`2.8.6:.github/workflows/tests.yml`: no sphinx).

Gap: `whats_new.rst:8` still calls 2.8.5 "the current stable release" (2.8.6 is out) — out of scope.
