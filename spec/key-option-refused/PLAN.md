# PLAN — Refuse the queue key on a command line

> **Status:** Draft for review · **Last updated:** 2026-11-13
> **Authoritative technical design.** The *how*. Vision/contract is [`GOAL.md`](GOAL.md);
> the phased executable roadmap is [`TECH.md`](TECH.md). Backing detail is in
> [`research/`](research/). Every design element traces to a GOAL R-ID.

## 1. Summary

`hs client`, `hs server` and `hs submit` keep `-k`/`--auth` registered on their parsers, but bound to
one shared `argparse.Action` that refuses it: a `cmdkit` `ArgumentError` with a fixed message naming
`HYPERSHELL_SERVER_AUTH` and `server.auth`, raised while parsing, before the command does anything.
Because argparse resolves every accepted form (`-k K`, `-kK`, `-k=K`, `--auth K`, `--auth=K`,
unambiguous prefixes, `-qkK`) to that action and passes it only the option string, the key never
reaches a message, and `--` and task files never reach the action at all. Every command then reads the
key with `resolve_auth('server')` alone. The rest is surface: help, hints, docs, completions, tests and
the one drive that pass or offer a key on a command line. Two phases: the tests move to the
environment first (green on today's code), then the refusal lands with its surface.

## 2. Design

### 2.1 The refusal (R1, R2, R3)

A small block in `src/hypershell/core/config.py`, beside `resolve_auth` (which already owns where a
key may come from):

- `KEY_OPTION_STRINGS = ('-k', '--auth')` — the option strings that are refused.
- `RefuseKeyOption(argparse.Action)` — `__call__` raises `cmdkit.cli.ArgumentError` with a message
  built from the **matched option string** argparse passes in (never `values`), e.g.
  `--auth is not accepted: a key on a command line is visible to every user on the host. Give it in
  the HYPERSHELL_SERVER_AUTH environment variable or as server.auth in a configuration file.`
  An optional per-command hint is appended (below).
- `refuse_key_option(interface, hint='')` — registers the action on a cmdkit `Interface` with
  `nargs='?'` (a bare `-k`, or `-k` followed by another option, is refused the same way rather than
  failing "expected one argument") and `default=argparse.SUPPRESS` (the namespace never carries the
  attribute, so `from_namespace` sets nothing).

Each of `ClientApp` (`client.py:1593-1594`), `ServerApp` (`server.py:1699-1700`) and `SubmitApp`
(`submit.py:1304`, `:1308`) replaces its `add_argument('-k', '--auth', …)` with
`refuse_key_option(interface)`. `SubmitApp` passes a hint: a `-k` meant for the task goes after `--`
(probe: `hs submit cmd -k K` without `--` has always bound `K` as submit's own key, so a user who
meant the task's option is exactly who meets this refusal). The hint names `--`, not `-k`, and takes
the option from the message's `{option}`.

Why the refusal is complete (probe evidence, [`research/01`](research/01-refusal-mechanism.md)):

- argparse calls the action the moment it meets the option, so the parse stops there: extras before
  or after it are never reported, and no `unrecognized arguments` line is produced.
- `hypershell.main()` initialises logging before parsing, and `Application.main` logs every
  `ArgumentError` at CRITICAL through the `hypershell` logger (stderr and any configured file).
  The message holds no key, so neither does any record; nothing else logs argv at any level, and no
  exception hook records it.
- The refusal fires in `from_cmdline`, before `__enter__` and `run()`: no port bound, no connection,
  no submission, no database opened (the probe site's database file was never created). Exit status
  is cmdkit's `exit_status.bad_argument` (2), as for every parser error.

### 2.2 Reading the key (R4)

- `ClientApp.run()`: `self.auth = resolve_auth('server')` unconditionally; `auth_given` goes;
  `no_key = AUTH_ENV not in os.environ and self.auth == DEFAULT_AUTH` (unchanged meaning). The class
  keeps `auth: str` as an instance attribute set in `run()` (`check_args()` reads it).
- `ServerApp.run()`: `self.auth = resolve_auth('server')` unconditionally; the key-policy refusals at
  `server.py:1802-1807` are untouched.
- `SubmitApp.run()`: in queue mode, `self.queue_auth = resolve_auth('server')` unconditionally.
- Precedence is `resolve_auth`'s, unchanged: the highest configuration layer that sets `auth`,
  `auth_env` or `auth_eval` decides, with `HYPERSHELL_SERVER_AUTH` as the environment layer.
- Nothing in `cluster/` or `relay/` changes. Launched clients already take the key from
  `HYPERSHELL_SERVER_AUTH` or ssh's stdin; `client_launch()` emits no `-k`.

### 2.3 Surface (R5, R6, R7)

- **Help and usage** (`APP_USAGE`/`APP_HELP` in the three modules): drop `[-k KEY]` and the
  `-k, --auth` line. `hs submit`'s `-q` line, and the client's and server's help prose, say the key
  comes from `HYPERSHELL_SERVER_AUTH` or `server.auth`.
- **Client hints** (`client.py:1149-1160`, and the comment at `:1667`): "no -k was given" becomes
  "no key is configured (server.auth)"; the condition is unchanged.
- **Help snippets** (`docs/_include/{client,server,submit}_{usage,help}.rst`): mirror the above;
  `submit_help.rst:50` ("Requires -H, -p, and -k") says where `-q` takes its key from.
- **Prose** — each passage that describes behaviour gets `.. versionchanged:: 2.9.0` worded without
  `-k`/`--auth` (the man page must hold neither; the change is described as "a key on the command
  line is refused"):
  `getting_started.rst:133-152` (server and client examples take the key from the environment: an
  `export HYPERSHELL_SERVER_AUTH=…` builtin, which starts no process and so is on no process's
  command line, or `server.auth` in a configuration file), `security.rst:174` and `:187`,
  `security.rst:524` (reword "no upstream `-k` option"), `install.rst:473` (the `--auth <tab>`
  bullet goes), `_include/templates.rst:213`, `_include/security_alt.rst:13`. `README.rst` has no
  match today and is checked by the sweep.
- **Completions**: Bash `share/bash_completion.d/hs` (submit 529/570, client 585/604, server
  646/668-671) and zsh `share/zsh/site-functions/_hs` (475, 510, 557) drop `-k`/`--auth`; zsh's
  `_hs_authkey` stays, since `hs config set server.auth` (line 273) still uses it.
- **Man pages**: `share/man/` is not regenerated here (GOAL gate); the docs sources are proven by a
  `-b man` build into a temporary directory that greps clean.

### 2.4 Tests and drives (R4, R8)

- **Conversion (P1).** Every test, bench and drive that starts `hs server`/`hs client` with `-k KEY`
  gives the key in `HYPERSHELL_SERVER_AUTH` instead, through the `env` the harness already passes
  (most helpers take one): `test_dispatch_{workload,staged,version}.py`, `test_queue_{start,workload,
  limits}.py`, `test_relay_{workload,outage}.py`, `test_topology_server.py`, `test_upgrade_cli.py`,
  `test_zone_workload.py`, `test_cluster_launch.py` (`start_server`; the hint test's two
  `explicit-*-key` cases go, their environment and configuration twins remain),
  `tests/bench/bench_queue.py`, `spec/relay/drive/relay_throughput.py:170`.
  `test_auth_variants.py::test_command_submit_queue` is parametrised over `RESOLVED_FORMS`, so R4 has
  a test per source on each command (server and client already have one).
- **New `tests/test_key_option.py` (P2).**
  - unit: each app's `interface.parse_args` refuses every form (table from research 01, including
    `--au`, `-k=K`, bare `-k`, `-qkK`, `cmd -k K`) with an `ArgumentError` whose text names both
    sources and holds no key; `--` and `--template=-kfoo` parse as before.
  - integration (`hs` subprocess in `temp_site`, `HYPERSHELL_LOGGING_FILE=enabled`, file level
    `devel`): per command × the four GOAL forms, exit 2, stderr names `HYPERSHELL_SERVER_AUTH` and
    `server.auth`, the key in no stdout, stderr or file under the site, no database file created.
  - R3: `hs submit -- echo -k X` and a task file holding `echo -k X` store the task's args unchanged.
  - sweep (R5–R8): walk `src/`, `docs/` (not `_build`), `README.rst`, `share/` (not `share/man`),
    `tests/` and `spec/*/drive/` for `-k`/`--auth` as a word and fail on any hit outside the allow
    list: the Non-goals' negative assertions (`tests/test_cluster_launch.py` `KEY_OPTIONS` and the
    `CONTRACT_CLIENT_OPTIONS` row, `tests/test_relay_launch.py`, `spec/relay/drive/relay_probe.py`),
    `cluster/launch.py`'s redaction (§2.5), `KEY_OPTION_STRINGS`, `tests/test_factory_fsm.py`
    (pytest's own `-k`), and `tests/test_key_option.py` itself, which passes the option only to see
    it refused.
- `tests/test_server.py` `OPTION_PAIR`: the `-k`/`--auth` entries move to the refusal test (a bare
  `-k` now gets the refusal, not "expected one argument").

### 2.5 `cluster/launch.py` redaction — kept

`ClientLaunch.describe()` keeps `redact_secrets(argv, ['-k', '--auth'])`
([`research/02`](research/02-launch-redaction.md)). HyperShell emits no `-k`, so the guard only ever
meets a launcher's own `-k` (`srun -k`, `ssh -k`), where it masks the next word in the log: a
pre-existing cosmetic flaw that hides nothing secret. Keeping it changes no launch log line and keeps
R4's "launched clients join as before" true by construction; the `assigns_key` redaction, the guard
that matters for the hand-off, is untouched. `redact_secrets` stays public (`cluster/remote.py`
re-export, pinned by a test).

### Requirement → design map

| R-ID | Design element(s) that satisfy it |
|------|-----------------------------------|
| R1 | `RefuseKeyOption` / `refuse_key_option` (`core/config.py`) on the three interfaces; parse-time `ArgumentError` → exit 2 before `run()` |
| R2 | message built from the option string only; logging sees only that message; no argv logging or hook exists (research 01) |
| R3 | argparse never calls the action after `--`; task files never reach a parser; `SubmitApp` hint names `--` |
| R4 | `run()` reads `resolve_auth('server')` unconditionally; no `cluster/`/`relay/` change; P1 conversion and per-source tests |
| R5 | `APP_USAGE`/`APP_HELP` of the three modules; client hints; submit's `-q` help |
| R6 | `docs/_include` snippets, prose passages with `versionchanged:: 2.9.0`, man build grep |
| R7 | Bash and zsh completions |
| R8 | P1 conversion; `tests/test_key_option.py` (R1–R3 per command and form, sweep); `test_auth_variants.py` per source |

## 3. Invariant gate (AGENTS.md constitution check)

Checked against [`.agents/factory/invariants.md`](../../.agents/factory/invariants.md) before research
and again after this design. Touched sections: §9, §10, §11, §12. §1–§8 are untouched: no model,
query, FSM, thread, shutdown, resource or signal code changes (`client.py`/`server.py` change only
in their `Application` classes).

- **§9 Auth is always required; refuse the placeholder; minimum length and alphabet.** Unchanged:
  the key still comes from `resolve_auth`, the server's three key-policy refusals stay, and the
  client still warns on the default key. One fewer way to supply it; none weakened.
- **§10 Configuration built once at import.** The new block in `core/config.py` is a class and a
  function; it reads and mutates no configuration. `resolve_auth` is unchanged.
- **§11 The key is never in a launched argv; the client strips it from tasks; contract test of
  2.9.0a4 client options.** `client_launch()`, `describe()`, `task_env()` untouched. The contract
  test is unaffected: the launch never emitted `-k`, and a launched client's options are still
  accepted (`-k` was never among the ones emitted).
- **§12 Same-commit rule.** P2 carries the CLI change, its `docs/_include` snippets, the completions
  and the prose passages in one commit; markers use `2.9.0`; the docs build passes with `-W`.
  Exit status uses `exit_status.bad_argument` via cmdkit, no literal. New tests are tagged.

### Deviation justifications

| Deviation | Why needed | Simpler alternative rejected because |
|-----------|-----------|--------------------------------------|
| An option is kept registered only to refuse it | Deleting it makes cmdkit/argparse echo `-k KEY` to stderr and the log file (research 01, mode 2) | A pre-scan of argv before parsing must re-implement argparse's rules (`-qkK`, prefixes, `--`, values with a space, `--template=-kfoo`) and would miss or misfire on some; the parser already knows them all |

## 4. Rabbit holes (resolved)

- **Does any form slip past the parser, or leak through a log?** → No, for every form the parser
  accepted before; probed per command with a DEVEL log file
  ([`research/01`](research/01-refusal-mechanism.md)).
- **Does the refusal fire before anything happens?** → Yes: parse time, before `run()`; logging is
  already up, which is why the message, not the parser's default, must carry no key.
- **Keep or drop the launch redaction?** → Keep ([`research/02`](research/02-launch-redaction.md)).

## 5. Risks & open questions

- **Residual echoes outside R1** (pre-existing, unchanged): `hs server --au=K` / `hs client --a=K`
  fail as `ambiguous option: --au=K could match …`, and `hs relay -k K` / `hs cluster --auth=K` as
  `unrecognized arguments: … K`. None was ever an accepted form. The general fix (a parser error
  never quotes an option's value) belongs in cmdkit's `Interface`; proposed as a follow-up seed, not
  this cycle. *Foreman: confirm out of scope.*
- **`hs submit cmd -k X`** now refuses where it used to bind `X` silently as submit's key. That is
  R1 (an accepted form), and the hint names `--`; the squash note (`hs-publish`) should mention it.
- **Test conversion breadth (P1)**: about 40 call sites in 12 test files, plus the bench and one drive. Mechanical,
  but the workload tests are slow; P1's verify runs them once.

## 6. Verification strategy

- **P1:** the converted test files pass on today's code (the environment path already works), the
  bench and drive compile, and a grep finds no key option in `tests/` or `spec/*/drive/` outside the
  allow list and `tests/test_server.py` (changed in P2).
- **P2:** `tests/test_key_option.py` (unit, integration, sweep), `test_server.py`, `test_client.py`,
  `test_submit.py`, `test_auth_variants.py`, `test_completions.py`, and the launch tests
  (`test_cluster_launch.py`, `test_relay_launch.py`) unchanged in behaviour; a CLI drive in a
  `temp_site` with a log file (`hs server --auth=KEY` → exit 2, both sources named, key nowhere on
  disk); a `-b man` build into a temporary directory that greps clean; the HTML build with `-W`.
  Both docs builds use a separate environment (`UV_PROJECT_ENVIRONMENT=$TMPDIR/docs-venv`) so the
  worktree's `.venv` is not replaced, and run with the sandbox off (`sphinx_sitemap`).

---

*Backing research: [`research/00-digest.md`](research/00-digest.md).*
