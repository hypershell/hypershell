# GOAL — Refuse the queue key on a command line

> **Origin spec.** The *what* and *why* — the locked contract `hs-review` grades against.
> The *how* lives in [`PLAN.md`](PLAN.md) and [`TECH.md`](TECH.md) (written by `hs-plan`).
> Keep this at the right altitude: solved and bounded, but not over-specified — leave design
> freedom for the plan. Edit requirements here; do **not** silently drift them during build.

- **slug:** key-option-refused
- **kind:** fix
- **appetite:** small

## Problem

Since 2.9.0a5, `hs cluster` hands its run key to the clients it launches in their environment
(`HYPERSHELL_SERVER_AUTH`), or on ssh's stdin, never on a command line, where a process listing
shows it to every user on the host (#80, #81). The same key can still be put on a command line by
hand. Three commands on `develop` (2ffb5cc) declare a `-k`/`--auth` option:

- `hs client` — `src/hypershell/client.py:1594`
- `hs server` — `src/hypershell/server.py:1700`
- `hs submit` (with `-q`) — `src/hypershell/submit.py:1308`

No other command has it. `hs cluster` (and its alias `hsx`) has no key option and mints its own
key; `hs relay` takes its upstream key from `[relay.upstream]` only; `hyper-shell` is the same entry
point as `hs`. Nothing inside HyperShell passes `-k`: `cluster/launch.py` builds every launched
client's argv without it and still redacts `-k`/`--auth` from logged commands
(`cluster/launch.py:135-136`) as a guard; the relay's site clients take the key the same way.

So the hardening covers the clients a cluster launches and leaves out the processes a person starts,
which are the long-lived ones: a persistent `hs server` and the `hs client` processes that join it
hold the key on their command lines for as long as they run. It is also what a reader copies. The
usage lines and help text of all three commands, the client's own missing-key hints
(`client.py:1149-1160`, "no -k was given"), the docs (`getting_started.rst`, `security.rst`,
`install.rst`, `_include/templates.rst`, `_include/security_alt.rst`, the `_include` help and usage
snippets), the man pages built from them, and both the Bash and the zsh completions all offer it.

Nothing needs the option. When it is absent, each command already resolves the key with
`resolve_auth('server')`, from `HYPERSHELL_SERVER_AUTH` or from `server.auth`, `server.auth_env` or
`server.auth_eval` in a configuration file (since #97, `auth_env` and `auth_eval` work).

Simply deleting the option is not enough. A probe of cmdkit's parser (2026-11-13) shows that an
unknown option is reported as `unrecognized arguments: -k SECRETVALUE…`: the refusal itself would
echo the key to stderr and the log, and name no alternative. **Pre-existing** in `develop` and in the
2.8 line.

## Outcome / vision

On 2.9, a key on a command line is refused. Giving `-k` or `--auth` to any command is a usage error
that names `HYPERSHELL_SERVER_AUTH` and `server.auth` and never repeats the key; the command does
nothing else. Every other way to give the key works exactly as before, and the clients `hs cluster`
and `hs relay` launch are unaffected. No help text, message, completion, man page, documentation
example, test or runnable drive shows a key on a command line.

## Acceptance criteria (the contract)

- **R1** — WHEN `-k` or `--auth` is given to `hs client`, `hs server` or `hs submit`, in any form
  the parser accepted before (`-k KEY`, `-kKEY`, `--auth KEY`, `--auth=KEY`), the command SHALL
  exit with status 2, the status every other argument error exits with (cmdkit
  `exit_status.bad_argument`), with an error that names the `HYPERSHELL_SERVER_AUTH` environment
  variable and the `server.auth` configuration key as the ways to give the key, and SHALL do nothing
  else: no server bound, no connection made, no task submitted, no database touched.
- **R2** — IF R1's refusal happens, THEN the key SHALL appear neither on stderr nor in any log record
  at any level, including a configured log file.
- **R3** — WHEN `-k` is part of a task's own command (after `--` on `hs submit`, or inside a task
  file), it SHALL reach the task unchanged, as today.
- **R4** — WHEN no key option is given, `hs server`, `hs client` and `hs submit -q` SHALL take the
  key exactly as before, from `HYPERSHELL_SERVER_AUTH`, `server.auth`, `server.auth_env` or
  `server.auth_eval`, with the same precedence and the same key-policy checks; and the clients that
  `hs cluster` launches in every mode, and the site clients `hs relay` launches, SHALL join as
  before.
- **R5** — The usage lines and help text of the three commands, the client's missing-key hints and
  every other HyperShell message SHALL no longer offer `-k` or `--auth`; `hs submit`'s help SHALL say
  where `-q` takes its key from in place of requiring `-k`.
- **R6** — The documentation sources (`docs/`, `README.rst`) SHALL show no key on a command line and
  give every key through `HYPERSHELL_SERVER_AUTH` or the configuration; each changed passage that
  describes behaviour SHALL carry `.. versionchanged:: 2.9.0`; and man pages built from these
  sources SHALL contain no `-k` or `--auth`.
- **R7** — The Bash and zsh completions under `share/` SHALL offer neither `-k` nor `--auth` for any
  command.
- **R8** — The tests (`tests/`, including `tests/bench/`) and the executable drives under
  `spec/*/drive/` SHALL put no key on a command line; tests SHALL cover R1–R3 on each of the three
  commands and R4 with a key from each source.

## Non-goals (no-gos)

- **The 2.8 line.** The seed's warn-but-run behaviour for 2.8.x (its draft R1–R2) is not in this
  cycle: `master` is the maintainer's. A 2.8 backport, if wanted, is its own cycle against that
  line.
- The Python API: the `auth=` parameters of `QueueConfig`, `ServerThread`, `ClientThread`,
  `run_client` and kin are not a command line and stay.
- A replacement option such as `--auth-file PATH`, or any new way to give the key. A configuration
  file (`server.auth`, `auth_env`, `auth_eval`) already serves a site that will not use the
  environment.
- `hs relay` and `[relay.upstream]`; `hs cluster`'s key minting and hand-off (#80, #81).
- `hs config set server.auth KEY`, which names a key briefly on a command line to write it to a file.
- Whether `cluster/launch.py` keeps its `-k`/`--auth` redaction as a guard: `/hs-plan`'s call, so
  long as no log ever holds a key.
- Negative assertions and rules that name `-k` only to forbid it: the `KEY_OPTIONS` checks in
  `tests/test_cluster_launch.py` and `tests/test_relay_launch.py`, `spec/relay/drive/relay_probe.py`,
  `CONTRACT_CLIENT_OPTIONS` (it records the 2.9.0a4 client's options), and the harness rules in
  `AGENTS.md` and `.agents/`.
- Historical records under `spec/`: Markdown files and `research/` prototypes of landed cycles stay
  as written. The website and earlier release notes are not in this repository.
- Environment-variable reach to every configuration key (ROADMAP *Environment variables cannot
  reach every configuration key*), including an all-digit `HYPERSHELL_SERVER_AUTH`.
- A key-generating completion in place of the `--auth <tab>` one that goes away.

## Clarifications

- **Q:** Which direction does 2.9 take? — **A:** Refuse the option outright on 2.9; the 2.8 warning
  is out of this cycle. (maintainer via foreman, resolved 2026-11-13)
- **Q:** Which exit status is a usage error? — **A:** cmdkit maps `ArgumentError` to
  `exit_status.bad_argument` = 2, the status of every parser error and of `hs server`'s existing
  key-policy refusals (`server.py:1802-1807`); R1 uses it. (verified on `develop`, 2026-11-13;
  **needs foreman sign-off**)
- **Q:** Which spec/ files change? — **A:** Executable drives under `spec/*/drive/` that pass a key
  on a command line change, so a re-run on 2.9 still works: today only
  `spec/relay/drive/relay_throughput.py:170`. Markdown records and `research/` prototypes
  (`spec/zone-queue-primitive/research/prototypes/bench/bench.py`) are history and stay.
  (shaping decision, 2026-11-13; **needs foreman sign-off**)
- **Q:** Does this cycle regenerate the committed man pages under `share/man/`? — **A:** Proposed:
  no. The cycle changes the docs sources and proves R6 by building the man pages into a temporary
  directory; `hs-release` rebuilds `share/man/` at the next release, as it does for every change.
  (shaping decision, 2026-11-13; **needs foreman sign-off**, as the brief named `share/man`)
- **Q:** Does 2.9 offer `--auth-file PATH` (the seed's open question)? — **A:** Proposed: no; see
  Non-goals. (shaping decision, 2026-11-13; **needs foreman sign-off**)
- **Q:** Is removing the option from the parser enough? — **A:** No. cmdkit's parser reports an
  unknown option with its value (`unrecognized arguments: -k SECRETVALUE1234567`, probe on
  `develop`'s environment, 2026-11-13), so R1–R2 need the option recognised and refused. How is
  `/hs-plan`'s choice. (resolved 2026-11-13)
- **Q:** Is this a breaking change for users? — **A:** Yes, on the 2.9 line, by the maintainer's
  decision; `hs-publish` drafts the user-facing note in the squash commit body. (resolved
  2026-11-13)

## Evidence plan

- R1–R3: tests per command and per option form: exit status 2, the message names both alternatives,
  the key is absent from captured stderr and from every log record, nothing is started or submitted;
  `hs submit -- … -k …` keeps the task's `-k`.
- R4: the existing workload, queue and dispatch tests, converted to give the key in the
  environment, plus a test per source (environment, `server.auth`, `auth_env`, `auth_eval`) on each
  command; the existing `hs cluster` launch tests and relay tests pass unchanged.
- A CLI drive in a `temp_site`: `hs server` and `hs client` joined with the key in the environment
  and `hs submit -q` with the key from a configuration file, tasks completed; each command given
  `-k` / `--auth=` refuses with status 2 and no key in its output or log.
- R5–R8: a grep over `src/`, `docs/`, `README.rst`, `share/`, `tests/` and `spec/*/drive/` finds no
  offered `-k`/`--auth` outside the negative assertions named in Non-goals;
  `uv run sphinx-build -b man docs <tmp>` output greps clean; `tests/test_completions.py` passes and
  the completions grep clean; the docs build passes with warnings as errors.

## Related materials

- Seed: `issues/key-option-on-command-line.md`
- ROADMAP: *The queue key can still be given on a command line*.
- `spec/authkey-handling-hardening/`, `spec/launch-hint-followups/` (#80, #81): the launched-client
  hand-off.
- `spec/server-auth-variant-collision/` (#97): `resolve_auth`, `auth_env` and `auth_eval`.
- `src/hypershell/client.py:1144-1160`, `:1527-1594`, `:1664-1669`; `server.py:1588-1700`,
  `:1739-1807`; `submit.py:1207-1308`, `:1356-1359`; `cluster/launch.py:135-136`;
  `core/config.py:356` (`resolve_auth`).
- `docs/security.rst:174`, `:187`, `:524`; `docs/getting_started.rst:133-152`;
  `docs/install.rst:473`; `docs/_include/templates.rst:213`; `docs/_include/security_alt.rst:13`;
  `docs/_include/{client,server,submit}_{usage,help}.rst`.
- `share/bash_completion.d/hs:529-668`; `share/zsh/site-functions/_hs:100-104`, `:475`, `:510`,
  `:557`.
