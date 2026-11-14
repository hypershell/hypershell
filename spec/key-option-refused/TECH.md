---
slug: key-option-refused
title: Refuse the queue key on a command line
kind: fix
appetite: small
status: in_review
branch: fix/key-option-refused
base: develop
current_phase: done
last_updated: '2026-11-13'
phases:
- id: P1
  name: Tests, bench and drives give the key in the environment; submit -q per key
    source
  status: done
  satisfies:
  - R4
  - R8
  depends_on: []
  parallel: false
  hammerable: false
  hill: downhill
  verify: 'uv run pytest -v tests/test_auth_variants.py tests/test_cluster_launch.py
    tests/test_dispatch_workload.py tests/test_dispatch_staged.py tests/test_dispatch_version.py
    tests/test_queue_start.py tests/test_queue_workload.py tests/test_queue_limits.py
    tests/test_relay_workload.py tests/test_relay_outage.py tests/test_topology_server.py
    tests/test_upgrade_cli.py tests/test_zone_workload.py && uv run python -m py_compile
    tests/bench/bench_queue.py spec/relay/drive/relay_throughput.py && ! git grep
    -n -E -e "(^|[^[:alnum:]_-])-k([^[:alnum:]_-]|$)" -e "--auth([^[:alnum:]_-]|$)"
    -- tests "spec/*/drive/*" | grep -v -e "^tests/test_relay_launch.py:" -e "^tests/test_factory_fsm.py:"
    -e "^tests/test_server.py:" -e "^spec/relay/drive/relay_probe.py:" -e KEY_OPTIONS
    -e "-k.: True, .--auth.: True"'
- id: P2
  name: Refuse -k/--auth on hs client, server and submit; help, hints, docs, completions;
    refusal tests and sweep
  status: done
  satisfies:
  - R1
  - R2
  - R3
  - R4
  - R5
  - R6
  - R7
  - R8
  depends_on:
  - P1
  parallel: false
  hammerable: false
  hill: downhill
  attempts: 1
  verify: uv run pytest -v tests/test_key_option.py tests/test_server.py tests/test_client.py
    tests/test_submit.py tests/test_auth_variants.py tests/test_completions.py tests/test_cluster_launch.py
    tests/test_relay_launch.py && .agents/factory/bin/temp_site.sh sh -c "HYPERSHELL_LOGGING_FILE=enabled
    uv run hs server --forever --auth=abcdefghijklmnop0123 2> e.txt; test \$? -eq
    2 && grep -q HYPERSHELL_SERVER_AUTH e.txt && grep -q server.auth e.txt && ! grep
    -rq abcdefghijklmnop0123 ." && UV_PROJECT_ENVIRONMENT=$TMPDIR/docs-venv uv run
    --python 3.12 --group docs sphinx-build -q -E -b man docs $TMPDIR/key-option-man
    && ! cat $TMPDIR/key-option-man/*.1 | tr -d "\\134" | grep -n -E -e "(^|[^[:alnum:]-]|f[BIPR])-k([^[:alnum:]-]|f[BIPR]|$)"
    -e "--auth([^[:alnum:]_-]|f[BIPR]|$)" && UV_PROJECT_ENVIRONMENT=$TMPDIR/docs-venv
    uv run --python 3.12 --group docs sphinx-build -W --keep-going -q -E -b html docs
    $TMPDIR/docs-html
review:
  last_reviewed_commit: fcd3ba759d1f5bc998ae916f27b9d58c41969685
  verdict: changes-requested
  blocked_reason: F2 versionchanged gaps (R6); F1 -k registration breaks cluster/relay
    option values (R4); F3, F5
  cycle: 1
---
# TECH.md — Refuse the queue key on a command line

The **context engine and finite-state machine** for building this fix. The YAML frontmatter above is
the resume ground-truth (read it with
`uv run python .agents/factory/bin/next_phase.py spec/key-option-refused/TECH.md`); the per-phase
checklists below are the work. `hs-build` executes the next actionable phase, runs its `verify:`
command, updates state via `uv run python .agents/factory/bin/set_phase.py …`, and makes one atomic
code+state commit.

- **Vision / requirements (locked):** [`GOAL.md`](GOAL.md) — R-IDs are the contract.
- **Authoritative design:** [`PLAN.md`](PLAN.md).
- **Backing research:** [`research/00-digest.md`](research/00-digest.md) + briefs.

## Frontmatter field reference

- `status` (top): `planned | in_progress | blocked | in_review | done` (`done` is stamped by
  `hs-publish` after confirmation, just before landing — the terminal state of the retained record)
- `appetite`: `small | big` — caps phase count and build-iteration budget (circuit breaker).
- phase `status`: `pending | in_progress | done | blocked`
- `satisfies`: GOAL R-IDs this phase delivers (traceability anchor for `hs-review`).
- `depends_on`: phase ids that must be `done` first (a phase is actionable only when met).
- `parallel`: `true` only for genuinely independent, non-coupled work. Both phases here are `false`:
  P2 touches `client.py`, `server.py` and `core/config.py`, and P1 must land before P2.
- `hammerable`: `false` marks a correctness/security phase that scope-hammering must **never** cut.
- `hill`: `uphill` → `crest` → `downhill`. A phase stuck `uphill` across builds is a raised hand.
- `attempts`: durable failed-verify counter (absent = 0), bumped by `set_phase.py --phase P<n>
  --record-attempt` on every red verify gate; `next_phase.py` warns at ≥3.
- `verify`: the exact command that proves the phase.
- `review.cycle`: completed review passes, auto-incremented by every `set_phase.py --verdict …`.

## Conventions (apply to every phase)

- Commit conventions, code style, and load-bearing invariants come from
  [`AGENTS.md`](../../AGENTS.md). Invariant sections in play: §9, §10, §11, §12
  ([`invariants.md`](../../.agents/factory/invariants.md)).
- One phase per `hs-build` invocation; one atomic commit containing **both** the code and the
  `TECH.md` state change, subject `[fix] Build key-option-refused P<n>: …`. **No `Co-Authored-By`
  trailer.**
- P2 is a CLI change: its `docs/_include/*.rst` snippets, the `share/` completions and the prose
  passages (`.. versionchanged:: 2.9.0`) go **in the same commit**. The user-facing note is drafted
  by `hs-publish` for the squash body, not written into a docs file.
- **Public repository.** No internal host or cluster names. No example, test fixture or doc puts a
  key on a command line; tests that must pass the option to prove the refusal live in
  `tests/test_key_option.py` and use a dummy key.
- **Running the verify commands.** Set `UV_CACHE_DIR` under `$TMPDIR`. The two `sphinx-build` runs
  use their own environment (`UV_PROJECT_ENVIRONMENT=$TMPDIR/docs-venv`) so the worktree's `.venv`
  is not replaced by a 3.12 one, and need the sandbox off (`sphinx_sitemap`).
- `share/man/` is **not** regenerated (GOAL gate); `hs-release` does that at the next release.

---

## Phase P1 — Tests, bench and drives give the key in the environment
**Satisfies:** R4, R8 · **Depends on:** —
**Goal:** every test, bench and drive that starts a server or client by hand passes its key in
`HYPERSHELL_SERVER_AUTH`, green on today's code, so the option can go without breaking the suite;
`hs submit -q` gains a test per key source.

- [x] Convert each `'-k', key` (or `'-k', AUTH`, `site.key`, `SERVER_KEY`, `secrets.token_hex(16)`) to
      the same key in `HYPERSHELL_SERVER_AUTH` in the process environment, through the helper's
      existing `env` argument where it has one (`group.start(..., env=…)`, `Popen(..., env=…)`), or
      by adding one: `test_dispatch_workload.py`, `test_dispatch_staged.py` (the shared address
      list at :97 becomes an environment), `test_dispatch_version.py`, `test_queue_start.py`,
      `test_queue_workload.py`, `test_queue_limits.py`, `test_relay_workload.py`,
      `test_relay_outage.py`, `test_topology_server.py`, `test_upgrade_cli.py`,
      `test_zone_workload.py`, `test_cluster_launch.py` (`start_server`), `tests/bench/bench_queue.py`,
      `spec/relay/drive/relay_throughput.py:170`. Where a server and its clients share one key, set it
      once for the group.
- [x] `test_cluster_launch.py::test_client_names_missing_key_only_when_none_given`: drop the
      `explicit-default-key` and `explicit-wrong-key` cases; their `environment-*` and `config-*`
      twins stay.
- [x] `test_auth_variants.py::test_command_submit_queue`: parametrise over `RESOLVED_FORMS`
      (`auth`, `auth_env`, `auth_eval`, environment), server and submit sharing the form.
- [x] Leave untouched: `KEY_OPTIONS` checks, the `CONTRACT_CLIENT_OPTIONS` row,
      `tests/test_relay_launch.py`, `spec/relay/drive/relay_probe.py`, `tests/test_factory_fsm.py`,
      and `tests/test_server.py` (its `-k` rows change with the behaviour, in P2).
- **Verify:** the converted files pass, the bench and drive compile, and a grep of `tests/` and
  `spec/*/drive/*` finds no key option outside those exceptions (frontmatter).
- **Touches:** `tests/…`, `tests/bench/bench_queue.py`, `spec/relay/drive/relay_throughput.py`.

## Phase P2 — Refuse the option; its surface; the refusal tests and the sweep
**Satisfies:** R1, R2, R3, R4, R5, R6, R7, R8 · **Depends on:** P1
**Goal:** `-k`/`--auth` on the three commands is a parse-time usage error (exit 2) that names
`HYPERSHELL_SERVER_AUTH` and `server.auth` and never the key; the key is read from `resolve_auth`
alone; nothing in help, hints, docs, man pages or completions offers the option. `hs cluster`
(and `hsx`) and `hs relay` keep `develop`'s parsers, with no key option (review-cycle-1 ruling,
which withdraws plan-gate ruling 3; see the remediation below).

- [x] `core/config.py`: `KEY_OPTION_STRINGS`, `RefuseKeyOption(argparse.Action)` raising
      `cmdkit.cli.ArgumentError` with a message built from the matched option string (never
      `values`), and `refuse_key_option(interface, hint='')` registering it with `nargs='?'`,
      `default=argparse.SUPPRESS` (PLAN §2.1). One name for the variable: reuse the existing
      `AUTH_ENV` value without an import cycle (move the constant to `core/config.py` and keep
      `hypershell.client.AUTH_ENV` importable, or spell it once there).
- [x] `client.py`, `server.py`, `submit.py`: replace the `-k/--auth` `add_argument` with
      `refuse_key_option(interface)` (`SubmitApp` with the `--` hint); read the key with
      `resolve_auth('server')` unconditionally in `run()`; client `no_key` drops `auth_given`.
- [x] ~~`cluster/__init__.py` and `relay/__init__.py`: register the refusal (plan-gate ruling 3).~~
      Withdrawn at review cycle 1: both files are `origin/develop`'s again.
- [x] Usage and help text of the three commands (R5): no `[-k KEY]`, no `-k, --auth` line; the
      submit `-q` line and the client/server help prose say where the key comes from. Client hints
      at `client.py:1149-1160` (and the comment at `:1667`) name `server.auth` instead of `-k`.
- [x] Docs (R6): `docs/_include/{client,server,submit}_{usage,help}.rst`; `getting_started.rst`
      (key from the environment or a configuration file; never on a command line), `security.rst`
      (:174, :187, :524), `install.rst` (:473 bullet goes), `_include/templates.rst:213`,
      `_include/security_alt.rst:13`. Each behavioural passage gets `.. versionchanged:: 2.9.0`,
      worded without the option's spelling (the man page must not hold it).
- [x] Completions (R7): Bash `share/bash_completion.d/hs` (submit, client, server blocks) and zsh
      `share/zsh/site-functions/_hs` (:475, :510, :557); keep `_hs_authkey` for `config set`.
- [x] `tests/test_server.py` `OPTION_PAIR`: drop the `-k`/`--auth` rows.
- [x] New `tests/test_key_option.py` (PLAN §2.4): unit refusal table per app (every form from
      research 01, plus `--` and `--template=-kfoo` parsing as before); integration per command ×
      the four GOAL forms with a DEVEL log file (exit 2, both sources named, key in no output or
      site file, no database file); R3 for `hs submit -- echo -k X` and a task file; the R5–R8
      sweep with its allow list. The unit and integration refusal tests cover `hs client`,
      `hs server` and `hs submit`.

**Review-cycle-1 remediation** (REVIEW.md F1, F2, F3, F5; PLAN.md "Review-cycle-1 rulings"):

- [x] F1, F3: `src/hypershell/cluster/__init__.py` and `src/hypershell/relay/__init__.py` restored
      from `origin/develop` (no `-k`/`--auth`, no refusing action, no advice text). Their refusal
      cases leave `tests/test_key_option.py`; `docs/security.rst` no longer says that every command,
      or the relay, refuses the option. A regression test pins that `--launcher-args '-k --exclusive'`
      (relay), `--ssh-args '-k -i id'`, `-t '-k {}'`, `--launcher '-k srun'` and `--remote-exe '-k x'`
      (cluster) parse as values and `--aut fixed` is `--autoscaling fixed`.
- [x] F2: `.. versionchanged:: 2.9.0` beside the changed includes in `docs/cli/{client,server,submit}.rst`,
      after the hand-started client sentence in `docs/_include/templates.rst`, and after the
      completion examples in `docs/install.rst`.
- [x] F5: R3 on `hs server` (a task file whose task carries `-k`) and `hs client` (a template
      carrying `-k`), end to end, beside the existing `hs submit` tests.
- **Verify:** the tests above; a `temp_site` drive of `hs server --auth=KEY` with a log file (exit 2,
  both sources named, key nowhere under the site); a `-b man` build into `$TMPDIR` that greps clean;
  the HTML build with `-W` (frontmatter).
- **Touches:** `src/hypershell/core/config.py`, `client.py`, `server.py`, `submit.py`,
  `docs/_include/…`, `docs/cli/…`, `docs/{getting_started,security,install}.rst`, `share/…`, `tests/…`.

---

## How `hs-build` drives this

1. `next_phase.py` prints the next actionable phase (statuses are authoritative; the
   `current_phase` pointer is reconciled against them).
2. Pre-flight: clean tree, on `branch`, `base` reachable.
3. Execute every `[ ]` in the phase (consult `PLAN.md` / `research/` for detail).
4. Run the phase's `verify:` command — never advance on a checkbox alone.
5. Amend this file freely if reality diverges (regenerate frontmatter with `set_phase.py`; note the
   amendment in the commit body). STOP and escalate only on a **`GOAL.md` contradiction**.
6. Mark the phase `done`, advance `current_phase`, `--touch`; one `[fix]` commit; stop and report.
