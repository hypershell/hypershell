---
slug: launch-hint-followups
title: 'Launch-path follow-ups: accurate no-key hints, template docs, an export-name
  test'
kind: fix
appetite: small
status: in_review
branch: fix/launch-hint-followups
base: develop
current_phase: done
last_updated: '2026-11-05'
phases:
- id: P1
  name: No-key hints only when no key reached the client
  status: done
  satisfies:
  - R1
  - R2
  - R3
  depends_on: []
  parallel: false
  hammerable: false
  hill: downhill
  verify: uv run pytest -n auto tests/test_cluster_launch.py -k 'names_missing_key
    or names_cause_under_tls'
- id: P2
  name: Test the export-name check, and kill its mutant
  status: done
  satisfies:
  - R5
  depends_on: []
  parallel: true
  hammerable: false
  hill: downhill
  verify: uv run pytest -n auto tests/test_cluster_launch.py -k shell_cannot_assign
- id: P3
  name: Accurate template docs and the approved security sentence; final gates
  status: done
  satisfies:
  - R4
  - R6
  depends_on:
  - P1
  - P2
  parallel: false
  hammerable: false
  hill: downhill
  verify: 'uv run pytest -n auto && uv run sphinx-build -W --keep-going -E -q docs
    docs/_build 2>&1 | grep WARNING  # expect exactly the two known toctree warnings'
review:
  last_reviewed_commit: de626171d4d77b4a0fabcf8574fcb97427bfb723
  verdict: approved
  blocked_reason: ''
  cycle: 2
---
# TECH.md — Launch-path follow-ups: accurate no-key hints, template docs, an export-name test

The **context engine and finite-state machine** for building this fix. The YAML frontmatter above is
the resume ground-truth (read it with `uv run python .agents/factory/bin/next_phase.py
spec/launch-hint-followups/TECH.md`); the per-phase checklists below are the work.

- **Vision / requirements (locked):** [`GOAL.md`](GOAL.md) — R-IDs are the contract.
- **Authoritative design:** [`PLAN.md`](PLAN.md). No `research/` (small appetite; PLAN §4 records
  the targeted reads and probes).

## Conventions (apply to every phase)

- Invariants and code style come from [`AGENTS.md`](../../AGENTS.md) /
  [`invariants.md`](../../.agents/factory/invariants.md). Touched sections: **§9** (auth rules
  untouched; advisory text only), **§10** (reads the resolved config value), **§11** (observed, not
  changed), **§12** (tagged, parametrized tests; no CLI surface change).
- One atomic commit per phase, code and `TECH.md` state together. Subjects:
  `[fix] Build launch-hint-followups P<n>: …`. Signed, no trailer.
- **P3's Sphinx gate:** `-W` turns the two known pre-existing "document isn't included in any
  toctree" warnings (`cli/task_submit.rst`, `manual.rst`) into a non-zero exit, as on `develop`
  (checked at `d2a460f`). The gate is: those two warnings and no other.

---

## Phase P1 — No-key hints only when no key reached the client
**Satisfies:** R1, R2, R3 · **Depends on:** —
**Goal:** a client whose key came from a configuration file never gets the no-key hint, and a
client with no key from any source still does.

- [x] Red first: add the failing cases to `tests/test_cluster_launch.py`.
  - `test_client_names_missing_key_only_when_none_given` gets a `config-wrong-key` case: the key
    comes from a configuration file named by `HYPERSHELL_CONFIG_FILE`, so expect no hint.
  - `test_launched_client_without_environment_names_cause_under_tls` gets a `key-in-config` case:
    the bare-environment launcher writes the run's key into a configuration file in the client's
    own home, so expect no hint.
  - Run them on the unchanged `client.py` and record the result below.
- [x] `ClientApp.run`: compute `no_key` once (no `-k`, no `HYPERSHELL_SERVER_AUTH`, resolved key
  still `DEFAULT_AUTH`) and use it in both handlers.
- [x] Re-run: every case passes. That covers the existing no-key, `-k` and environment cases too
  (R3).
- **Verify:** `uv run pytest -n auto tests/test_cluster_launch.py -k 'names_missing_key or names_cause_under_tls'`.
- **Touches:** `src/hypershell/client.py`, `tests/test_cluster_launch.py`.

## Phase P2 — Test the export-name check, and kill its mutant
**Satisfies:** R5 · **Depends on:** —
**Goal:** removing the `SHELL_NAME` skip in `ssh_env_exports()` fails a test.

- [x] Add `test_ssh_env_skips_names_a_shell_cannot_assign`, parametrized over a dot, a dash and a
  space in a `HYPERSHELL_*` name. It drives `hs cluster --ssh HOST --env` through the stand-in ssh
  and asserts four things: the name is on no command line, the warning is logged, the other exports
  arrive, and the run completes.
- [x] Run green; apply the mutant (the skip removed) and run again (it must fail); restore
  `launch.py` (`git diff` empty) and run green. Record all three below.
- **Verify:** `uv run pytest -n auto tests/test_cluster_launch.py -k shell_cannot_assign`.
- **Touches:** `tests/test_cluster_launch.py`.

## Phase P3 — Accurate template docs and the approved security sentence; final gates
**Satisfies:** R4, R6 · **Depends on:** P1, P2
**Goal:** `templates.rst` says what the probes in PLAN §4 show; `security.rst` carries the approved
wording; the full suite and the docs build pass.

- [x] `docs/_include/templates.rst`: rewrite the `{% %}` environment paragraph and the `{= =}`
  sentence per PLAN §2.3.
- [x] `docs/security.rst`: replace the sentence with the approved text, moved per PLAN §2.3 so that
  "This" follows what it refers to.
- [x] Full suite under `-n auto` (Python 3.14) and the Sphinx build. Record both below.
- **Verify:** `uv run pytest -n auto && uv run sphinx-build -W --keep-going -E -q docs docs/_build 2>&1 | grep WARNING`;
  green means the suite passes and the build lists exactly the two known toctree warnings (see Conventions).
- **Touches:** `docs/_include/templates.rst`, `docs/security.rst`.

---

## Evidence

Recorded as each phase runs. Python 3.14.6, worktree venv (`uv sync --frozen`), every run under a
perl process-group kill timer.

### P1 — red first, then green

- **Red** (new cases in `tests/test_cluster_launch.py`, `client.py` unchanged at `d2a460f`):
  `pytest -n auto tests/test_cluster_launch.py -k 'names_missing_key or names_cause_under_tls'`
  → **2 failed, 6 passed**. The two failures are exactly the new cases:
  - `test_client_names_missing_key_only_when_none_given[config-wrong-key]`: `AuthenticationError:
    digest sent was rejected - No key reached this client: HYPERSHELL_SERVER_AUTH is not set …`
    (hint present, expected absent).
  - `test_launched_client_without_environment_names_cause_under_tls[key-in-config]`:
    `SSLCertVerificationError: [SSL: CERTIFICATE_VERIFY_FAILED] … - No key reached this client
    either …` (hint present, expected absent).
  - The existing cases passed: `no-key` (hint present), `explicit-default-key`,
    `explicit-wrong-key`, `environment-wrong-key`, and under TLS `no-key` (hint present) and
    `key-kept`.
- **Green** (with `no_key` in `ClientApp.run`): the same command → **8 passed**.

### P2 — the export-name mutant

- **Green:** `pytest -n auto tests/test_cluster_launch.py -k shell_cannot_assign` → **3 passed**
  (`dot`, `dash`, `space`).
- **Mutant:** in `ssh_env_exports()`, the three lines of the `SHELL_NAME` skip (the `if`, its
  warning, and its `continue`) were removed. The same command → **3 failed**, each on the first
  assertion: `AssertionError: 'HYPERSHELL_PROBE.NAME' on the command line of ssh` (and the same for
  `-NAME` and ` NAME`). The mutant is killed.
- **Restored:** `git checkout -- src/hypershell/cluster/launch.py`; `git diff` on `src/` empty; the
  same command → **3 passed**.

### P3 — docs and final gates

- **R4, checked by execution** (PLAN §4 table, throwaway sites):
  - A `{% %}` command gets neither the `TASK_*` variables nor the key variable in any mode.
  - It gets an `[export]` value only under `--launcher`, where `ClientLaunch.start` has put the
    value in the client's own environment.
  - A `{= =}` lambda reads the key variable as `True` under `--launcher`, and as `False` under local
    `hs cluster` and under a hand-started `hs client -k`.
  - The SSH remote step leaves the key variable in the client process's environment.

  The new `templates.rst` text states exactly these.
- **R6:** the approved two sentences are verbatim. They now follow "… unless the configuration gives
  it explicitly.", so "This" refers to keeping the key out of task environments. "This applies to
  clients from this release on; …" moved with them. "There are two explicit ways:" became "A task
  can be given the key in two explicit ways:". The nested-run sentence stays after the bullets.
  (Amendment: PLAN §2.3 first had "The configuration can give a task the key …". That wording does
  not fit the `{= =}` bullet, so PLAN is updated to match.)
- **Full suite**, `pytest -n auto`, Python 3.14.6: **1005 passed, 88 skipped**, 0 failed. That is
  the cycle-3 review's 1000 passed plus the 5 new cases.
- **Sphinx** `-W --keep-going -E`: exit 1 with exactly the two known warnings (`cli/task_submit.rst`,
  `manual.rst`: "document isn't included in any toctree"). That is identical to `d2a460f`. Nothing
  comes from `security.rst` or `templates.rst`; both pages render the new text.

---

## Review-1 remediation

Review cycle 1 (`c1b9616`, reviewing `b80c6b2`) requested changes: F1 and F2, both LOW. The
foreman accepted both as the reviewer proposed. P1 (R3) and P3 (R4) were reopened and closed again
through `set_phase.py`, and the FSM returns to `in_review` for a cycle 2 scoped to these two items.
O1 (already a seed) and O2 (`security.rst`, waiting on the maintainer) are out of scope and
untouched.

- [x] **F1 (P3, R4).** `docs/_include/templates.rst`: the local-mode `{= =}` sentence now makes
  the same exception the next sentence makes for a client started by hand. It reads: "Under the
  default local mode, whose client runs inside the `hs cluster` process, it includes the variable
  only if the environment that starts `hs cluster` already sets it: the key that run mints is
  never put in that process's environment."
- [x] **F2 (P1, R3).** `test_client_names_missing_key_only_when_none_given` gains
  `environment-default-key`: `HYPERSHELL_SERVER_AUTH` set to the placeholder, no `-k`, expect no
  hint.

### Evidence

Python 3.14.6, worktree venv, every run under a perl process-group kill timer.

- **F1 probes** (throwaway sites, `echo 1 | hs cluster --no-db --no-tls` with template
  `echo lambda={= os.environ.get('HYPERSHELL_SERVER_AUTH', 'absent')[:6] =}`; the launcher is
  `--launcher=env`, which passes its environment through):

  | Run | starting shell exports the variable | lambda reads |
  |---|---|---|
  | local `hs cluster` | no | `absent` |
  | local `hs cluster` | yes (`userow…`) | `userow`, the shell's value |
  | `--launcher` | no | `0742da`, the run's own key |
  | `--launcher` | yes (`userow…`) | `93612c`, the run's own key |

  Every run exited 0. The new local-mode sentence and the unchanged launcher sentence state
  exactly this.
- **F2 green:** `pytest -n auto tests/test_cluster_launch.py -k names_missing_key` → **6 passed**.
- **Mutant H2** (`AUTH_ENV not in os.environ` dropped from `no_key` in `ClientApp.run`):
  `pytest -n auto tests/test_cluster_launch.py` → **1 failed, 115 passed, 1 skipped**. The one
  failure is `…none_given[environment-default-key]` (hint present, expected absent). The mutant is
  killed. Restored with `git checkout -- src/hypershell/client.py`; `git diff src/` empty; the module
  → **116 passed, 1 skipped**.
- **Full suite**, `pytest -n auto`: **1006 passed, 88 skipped**, 0 failed (1005 plus the new case).
- **Sphinx** `-W --keep-going -E`: exit 1 with exactly the two known toctree warnings
  (`cli/task_submit.rst`, `manual.rst`). The new sentence renders in `templates.html`.
