# REVIEW — `auth_env` and `auth_eval` in a config file break every command

> Adversarial QA by `hs-review`, run in an isolated/clean context. The correctness pass grades the
> branch diff against [`GOAL.md`](GOAL.md) + the AGENTS.md invariants **only** — it does not see
> `PLAN.md`/`TECH.md` (avoids grading-its-own-homework / plan-sycophancy). Every finding cites an
> **executed** command, not an assertion.

- **Reviewed commit:** 9938ea9  ·  **Base:** `origin/develop` (15587cf)  ·  **Date:** 2026-11-11
- **Verdict:** changes-requested
- **Cycle:** 1 of ≤3 — mirrors `review.cycle` in `TECH.md`

Run by hand by a reviewer agent that did the correctness pass itself (no nested subagent): it read
only `GOAL.md`, the spec-excluded diff, `invariants.md`, `review-rubric.md`, `AGENTS.md`, and the
`TECH.md` frontmatter for `slug`/`kind`/`base`. Contract drift: none (`GOAL.md` has only the shaping
commit `cec1d83`).

## Verification run

All CLI drives used an isolated `HOME` (user layer) and `HYPERSHELL_SITE` (local layer) under
`$TMPDIR`, with a marker string in every secret; after each drive every file under the temp tree,
stdout and stderr were grepped for the marker.

- `uv lock --check` → clean; cmdkit 2.8.0 in the venv.
- `uv run pytest -q -n 4` (HEAD, Python 3.14) → **1627 passed, 90 skipped**.
- `uv run --python 3.12 --group docs sphinx-build -W --keep-going -q -E -b html docs …` → exit 0, no warnings.
- `hs config get server.auth` with `[server] auth_env` (set) / `auth_eval` (ok) → resolved value, exit 0.
  Unset variable → exit 3 `ConfigurationError: server.auth_env names NOPE_UNSET, which is not set (from: …)`;
  `auth_eval = "false"` / `"exit 4"` / missing binary → exit 3 `… command exited with status N (from: …)`;
  `auth_eval = "echo <secret>; exit 3"` → exit 3, secret absent from all output and files.
- `hs server --no-db`, `hs client`, `hs submit -q` with unset `auth_env` or failing `auth_eval` → exit 3,
  one CRITICAL line naming the key, no traceback file; never `<not-secure>`/`None`.
- End-to-end `hs server --no-db tasks.in` + `hs client` (both keys from config): `auth_env` + `--no-tls`,
  `auth_eval` + TLS, plain `auth`, `HYPERSHELL_SERVER_AUTH` over a file `auth_env`, user `auth` under
  local `auth_env` → all 4 tasks run, server and client exit 0; repeated at `DEBUG` and `TRACE` logging
  → no key in any log, stdout, stderr or file.
- `hs submit -q` against a running server, key from `auth_env` → authenticates (refused later only for
  DB mode); same with `-k wrongkey…` → `AuthenticationError`. Proves submit uses the resolved key.
- `hs cluster` local (TLS and `--no-tls`, DB and `--no-db`), `--launcher=env` with a file `auth_eval`,
  and local with an unset `auth_env` (key unused) → all exit 0, tasks run.
- `auth_eval` side effect counter: `hs list` 0 runs (no import-time evaluation), `hs config get`,
  `hs server`, `hs client` 1 run each.
- R3 layering: user `auth` / local `auth_env`; user `auth_env` / local `auth`; user unset `auth_env` /
  local `auth`; user `auth_eval` / local `auth_env` → the higher layer decides in every case.
- R6: two forms in the user or local file (server or `[relay.upstream]`) → `hs config get server.port`,
  `hs list` exit 3, `More than one form of server.auth (auth, auth_env) in one configuration layer (<path>)`.
- R4: `hs config get relay.upstream.auth` set/eval/unset/failed/two-forms/default → as for `server.auth`;
  `hs relay -H localhost -p 1` with unset `auth_env` → exit 3 naming the key.
- Edge keys: empty `auth_env`, whitespace-only `auth_env`, `auth_eval = "true"` → `hs config get` prints
  the empty/blank value (exit 0); `hs server` refuses (exit 2, length / allowed-characters policy).
  Set-but-empty is a GOAL non-goal; behaviour is as the GOAL describes.
- **2.8.7 pick** (scratch clone, `2.8.6^{commit}` = 0eaea7b): cherry-pick `15587cf`, `a5aaf42`, `844e897`.
  Conflicts only in absent files, dropped: `15587cf` → `ROADMAP.md`, `docs/whats_new.rst`;
  `a5aaf42` → `spec/…/TECH.md`; `844e897` → `docs/whats_new.rst`, `spec/…/TECH.md`. All source,
  `pyproject.toml` and `uv.lock` hunks applied cleanly (cmdkit `>=2.8.0`, locked 2.8.0).
  `uv lock --check` clean; `uv run --python 3.12 pytest -q -n 4` → **5 failed, 712 passed, 1 skipped**
  (all five in `tests/test_auth_variants.py`, deterministic on a serial rerun; see F1). The product
  itself works on the pick: the same server+client e2e drive against the pick's venv (`auth_env`,
  plain `auth`; DB and `--no-db`) → server and client exit 0, all tasks run.

## Requirement → evidence matrix

| R-ID | Implemented by (file/commit) | Verified how | Status |
|------|------------------------------|--------------|--------|
| R1 | `core/config.py` `resolve_auth`; `server.py`, `client.py`, `submit.py` resolve at run (a5aaf42, 844e897) | e2e server+client with `auth_env`/`auth_eval`, TLS on/off; submit -q auth + wrong-key control | ✅ |
| R2 | `config.py` `ConfigGetApp` → `resolve_auth` (a5aaf42) | `hs config get server.auth` prints resolved value, exit 0 | ✅ |
| R3 | cmdkit 2.8.0 one-key merge + `resolve_auth` | four user/local layer orders via config get and e2e | ✅ |
| R4 | `relay/upstream.py` `upstream_key` → `resolve_auth` (9938ea9) | config get set/eval/unset/fail/dup; `hs relay` unset | ⚠️ see F2 |
| R5 | `resolve_auth` unset/failed guards | config get, server, client, submit -q, relay → exit 3, no traceback, no default | ✅ (F2 is an adjacent edge) |
| R6 | `check_auth_forms` at import (a5aaf42) | dup in user/local, server/relay → exit 3 clean | ✅ |
| R7 | `pyproject.toml`, `uv.lock` (a5aaf42) | `uv lock --check` on HEAD and on the 2.8.6 pick | ✅ |
| Pick-safe onto `2.8.6` (Clarifications) | a5aaf42 + 844e897 separable from 9938ea9 | scratch-clone pick + full suite under 3.12 | ❌ F1 |

Unmapped changes (possible scope creep): none. `ROADMAP.md` / `issues/` status flips to
`adopted:server-auth-variant-collision` match reality.

## Findings

### [HIGH/CONFIRMED] F1 — the new command tests fail on the 2.8.7 pick: `stop_server` interrupts a server still shutting down
- **Where:** `tests/test_auth_variants.py:261` (`stop_server`), used by `test_command_server_and_client`
  (all four forms, `:283`) and `test_command_submit_queue` (`:305`).
- **Failure scenario:** on `2.8.6` + `15587cf` + `a5aaf42` + `844e897`, the client finishes and returns
  while the server is still in its normal shutdown (log ends at `Done (confirm)`); `stop_server` sends
  SIGINT at once, so the server exits 4 (`keyboard-interrupt: going down now!`), or -2 in the submit
  test, and `assert status == exit_status.success` fails. The plain `auth` form fails too, so the cause
  is the test's timing assumption, not the key. On `develop` the same tests pass.
- **Evidence:** pick suite `uv run --python 3.12 pytest -q -n 4` → 5 failed, 712 passed, 1 skipped;
  serial rerun `-k "server_and_client or submit_queue"` → same 5 fail. With a 10 s `server.wait()`
  before the SIGINT (scratch clone only, reverted) → 5 passed. The pick's CLI e2e (server + client,
  no SIGINT) exits 0.
- **Touches invariant / requirement:** GOAL Clarifications — "must be pick-safe onto `2.8.6`"; the
  2.8.7 release cannot ship with a red suite.

### [MEDIUM/CONFIRMED] F2 — `hs relay` regresses to a traceback when `auth_eval` output is not UTF-8
- **Where:** `src/hypershell/core/config.py:374` (`except subprocess.CalledProcessError`), reached from
  `src/hypershell/relay/upstream.py:156`.
- **Failure scenario:** `[relay.upstream] auth_eval` naming a command that exits 0 but prints non-UTF-8
  bytes. cmdkit decodes with `.decode()`, raising `UnicodeDecodeError`; `resolve_auth` catches only
  `CalledProcessError`, so the relay exits 5 with `Exception traceback written to …/Logs/exception-*.log`.
  On `origin/develop`, `upstream_key` wrapped *any* resolution error (`except Exception`) into a clean
  `ConfigurationError` (exit 3). The same path gives the same traceback for `server.auth` in
  `hs config get`, `hs server`, `hs client`, `hs submit -q` (not a regression there: the old import-time
  read also tracebacked). No key bytes beyond the single offending byte reach the message or the file.
- **Evidence:** `printf 'SECRETMARKabc\377def0123456789'` as the eval script →
  `hs relay -H localhost -p 1` rc=5 `UnicodeDecodeError: 'utf-8' codec can't decode byte 0xff in position 13`
  + traceback file; `hs config get server.auth` and `hs client -p 1 --no-tls` rc=5, same.
  `git show origin/develop:src/hypershell/relay/upstream.py` → `except Exception as error:` in `upstream_key`.
- **Touches invariant / requirement:** R4 (relay behaviour narrowed vs develop); GOAL *Outcome* "never a
  traceback". Not the coupled core, not a security/DB invariant.

### Observations (not findings)
- `core/queue.py:396` `QueueConfig.load()` (only via `QueueInterface.new()`, which no command or doc
  calls) still reads `config.server.auth` directly: with `auth_env` unset it yields `auth=None` and
  `QueueClient.new()` fails with `AttributeError: 'NoneType' object has no attribute 'encode'`. Library
  path, outside R5's "every command"; noted for triage, not graded.
- `hs config get server.auth --user` with only `auth_env` in that file reports "not found" (site views
  read the raw file, by design of the `site_name is None` branch); `hs config which server.auth` with a
  variant reports "not found". Neither is an R-ID; both predate the diff.

## Human-gate triggers

- Not triggered. F1 is in a test file; F2 lives in `core/config.py` / `relay/upstream.py`, outside the
  high-blast-radius list, and weakens no auth/TLS/DB invariant (the error path refuses; it does not
  fall back to a key). No secret reached stdout, stderr, logs, traceback files or argv in any drive.

## Optional completeness sub-pass

- Not run (not requested).
