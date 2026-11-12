# PLAN — `auth_env` and `auth_eval` in a config file break every command

> **Status:** Draft for review · **Last updated:** 2026-11-11
> **Authoritative technical design.** The *how*. Vision/contract is [`GOAL.md`](GOAL.md);
> the phased executable roadmap is [`TECH.md`](TECH.md). Backing detail is in
> [`research/`](research/). Every design element traces to a GOAL R-ID.

## 1. Summary

Raise the floor to `cmdkit[toml]>=2.8.0`, which already makes `auth`, `auth_env` and `auth_eval` one
key across layers (R3, R7). Add one HyperShell guard, `resolve_auth` in `core/config.py`, that
resolves `server.auth` or `relay.upstream.auth` from the merged view and turns an unset variable, a
failed command, or two forms in one layer into a `ConfigurationError` naming the key. The two
class-body reads in `server.py` and `submit.py` become `None` defaults resolved at run, as
`client.py` already does, so no key is read at import; a side-effect-free per-layer scan at import
refuses two forms for every command (R6). `hs config get` sends the two keys through the guard. The
relay's own resolver delegates to it in a separate, relay-only phase. Every shared edit sits on lines
identical on `2.8.6`, so the squash commit picks onto `2.8.6` for 2.8.7 after the units fix.

## 2. Design

### The guard — `src/hypershell/core/config.py`

- **`AUTH_FORMS: Final = ('auth', 'auth_env', 'auth_eval')`** and **`AUTH_SECTIONS: Final =
  (('server',), ('relay', 'upstream'))`**. The relay row is harmless where no layer has a `[relay]`
  table (2.8.6): the scan finds nothing there.
- **`check_auth_forms(base: Configuration) -> None`**: for each section in `AUTH_SECTIONS` and each
  layer in `base.namespaces` (walked with `.get(…, {})`, since `reload_local` passes plain `{}` for
  absent files), collect the forms the layer sets. More than one raises
  `ConfigurationError(f'More than one form of {key} ({", ".join(given)}) in one configuration layer ({label})')`,
  where `key` is `server.auth`/`relay.upstream.auth` and `label` is the file path for
  `system`/`user`/`local` (`path.get(name).config`, which honors `HYPERSHELL_CONFIG_FILE`), `env` for
  the environment, `<name>` otherwise. No environment lookup, no subprocess. It scans **every**
  layer, not only the deciding one: cmdkit hides a lower layer's duplicate in the merged view
  ([01](research/01-key-reads-and-cmdkit.md)), and R6 says "one configuration layer".
- **`resolve_auth(*section: str, base: Configuration = None) -> str`**: `base` defaults to the module
  `config`. Runs `check_auth_forms` for that section; walks the merged view to the section
  (`KeyError` → `ConfigurationError('"{key}" not found in configuration')`, the wording `hs config
  get` uses today); finds the one form present; `getattr(section_ns, 'auth')` (cmdkit does the
  environment read and the `shell=True` command, stripped). Then:
  - `subprocess.CalledProcessError` → `ConfigurationError(f'{key}_eval command exited with status {returncode} ({blame})')`, `from None`;
  - `None` (unset variable) → `ConfigurationError(f'{key}_env names {var}, which is not set ({blame})')`;
  - otherwise `str(value)`.
  `blame` is the existing `blame(base, *section, form)` (`from: <file>`). Messages name the key, the
  form and the variable, never the resolved value. An empty variable resolves to `''` (non-goal:
  the server's length check handles it).
- **Import-time scan.** Inside the existing `try` (lines 355–363, identical on `2.8.6`), after the
  `if/else` builds `config`, call `check_auth_forms(config)`; add an `except ConfigurationError as
  error:` branch **before** `except Exception`, printing `display_critical(error, module=__name__)`
  and exiting `exit_status.bad_config`. Add `display_critical` to the existing
  `core.exceptions` import (line 29).
- **`__all__`**: a new line directly after `'config', 'update', 'default', …` naming
  `'resolve_auth', 'check_auth_forms', 'AUTH_FORMS', 'AUTH_SECTIONS'`. The last `__all__` line differs
  on `2.8.6`, so it is not touched.
- Place the new definitions after `blame()` / `get_logging_style()` (identical on both lines) and
  before the `try`.

### Commands — resolve at run (`server.py`, `submit.py`, `client.py`)

- `server.py`: `auth: str = config.server.auth` → `auth: Optional[str] = None  # Without -k, the key
  is resolve_auth('server') (see run)`; `-k` keeps `default=auth`. In `ServerApp.run`, after
  `special_options()` and before `check_args()`: `if self.auth is None: self.auth =
  resolve_auth('server')`. `check_args`'s `DEFAULT_AUTH` / length / charset checks are unchanged.
- `submit.py`: `queue_auth: str = config.server.auth` → `Optional[str] = None`; in `run`, inside
  `if self.queue_mode:` before `QueueConfig(...)`: resolve when `None`. Without `-q` the key is not
  used and not resolved.
- `client.py`: `self.auth = config.server.auth` → `self.auth = resolve_auth('server')`. The
  `no_key` hint logic is unchanged.
- Each module adds `resolve_auth` to its existing `from hypershell.core.config import …` line
  (identical, with identical neighbors, on `2.8.6`).
- A `ConfigurationError` raised in `run` goes through each app's `get_shared_exception_mapping`
  → `handle_exception` → one `CRITICAL` line, exit `bad_config`, no traceback (R5).
- `-k` given: the configured key is not used, so it is not resolved and cannot fail.
- `core/queue.py` `QueueConfig.load` is **not** changed: no command calls `QueueInterface.new()`,
  and its import line neighbors a line that differs on `2.8.6`.

### `hs config get` — `src/hypershell/config.py` (identical on `2.8.6`)

In `ConfigGetApp.run`, merged view only (`self.site_name is None`): when the varpath's section is in
`AUTH_SECTIONS` and the variable is `auth`, print `resolve_auth(*section)` through `print_output`
(same JSON/raw formatting as a literal key) and return, before the generic lookup. Applies with or
without `-x`. Per-file views (`--user`, `--local`, …) and other keys are unchanged. Unset/failed/
duplicate → `ConfigurationError` → exit `bad_config` with the key named (R5). On `2.8.6`
`relay.upstream.auth` reaches `resolve_auth`, finds no section, and reports `"relay.upstream.auth"
not found in configuration`, the same exit as today.

### Relay — `src/hypershell/relay/upstream.py` (2.9 only, its own phase)

`upstream_key(base=None)` becomes `return resolve_auth('relay', 'upstream', base=base)`; drop the
local `AUTH_VARIANTS` and the layer walk (now in the guard). Callers and the `DEFAULT_AUTH` warning in
`RelayApp.check_args` are unchanged. The existing tests (`tests/test_relay_launch.py:201`, `:215`)
keep passing (`'auth, auth_env'` stays in the message).

### Dependency

`pyproject.toml`: `cmdkit[toml]>=2.7.7` → `cmdkit[toml]>=2.8.0`. `uv.lock` by
`uv lock --upgrade-package cmdkit` (sandbox off), never by hand; the lock diff must touch only the
`cmdkit` package block and its `requires-dist` specifier. `uv lock --check` passes after.

### Docs

`docs/security.rst` is unchanged (GOAL: this fix makes it true). No CLI surface changes, so no
`_include/*_help.rst` or `share/` edits. A *Not yet released → Fixes* entry in `docs/whats_new.rst`
below the units entry, ending "This also ships in 2.8.7."; P3 adds the `[relay.upstream]` clause. No
`versionchanged` marker (a fix restores documented behavior; same call as the units fix).

### 2.8.6 drift and the pick ([02](research/02-pick-onto-2.8.6.md))

- **Identical on `2.8.6`:** `src/hypershell/config.py`; in `core/config.py` the imports, `blame`,
  `get_logging_style` and the import-time `try`; the `core.config` import lines and the auth lines
  (with context) in `server.py`, `submit.py`, `client.py`; the `cmdkit` line in `pyproject.toml`;
  the `cmdkit` block and specifier in `uv.lock`; `tests/conftest.py`; the `tests.main` helpers.
- **Differs, not touched:** `core/config.py` `__all__` last line, defaults (`bundlemax`, `relay`),
  the post-`try` relay block; `core/queue.py`; `docs/security.rst`.
- **Lock:** `2.8.6` ships a `uv.lock` (no other lock file). Its `cmdkit` lines match develop's, so
  the lock hunk picks clean. The 2.8.7 release re-locks for its version bump anyway.
- **Absent on `2.8.6`:** `src/hypershell/relay/`, `tests/test_relay_*`. R4's code and tests live
  only there (P3); its shared rows are harmless.
- **Pick order:** `15587cf` (units fix) first, then this squash commit. Expected conflicts, each
  resolved by dropping the path: `docs/whats_new.rst`, `spec/`, `src/hypershell/relay/upstream.py`,
  `tests/test_relay_launch.py`. The units fix touches `server.py:69` (two lines below our import
  edit, one unchanged line between) and no other line near ours.
- **Gate:** `git merge-tree --merge-base=<plan commit> 2.8.6 HEAD` reports no conflict outside
  those four paths (P2, P3 verify), plus one build-time run against a synthetic `2.8.6 + 15587cf`
  commit.

### Requirement → design map

| R-ID | Design element(s) that satisfy it |
|------|-----------------------------------|
| R1   | cmdkit 2.8.0 precedence; `resolve_auth('server')` at run in `server.py`, `submit.py -q`, `client.py`; no import-time read |
| R2   | `ConfigGetApp.run` routes `server.auth` (merged view) through `resolve_auth` |
| R3   | cmdkit 2.8.0 layer merge (highest form wins), read by `resolve_auth`; unit tests in both directions |
| R4   | `AUTH_SECTIONS` row for `relay.upstream` (shared, harmless on 2.8.6); `upstream_key` delegates to `resolve_auth` (P3) |
| R5   | `resolve_auth` maps `None` / `CalledProcessError` to `ConfigurationError` naming the key; apps' mapping → `bad_config`; never substitutes |
| R6   | `check_auth_forms` at import (every command) and inside `resolve_auth`; clean `except ConfigurationError` branch |
| R7   | `pyproject.toml` floor + `uv.lock` via `uv lock --upgrade-package cmdkit`; same hunk picks onto 2.8.6 |

## 3. Invariant gate (AGENTS.md constitution check)

Checked against [`.agents/factory/invariants.md`](../../.agents/factory/invariants.md) before
research and again after this design.

- **§9 Auth always required** — the resolver never returns `DEFAULT_AUTH`, `None` or a substitute
  on failure; `ServerApp.check_args` still refuses `DEFAULT_AUTH`, keys under
  `AUTH_MINIMUM_LENGTH`, and characters outside `AUTH_ALLOWED_CHARS`, now on the resolved key.
  Messages never contain the key.
- **§10 Configuration** — `config` is still built once at import and never mutated; the guard only
  reads it. The relay database swap (`RELAY_PROCESS`) is untouched and still runs after the `try`.
- **§12 Conventions** — exit codes from `exit_status.bad_config`; new tests carry `@mark.unit` /
  `@mark.integration`; `whats_new` in the same commit as the user-visible change; no CLI surface
  change, so no help snippet or completion edit; the dependency floor is raised (see deviation).
- **High-blast-radius files** — `server.py` and `client.py` get one default and one resolve line
  each; a confirmed review finding there takes the human gate. §1, §3–§8, §11 untouched.

### Deviation justifications

| Deviation | Why needed | Simpler alternative rejected because |
|-----------|-----------|--------------------------------------|
| Raise the `cmdkit` floor (§12: "do not raise casually") | R7 requires `>=2.8.0`; 2.8.0 is the precedence fix itself | Re-implementing per-layer precedence in HyperShell duplicates cmdkit and leaves `hs config get`'s merged view wrong |
| New `except ConfigurationError` branch at import also changes how other import-time config errors print (one line, no traceback-file line) | R6 asks for "not a traceback"; the scan must run inside the existing `try` | A separate `try` after the block sits against develop-only relay lines and would conflict on the 2.8.6 pick |

## 4. Rabbit holes (resolved)

- *What does cmdkit 2.8.0 return in each case?* → probed: `None`, `CalledProcessError`,
  `AttributeError` only when the deciding layer has two forms; a lower layer's duplicate is hidden
  ([01](research/01-key-reads-and-cmdkit.md)).
- *Eager or lazy resolution?* → lazy for the value (a bad key fails only commands that use it; `hs
  config edit` still works; `auth_eval` no longer runs at every `hs` start), eager for the
  structural two-forms scan (R6 says "commands", and the scan has no side effects).
- *Can the relay part be separated on the pick?* → yes: its code and tests live only in files absent
  on `2.8.6`, and the shared relay row is inert there ([02](research/02-pick-onto-2.8.6.md)).
- *Does `2.8.6` have a `uv.lock`?* → yes, with identical `cmdkit` lines; no other lock file.

## 5. Risks & open questions

- **`uv lock` churn.** A newer `uv` could rewrite unrelated lock lines, which would conflict on the
  pick. Mitigation: `--upgrade-package cmdkit`; the builder checks the diff touches only `cmdkit`
  lines and the merge-tree gate covers `uv.lock`.
- **Env-var form** (`HYPERSHELL_SERVER_AUTH_ENV`) still makes `server.auth` a table; `resolve_auth`
  returns `str(table)`, which the server refuses by charset. Non-goal (ROADMAP); not made worse.
- **Strict R6** refuses a file with two forms even when a higher layer overrides it. Intended (R6
  says "one configuration layer"); this also tightens `upstream_key`, which checked only the
  deciding layer.
- **`hs config which server.auth`** still reports "not found" when the key comes from `auth_env`
  (cmdkit's `which`). Not in the GOAL; left as is.

## 6. Verification strategy

- **New `tests/test_auth_variants.py`** (picks onto 2.8.6; uses only `tests.main`, `temp_site`,
  `monkeypatch`, `tmp_path`, `Configuration`/`Namespace`):
  - `@mark.unit` `test_resolve_*` over `Configuration` layers: literal, `auth_env` set, `auth_eval`
    ok; R3 both directions; unset → message names `server.auth_env` and the variable; `exit 4` →
    names `server.auth_eval` and status 4; two forms in the top and in a lower layer → names
    `server.auth` and `auth, auth_env`; no message contains the key; a section absent → not found.
  - `@mark.integration` `test_config_get_*` (subprocess, `HYPERSHELL_CONFIG_FILE` → a `tmp_path`
    TOML): `hs config get server.auth -r` prints the resolved value (env and eval), exit 0; with the
    variable unset / command failing → exit `bad_config`, stderr names the key, no `Traceback`.
  - `@mark.integration` `test_two_forms_*`: `hs config get logging.level` and `hs list` with
    `auth` + `auth_env` in one file → exit `bad_config`, names `server.auth` and the file, no
    `Traceback`, no "traceback written".
  - `@mark.integration` `test_command_*`: `hs server` (file input) and `hs client` run as
    subprocesses with `[server] auth_env` set → tasks complete (R1); `hs server`, `hs client`,
    `hs submit -q` with the variable unset → exit `bad_config`, key named, no traceback (R5); with
    the variable unset but `HYPERSHELL_SERVER_AUTH=tooshort` in the environment (a higher layer, R3),
    `hs server` fails on the key length (an argument error), not on the configuration. Tests, like
    docs, never pass a key with `-k`.
- **Relay (P3)**: in `tests/test_relay_launch.py`, extend the `upstream_key` tests (eval failure,
  unset message, lower-layer duplicate) and add a `hs config get relay.upstream.auth` subprocess test
  (resolved value; unset → `bad_config`).
- **CLI drive** in a throwaway site: a `[server] auth_env` file, `hs config get server.auth -r` prints
  the value when set and fails when unset.
- **Regression**: `tests/test_server.py`, `tests/test_submit.py`, `tests/test_client.py`; Sphinx
  `-W`; `uv lock --check`; the merge-tree gate against `2.8.6`.

---

*Backing research: [`research/00-digest.md`](research/00-digest.md).*
