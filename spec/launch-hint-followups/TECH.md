---
slug: launch-hint-followups
title: 'Launch-path follow-ups: accurate no-key hints, template docs, an export-name
  test'
kind: fix
appetite: small
status: in_progress
branch: fix/launch-hint-followups
base: develop
current_phase: P2
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
  status: pending
  satisfies:
  - R5
  depends_on: []
  parallel: true
  hammerable: false
  hill: uphill
  verify: uv run pytest -n auto tests/test_cluster_launch.py -k shell_cannot_assign
- id: P3
  name: Accurate template docs and the approved security sentence; final gates
  status: pending
  satisfies:
  - R4
  - R6
  depends_on:
  - P1
  - P2
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run pytest -n auto && uv run sphinx-build -W --keep-going -E docs docs/_build
review:
  last_reviewed_commit: ''
  verdict: none
  blocked_reason: ''
  cycle: 0
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

- [ ] Add `test_ssh_env_skips_names_a_shell_cannot_assign`, parametrized over a dot, a dash and a
  space in a `HYPERSHELL_*` name. It drives `hs cluster --ssh HOST --env` through the stand-in ssh
  and asserts four things: the name is on no command line, the warning is logged, the other exports
  arrive, and the run completes.
- [ ] Run green; apply the mutant (the skip removed) and run again (it must fail); restore
  `launch.py` (`git diff` empty) and run green. Record all three below.
- **Verify:** `uv run pytest -n auto tests/test_cluster_launch.py -k shell_cannot_assign`.
- **Touches:** `tests/test_cluster_launch.py`.

## Phase P3 — Accurate template docs and the approved security sentence; final gates
**Satisfies:** R4, R6 · **Depends on:** P1, P2
**Goal:** `templates.rst` says what the probes in PLAN §4 show; `security.rst` carries the approved
wording; the full suite and the docs build pass.

- [ ] `docs/_include/templates.rst`: rewrite the `{% %}` environment paragraph and the `{= =}`
  sentence per PLAN §2.3.
- [ ] `docs/security.rst`: replace the sentence with the approved text, moved per PLAN §2.3 so that
  "This" follows what it refers to.
- [ ] Full suite under `-n auto` (Python 3.14) and the Sphinx build. Record both below.
- **Verify:** `uv run pytest -n auto && uv run sphinx-build -W --keep-going -E docs docs/_build`.
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
