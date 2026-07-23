---
name: hs-release
description: >-
  Human-gated cutter of HyperShell versions — the operational sibling of hs-harness that fills the gap
  hs-publish leaves ("version bumps / master releases are a separate concern... out of scope here").
  Three modes: `patch` (hotfix off master — cherry-pick + merge to master + back-merge), `pre-release`
  (alpha/beta/rc off develop; master never touched), and `full-release` (promote develop → master via a
  short-lived release/X.Y.0 branch). Shared core: bump the single version source (pyproject.toml) +
  `uv lock`, rebuild the 3 man pages, run the CI-mirror gate (pytest → uv build → twine check --strict
  → sdist hygiene), sign an annotated tag, then — only after an explicit human OK before the first
  irreversible step — push + `gh release create`, and verify PyPI / ghcr / Actions. Rehearses every
  branch op + the full gate in an isolated git-worktree dry-run first. Operational, NOT a lifecycle
  step: never writes META findings, never recurses, never weakens a gate.
disable-model-invocation: true
argument-hint: "<patch|pre-release|full-release> <version> [<sha>… for patch] [--skip-dry-run] | status"
allowed-tools: Read, Edit, Grep, Glob, AskUserQuestion, Bash(uv run *), Bash(uv sync *), Bash(uv lock *), Bash(uv build *), Bash(uvx twine *), Bash(git status *), Bash(git branch *), Bash(git rev-parse *), Bash(git log *), Bash(git show *), Bash(git diff *), Bash(git fetch *), Bash(git pull *), Bash(git switch *), Bash(git add *), Bash(git commit *), Bash(git merge *), Bash(git push *), Bash(git tag *), Bash(git cherry-pick *), Bash(git worktree *), Bash(gh release *), Bash(gh run *), Bash(gh repo *), Bash(cp *), Bash(tar *), Bash(curl *), Bash(mktemp *), Bash(head *), Bash(tail *), Bash(ls *)
---

# hs-release — cut a version (patch / pre-release / full-release), human-gated

## When to Use

Invoke `/hs-release` to bump the version and cut a release — the concern `/hs-publish` explicitly
leaves out ("Version bumps / `master` releases are a separate concern (git-flow release), out of scope
here"). It is an **operational sibling of `/hs-harness`, NOT a lifecycle step**: it touches no `spec/`,
no FSM, no `GOAL/PLAN/TECH/REVIEW`; it moves the version, refs, and tags and publishes artifacts. This
is the one place refs move on `master` and where **irreversible, permanent** outward publishes happen
— a version string on PyPI can **never** be reused — so it always confirms before the first push and
rehearses everything in an isolated git worktree first.

Reference: [`factory/invariants.md`](../../factory/invariants.md) §12 (version single-sourced;
`share/` + the CI metadata list in lockstep; `twine check --strict`), the "Packaging & release" section
of [`AGENTS.md`](../../../AGENTS.md), and this skill (it codifies the maintainer's hotfix and
pre-release procedures — this file is now their ground truth).

**Harness portability.** Runs on any harness — see [`factory/portability.md`](../../factory/portability.md).
Fallbacks: run the *Current state* commands yourself if not auto-injected; ask in plain text and STOP
if `AskUserQuestion` is unavailable. `git` / `gh` / `uv` are portable shell, and the worktree dry-run
is plain `git worktree` (available everywhere) — no Claude-specific affordance is load-bearing here.

## User Instructions

Additional instructions provided with the invocation: $ARGUMENTS

## Current state (injected at load)

- Branch: !`git branch --show-current`
- Tree (must be clean): !`git status --porcelain | head -n 20`
- Version (pyproject.toml — the only source): !`head -n 5 pyproject.toml`
- Recent tags: !`git tag -l --sort=-v:refname | head -n 8`
- master / develop tips: !`git log --oneline -1 master 2>/dev/null; git log --oneline -1 develop 2>/dev/null`
- Default remote branch: !`gh repo view --json defaultBranchRef -q .defaultBranchRef.name 2>/dev/null || echo "(gh unavailable)"`

(PyPI/ghcr are checked in the post-publish verify step, not at load — they are network probes.)

## Argument Parsing

Parse `$ARGUMENTS` case-insensitively for the mode/flags (the version and SHAs are case-sensitive). If
self-contradictory or ambiguous, STOP and ask.

- **Mode** (first positional, required): `patch` | `pre-release` | `full-release`. Missing → STOP and
  offer the three via `AskUserQuestion`.
- **`status`** as the sole token → Step 0 fast-path (no work).
- **Version** (second positional, required — **never inferred/auto-bumped**; a permanent string is too
  dangerous to guess): validate PEP 440, no `v` prefix, **no hyphen in prereleases** (`2.9.0a1`, not
  `2.9.0-a1`), strictly greater than the latest tag (PEP 440 ordering, e.g. via
  `uv run python -c "from packaging.version import Version; ..."`), and not already a tag. **Mode/suffix
  consistency:** `pre-release` REQUIRES a prerelease suffix (`aN`/`bN`/`rcN`); `patch`/`full-release`
  REQUIRE a final version (no suffix). Any mismatch → STOP.
- **Patch SHAs** (all positional tokens after the version, in cherry-pick order): **required for
  `patch`** — validate each exists and is reachable from `develop` but NOT from `master` (an unreleased
  fix). None given for `patch` → STOP and ask which commits.
- **Flags:** `--skip-dry-run` opts out of Step 2 (must be explicit; discouraged, and **forbidden for
  `full-release`** — highest stakes, never exercised). Any unrecognized token → STOP and ask.
- **Infer from the mode (do not ask):** base branch, working branch, tag location, master-touched,
  back-merge policy, `--prerelease`. **Require explicitly:** mode, version, patch SHAs.

## Safety Principles

- **Confirm before every irreversible/outward step.** Push and PyPI/GitHub publish are permanent — a
  version string can NEVER be reused. Steps 1–6 are all reversible in-tree; the single Step 7
  `AskUserQuestion` gate precedes the first push. **No push without an explicit human OK.**
- **Dry-run first (default on).** Every branch op + the full gate is rehearsed in an isolated
  `git worktree` before a single real ref moves. `--skip-dry-run` requires an explicit flag and is
  forbidden for `full-release`.
- **`master` discipline is per-mode.** `pre-release` NEVER touches `master` and NEVER back-merges.
  `patch`/`full-release` move `master`, tag on the `master` **merge** commit, then back-merge
  `master → develop`. Always `--no-ff`; never fast-forward or force-push `master`.
- **The gate is non-negotiable.** `uvx twine check --strict dist/*` (the gate that caught the 2.8.0
  PyPI-render outage), `uv run pytest -q`, `uv build --no-sources`, and the sdist-hygiene check (no
  `.agents/`/`.local/`/`.security/`/`spec/` in the tarball) must ALL pass. A red gate is a STOP, never
  an override-to-ship.
- **Version is single-sourced.** Bump `pyproject.toml` only; `hypershell.__version__` reads it via
  `importlib.metadata` and `docs/conf.py` derives from it; `share/` man pages and the CI metadata list
  move in lockstep ([`invariants.md`](../../factory/invariants.md) §12). Never hardcode a version
  elsewhere.
- **Signed tags only.** `git tag -s` (GPG key 61AE0783), verified with `git tag -v` **before** any push.
- **Release notes are drafted, then confirmed.** Auto-draft from `git log <lasttag>..HEAD` grouped by
  `[category]`; present for human edit at Step 7; never publish unreviewed notes.
- **Never `rm`.** `dist/`, `sdist/`, `docs/_build/` are gitignored (leave them); `git worktree remove`
  cleans the rehearsal. Do not delete files.
- **Operational, not meta.** hs-release never writes `META.md` findings and never recurses; harness
  friction here goes to `/hs-harness`.
- **Branch discipline + no co-author.** Work on the mode's branch (`hotfix/` / `release/` / `develop`);
  use `[release]` subjects on the bump / merge / back-merge commits; never force-push. **No
  `Co-Authored-By` trailer** on any commit.

## Procedure

The **shared core** is Steps 4–5 (bump + man pages, then the gate); the three modes branch at Step 3
(branch setup), Step 6 (where the tag lands), and Step 9 (back-merge or not):

| mode | base | working branch | tag lands on | master moved | back-merge → develop | `gh release` | `:latest` / PyPI-latest |
|---|---|---|---|---|---|---|---|
| **patch** | `master` | `hotfix/X.Y.Z` (cherry-pick fix SHAs) | the `master` merge commit | yes (`--no-ff`) | yes | *(none — stable)* | moves — **but no container until `docker.yml` reaches `master`** |
| **pre-release** | `develop` | `develop` (in place) | the `develop` bump commit | **no** | **no** | `--prerelease` | PyPI stays on last stable (pip needs `--pre`); ghcr `latest`/`{{major}}`/`{{major}}.{{minor}}` suppressed → only `:X.Y.ZaN` + `:sha-<short>` |
| **full-release** | `develop` | `release/X.Y.0` off develop | the `master` merge commit | yes (`--no-ff`) | yes | *(none — stable)* | moves |

### Step 0 — status fast-path (when requested)
`status` (or no meaningful args): report the current version, the latest tags, `master`/`develop`
tips, whether the intended target is already a tag, and whether a release looks in-flight (a
`hotfix/`|`release/` branch, or an unpushed local tag). Stop.

### Step 1 — Parse + pre-flight
Parse `$ARGUMENTS` (see Argument Parsing) → resolve mode, base branch, version, patch SHAs. Require a
**clean tree** (`git status --porcelain` empty → else STOP: commit/stash first). `git fetch origin`;
confirm the base branch tip matches `origin/<base>` (diverged → STOP). STOP if the target version is
already a tag or is not strictly greater than the latest tag, or if the mode/suffix rule is violated.

### Step 2 — Worktree dry-run (default ON; `--skip-dry-run` opt-out, forbidden for full-release)
Rehearse the ENTIRE release in isolation before any real ref moves:
1. `dir=$(mktemp -d)` (outside the repo, so it never dirties the working tree — mirrors
   `factory/bin/temp_site.sh`); `git worktree add --detach "$dir/rel" <base>` (**`--detach`** — for
   `pre-release` the base `develop` is already checked out in the main tree, and git refuses to check
   out the same branch twice; a detached worktree at the base commit sidesteps that and needs no
   branch of its own).
2. In that worktree, replay the mode's branch ops (Step 3: cherry-pick / branch), the shared core
   (Step 4: bump + man rebuild) and the **full gate** (Step 5: pytest + `uv build --no-sources` +
   `uvx twine check --strict` + sdist hygiene).
3. For **patch/full-release** also test-merge the result into a throwaway copy of `develop` to prove
   the Step 9 back-merge applies without surprise conflicts.
4. `git worktree remove "$dir/rel"`. Any red → STOP and report; **nothing in the real tree moved.**

Portability: this is plain `git worktree` + the same gate commands — no Claude-specific affordance. If
the *Current state* wasn't auto-injected, run the probes yourself first. (A few permission prompts may
appear for commands run against the `/tmp` worktree path; that is expected and harmless.)

### Step 3 — Mode branch setup (real tree)
- **patch:** `git switch -c hotfix/X.Y.Z master`; `git cherry-pick <sha…>` in argument order (master
  is a strict ancestor of develop, so this pulls only released code + the fix).
- **pre-release:** stay on `develop` (the bump commit and tag land here).
- **full-release:** `git switch -c release/X.Y.0 develop`.

### Step 4 — Shared core: bump + man pages + commit
1. Edit the `version = "…"` line in `pyproject.toml` → X.Y.Z (the ONLY source).
2. `uv lock` (updates the `hypershell` entry in `uv.lock`).
3. Rebuild man pages (manual — no helper exists): `uv run sphinx-build -b man docs docs/_build/man`
   (`uv run` auto-syncs, so the new version installs first and the `.TH` line updates), then
   `cp docs/_build/man/hs.1 share/man/man1/hs.1`, `cp docs/_build/man/hyper-shell.1
   share/man/man1/hyper-shell.1`, and `cp share/man/man1/hs.1 share/man/man1/hsx.1` (`hsx.1` is a
   byte-copy — `docs/conf.py` has no `hsx` entry).
4. **Verify** `git diff -- share/man/man1` shows ONLY the `.TH` version/date line (a version-only bump;
   CLI content is already current on the branch).
5. Commit `[release] Bump version to X.Y.Z and rebuild man pages`, staging **exactly** these 5 files:
   `pyproject.toml`, `uv.lock`, `share/man/man1/{hs,hsx,hyper-shell}.1`.

### Step 5 — Gate (mirrors CI; non-negotiable)
Run all of: `uv run pytest -q`; `uv build --no-sources`; `uvx twine check --strict dist/*`; sdist
hygiene — `tar tzf dist/*.tar.gz` must NOT contain `.agents/`, `.local/`, `.security/`, or `spec/`.
Any failure → STOP (never override-to-ship). `dist/` is gitignored; leave it (do not `rm`).

### Step 6 — Land the release commit + signed tag (per mode)
- **patch:** `git switch master && git merge --no-ff hotfix/X.Y.Z -m "[release] Merge hotfix/X.Y.Z"`;
  tag on the **merge commit**.
- **full-release:** `git switch master && git merge --no-ff release/X.Y.0 -m "[release] Merge
  release/X.Y.0"`; tag on the **merge commit**.
- **pre-release:** no merge; tag on the **develop bump commit** (current HEAD).

Then `git tag -s X.Y.Z -m "HyperShell X.Y.Z"` (GPG 61AE0783) and `git tag -v X.Y.Z` to verify the
signature **before anything is pushed**. Signing/verify failure → STOP.

### Step 7 — PAUSE: confirm before anything irreversible
Everything so far is reversible in-tree. Draft the release notes from `git log <lasttag>..HEAD` grouped
by `[category]` (dedupe entries that appear under multiple SHAs from back-merges; link `#NN` issues if
present). Then present via `AskUserQuestion`: the mode, version, the branch/tag layout, `--prerelease`
yes/no, whether `:latest` / PyPI-latest will move, the drafted notes (human-editable), and the exact
push/publish commands. **Nothing is pushed until an explicit OK.** `AskUserQuestion` unavailable → ask
in plain text and STOP.

### Step 8 — Push + publish
Push the branch, then the tag (the tag must be on the remote before `--verify-tag`):
- **patch/full-release:** `git push origin master` then `git push origin X.Y.Z`.
- **pre-release:** `git push origin develop` then `git push origin X.Y.Z`.

Then `gh release create X.Y.Z --verify-tag [--prerelease] --title "HyperShell X.Y.Z" --notes-file
<file>` (`--prerelease` for pre-release mode only). This fires `publish.yml` (→ PyPI) and, when the
tagged commit's tree carries it, `docker.yml` (→ ghcr).

### Step 9 — Back-merge (patch & full-release only; pre-release skips this entirely)
`git switch develop && git merge --no-ff master -m "[release] Merge branch master (X.Y.Z) into
develop"`. Expect conflicts **only** on version / `uv.lock` / man pages — resolve to **develop's
superset content** (its CLI is the more advanced side), then re-run `uv lock` + rebuild the man pages
to re-stamp develop's CLI content at X.Y.Z; commit the resolution; push `develop`.

### Step 10 — Verify after publish
- **PyPI:** `curl -s https://pypi.org/pypi/hypershell/json` — `info.version` is the intended "latest"
  (unchanged from the prior stable for a pre-release; the new version for patch/full-release), and the
  new version appears under `releases`. (`gh api` is a fallback if `curl` is unavailable.)
- **ghcr:** fetch an anonymous pull token
  (`curl -s "https://ghcr.io/token?scope=repository:hypershell/hypershell:pull"`), then
  `GET /v2/hypershell/hypershell/tags/list` — confirm `:latest` moved (patch/full-release) or is
  ABSENT/unmoved and only `:X.Y.ZaN` + `:sha-<short>` published (pre-release).
- **Actions:** `gh run list` / `gh run watch` for `publish.yml` + `docker.yml` success.
- Confirm branch tips (`master`/`develop`) and the tag point where intended.

### Step 11 — Report
Mode, version, tag SHA + signature status, what published where (PyPI / ghcr / GitHub release URL),
`:latest` / PyPI-latest state, back-merge result (or "n/a — pre-release"), CI run URLs, and any caveat
— especially the **patch container gap** (no image until `docker.yml` reaches `master`).

## Examples

- `/hs-release patch 2.8.2 a1b2c3d` — hotfix off `master`, cherry-pick `a1b2c3d`, publish, back-merge
  to `develop`.
- `/hs-release pre-release 2.9.0a1` — alpha off `develop`; `master` untouched; `--prerelease`.
- `/hs-release full-release 2.9.0` — promote `develop` → `master` via `release/2.9.0`; `:latest` moves.
- `/hs-release status` — current version, tags, branch tips, in-flight check; no changes.
- `/hs-release patch 2.8.3 <sha> --skip-dry-run` — skip the worktree rehearsal (discouraged).

## Notes

- **Reference `invariants.md` §12, don't duplicate it** (single-source version; `share/` + CI metadata
  lockstep; `twine --strict`). This skill introduces no new numbered invariant.
- **patch container caveat:** `docker.yml` lives on `develop` only, so a master-sourced patch release
  builds **no** container image until `docker.yml` reaches `master` (which happens at the next
  full-release). State this in the Step 11 report.
- **pre-release semantics recap:** PyPI keeps the last stable as `info.version` (pip needs `--pre` or an
  exact `==X.Y.ZaN` pin); GitHub "Latest" stays on the stable; ghcr suppresses `latest`/`{{major}}`/
  `{{major}}.{{minor}}` for PEP 440 prereleases (`docker.yml` uses `type=pep440`). The `release` event
  runs `docker.yml` from the **tagged commit's tree**, so develop's workflow is what runs.
- The maintainer's hotfix + pre-release process notes are codified here; this SKILL.md is their ground
  truth.
- Never `rm`; `dist/`/`sdist/`/`docs/_build/` are gitignored, and `git worktree remove` cleans the
  rehearsal.
