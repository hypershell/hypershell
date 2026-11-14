# GOAL — Every duration option takes the forms the timeouts take

> **Origin spec.** The *what* and *why* — the locked contract `hs-review` grades against.
> The *how* lives in [`PLAN.md`](PLAN.md) and [`TECH.md`](TECH.md) (written by `hs-plan`).
> Keep this at the right altitude: solved and bounded, but not over-specified — leave design
> freedom for the plan. Edit requirements here; do **not** silently drift them during build.

- **slug:** duration-units-remaining
- **kind:** fix
- **appetite:** small

## Problem

Since `size-and-duration-units` (2.9.0a7, [`spec/size-and-duration-units/`](../size-and-duration-units/GOAL.md))
the task and client timeouts read a duration with units: `-W 1h30m`, `-W 1.5h`, `-T 1-12`. Every
other option that takes a duration still takes only a bare number of seconds, so `hs cluster
--period 5m` is an argument error while `hs cluster -W 5m` works. That cycle deferred them on
purpose, to keep its change small enough for the 2.8 line (its Non-goals; maintainer ruling
2026-11-11). **Pre-existing** on `develop` (`6c46cbd`), read from the source.

The options, on `develop`, with the configuration key that supplies each default:

| Option | Commands (declaration) | Declared as | Default from |
|---|---|---|---|
| `-w`, `--bundlewait` | `hs submit` (`submit.py:1280`), `hs server` (`server.py:1665`), `hs client` (`client.py:1604`), `hs cluster` (`cluster/__init__.py:151`) | `type=int` | `submit.bundlewait` (`core/config.py:132`) |
| `-S`, `--signalwait` | `hs client` (`client.py:1620`), `hs cluster` (`cluster/__init__.py:246`) | `type=int` | `task.signalwait` (`core/config.py:127`) |
| `-P`, `--period` | `hs server` (`server.py:1691`), `hs cluster` (`cluster/__init__.py:257`) | `type=int` | `autoscale.period` (`core/config.py:182`) |
| `-Q`, `--poll` | `hs server` (`server.py:1694`), `hs cluster` (`cluster/__init__.py:174`) | `type=int` | `server.poll` (`core/config.py:145`) |
| `-d`, `--delay-start` | `hs client` (`client.py:1607`), `hs cluster` (`cluster/__init__.py:154`) | `type=float` | none (`client.DEFAULT_DELAY`, `client.py:1132`) |
| `-n`, `--interval` | `hs wait` (`task.py:348`), `hs run` (`task.py:433`) | `type=int` | none (`task.DEFAULT_INTERVAL`, `task.py:310`) |

The help text of each calls the value `SEC` or "seconds" (`-Q` calls it `NUM`), and so do the
`docs/_include/*_help.rst` snippets, the configuration reference
(`docs/_include/config_param_ref.rst`) and the zsh and bash completions.

`autoscale.period` is also read directly by `hs relay`, as the period of its site fleet
(`relay/site.py:85`, `relay/__init__.py:506`), without going through an option.

**`--delay-start` has a meaning of its own for a negative number.** `-d 30` waits 30 seconds before
the client starts; `-d -600` waits a uniformly random time between zero and 600 seconds
(`client.py:1333-1342`), which the docs recommend for staggering a large launch
(`docs/_include/client_help.rst:64-67`, written there as `--delay-start=-600`). It takes fractional
seconds (`-d 0.5`). The strict duration parser refuses a sign and rounds up to whole seconds
(`core/types.py:140-147`), so it cannot be applied to this option as it stands.

**Python's own argument parser decides what follows `-d`.** On Python 3.11, 3.12 and 3.13 (the
package supports `>=3.11`), a separate word that starts with `-` and is not a plain negative number
is taken for an option: `-d -10m` fails with "expected one argument" before HyperShell sees the
value, while `--delay-start=-10m` and `-d-10m` reach it. Python 3.14 accepts `-d -10m`. Observed with
a bare `argparse` parser on each version, 2026-11-14.

## Outcome / vision

Every option that takes a duration takes it the same way, with the same forms and the same refusal,
on the command line, in a configuration file and in an environment variable. `--period 5m`,
`-w 30s`, `hs wait -n 1m` and `--delay-start=-10m` say what they mean, and every value that works
today still means what it means today.

## Acceptance criteria (the contract)

- **R1** — WHEN `-w`/`--bundlewait` (`hs submit`, `hs server`, `hs client`, `hs cluster`),
  `-S`/`--signalwait` (`hs client`, `hs cluster`), `-P`/`--period` (`hs server`, `hs cluster`),
  `-Q`/`--poll` (`hs server`, `hs cluster`) or `-n`/`--interval` (`hs wait`, `hs run`) is given a
  duration in any form `-W` takes — the units `s`, `m`, `h`, `d`, alone or combined, whole or
  fractional (`30s`, `5m`, `1h30m`, `1.5h`), or a Slurm form (`MM:SS`, `HH:MM:SS`, `D-HH[:MM[:SS]]`)
  — HyperShell SHALL use that many seconds, as `-W` reads the same text (`--period 5m` → 300,
  `-w 1:30` → 90).
- **R2** — WHEN `-d`/`--delay-start` (`hs client`, `hs cluster`) is given a duration in a form R1
  names, the client SHALL wait that long before it starts; WHEN the same form carries one leading
  `-` (`--delay-start=-10m`, `--delay-start=-1:30`), the client SHALL wait a uniformly random time
  between zero and that duration, as a negative number does today. Fractional seconds SHALL be kept,
  not rounded (`--delay-start=1.5s` waits 1.5 seconds).
- **R3** — WHEN the configuration key or environment variable behind an option in R1
  (`submit.bundlewait`, `task.signalwait`, `autoscale.period`, `server.poll`; for example
  `HYPERSHELL_AUTOSCALE_PERIOD=5m`, or `period = "5m"` under `[autoscale]`) holds a duration in a form
  R1 names, every command that reads that key SHALL use the same number of seconds — `hs relay`'s
  site fleet period included.
- **R4** — IF a value given to any option, key or variable in R1–R3 is not a duration in full
  (`5MB`, `1hr`, `2hours`, `1w`, `30m1h`), or carries a sign on any option other than
  `--delay-start` (`-w -5`, `-P +60`), THEN the command SHALL exit non-zero with the error `-W` gives
  for the same text, naming the value and the forms accepted, and SHALL NOT read any value by its
  leading part.
- **R5** — Every value accepted today, other than the signed values R4 refuses, SHALL be read as the
  same value as before: a bare integer is seconds on every option and key in R1–R3; on
  `--delay-start` a bare number, whole or fractional, with or without a leading `-`, waits exactly as
  it does today (`-d 30`, `-d 0.5`, `-d -600`, `--delay-start=-600`); and a number (not a string) in a
  configuration file keeps today's meaning.
- **R6** — The client command line that `hs cluster` and `hs relay` build to launch a client SHALL
  carry `--bundlewait`, `--signalwait` and `--delay-start` as bare numbers of seconds, a negative
  delay included, so that a client of an earlier release launched from it still accepts them.
- **R7** — The help text, the `docs/_include` help snippets, the configuration reference entries for
  the keys in R3, and the zsh and bash completion descriptions of each option in R1 and R2 SHALL state
  which duration forms it takes; those of `--delay-start` SHALL state the random form and write it
  attached to its option (`--delay-start=-10m`), the spelling every supported Python accepts.
- **R8** — Tests SHALL cover each option in R1 and R2 with a unit form, a Slurm form, a bare integer
  and a refusal; the fixed and random `--delay-start` forms, with a fraction; a configuration or
  environment string for each key in R3, including `hs relay`'s read of `autoscale.period`; and the
  launched client command line of R6.

## Non-goals (no-gos)

- **Duration keys that no option reads through its parser.** `server.evict`, `client.heartrate`,
  and the relay's own `relay.bundlewait`, `relay.poll`, `relay.retention`, `relay.timeout`,
  `relay.upstream.heartrate`, `relay.upstream.evict_hint`, `relay.upstream.deadline` and
  `relay.upstream.backoff` (`core/config.py:146`, `167`, `204-206`, `217`, `223-226`) are read
  directly by the code that uses them and keep taking a number. Deferred to a follow-up seed, to be
  filed once this deferral is signed off. (`size-and-duration-units` PLAN §5 already notes
  `relay.timeout` is forwarded to site clients as text.)
- Which key `hs client -w` reads (`submit.bundlewait` today, `[client].bundlewait` in the docs):
  [`issues/client-bundlesize-config-key.md`](../../issues/client-bundlesize-config-key.md).
- Making the two-word `-d -10m` work on a Python whose argument parser takes `-10m` for an option
  (3.11–3.13). The attached spelling is the documented one (R7); a bare negative number (`-d -600`)
  works as two words on every version, as today.
- A sign on any duration other than `--delay-start`, and any spelling `-W` does not take (`ms`,
  `sec`, `min`, `hr`, `w`). The `size-and-duration-units` grammar is the grammar.
- `-R`/`--ratelimit` (a rate, tasks per second) and `-F`/`--factor`: not durations.
- How these values are displayed or logged, and what the options do once read.
- The 2.8 line. This ships on 2.9 only.

## Clarifications

Proposed in shaping on 2026-11-14 by the shaping agent; each is **pending the maintainer's sign-off
via the foreman** before `/hs-plan`.

- **Q:** Which options are in scope? — **A:** The seed's list, plus two it missed:
  `-P`/`--period` is on `hs server` as well as `hs cluster`, and `-Q`/`--poll` on `hs server` and
  `hs cluster` is a duration in seconds that the seed did not name. Both are in, so "every option
  that takes a duration" holds. `hs relay` has no duration option; its duration keys are a non-goal.
- **Q:** How is `--delay-start`'s negative form kept? — **A:** A leading `-` on any form R1
  names is the random form (`-10m`, `-1:30`, `-0.5`); without it the wait is fixed. A bare number reads
  exactly as today, fraction and sign included. Fractions are kept rather than rounded up, because a
  delay of zero means "no delay" (not "no limit", the reason the timeouts round up) and the option
  takes fractions today. The help and docs write the random form attached (`--delay-start=-10m`), as
  the docs already write `--delay-start=-600`.
- **Q:** What about a signed value on the other options (`-w -5`, `-P +60`, `-n -1`), which `type=int`
  accepts today? — **A:** Refused, as `-W -60` has been since 2.9.0a7. None has a meaning
  anyone relies on; the `size-and-duration-units` review dropped the same case for `-W` (its REVIEW,
  Observations). This is the one exception to R5.
- **Q:** Do configuration keys and environment variables follow the same parser? — **A:**
  Yes for the four keys behind the options (R3). `size-and-duration-units` showed a string default
  reaches an option's parser (its PLAN, decision 1, verified in its REVIEW with
  `HYPERSHELL_TASK_TIMEOUT=2h`). `autoscale.period` is also read directly by `hs relay`, so R3 names
  it, or a `period = "5m"` that `hs cluster` accepts would break the relay.
- **Q:** Which release, and which marker? — **A:** The next 2.9.0 pre-release only, no 2.8
  cherry-pick; the changed help includes and configuration entries carry `.. versionchanged:: 2.9.0`
  (AGENTS.md § Docs: behaviour that differs from the stable line).
- **Left for `/hs-plan` (not clarification markers):**
  - How `--delay-start` is parsed (a signed, unrounded variant of the duration parser, or a wrapper)
    and how its error names the random form.
  - Where `hs relay` converts `autoscale.period`, and whether a string reaches `hs cluster`'s or
    `hs server`'s autoscaler by any path other than the option.
  - Whether `-Q`/`--poll` should take fractions below one second (the parser rounds up; `server.poll`
    is a whole number by default and the relay's `poll` is not in scope).
  - The completion example values (the zsh and bash completions offer `-60 -600` for `-d`).

**Ruled at the GOAL gate** (2026-11-14): approved by the foreman, gate judge under the maintainer's
mandate. All five proposed answers are confirmed: `-P` on `hs server` and `-Q`/`--poll` join the
scope; the random delay is a leading `-` with fractions kept; signed values are refused as `-W`
refuses them (the one compatibility break, stated in the user-facing note); `hs relay`'s read of
`autoscale.period` is in scope; 2.9 only, with `versionchanged:: 2.9.0`. The duration keys that no
option reads are deferred to `issues/duration-units-config-keys.md`, filed in this commit. The
contract is locked; `hs-plan` designs against it.

## Evidence plan

What `hs-review` should be able to run against the branch:

- A parser table for each option in R1 and R2: unit, Slurm, bare-integer and refusal forms, each
  read through the command's own argument parsing, and the exit status and message of a refusal.
- `.agents/factory/bin/temp_site.sh`: `hs submit -w 1m`, `hs cluster --no-db -N1 -w 30s -S 5s
  -P 2m -Q 1m --delay-start=-2s` and `hs client --delay-start=0.5s` complete a small task file;
  `hs wait -n 1s` and `hs run -n 1s` return; a refused `--period 5MB` exits 2 naming the value.
- The random delay: with a stubbed sleep or a short bound, `--delay-start=-2s` draws within `[0, 2]`
  and `--delay-start=2s` waits 2.
- `HYPERSHELL_AUTOSCALE_PERIOD=5m`, `HYPERSHELL_SUBMIT_BUNDLEWAIT=30s`,
  `HYPERSHELL_TASK_SIGNALWAIT=5s`, `HYPERSHELL_SERVER_POLL=1m`, and the same as TOML strings in a
  site `config.toml`, reach each command as seconds; `hs relay`'s site fleet period reads the same.
- The command line `client_launch` builds from `hs cluster -d -10m -w 1m -S 5s`, compared with what
  the 2.9.0a8 client accepts (the existing launch contract test).
- The test suite under Python 3.11 and 3.14, so the attached and two-word `-d` spellings are both
  exercised where each applies; the Sphinx `-W` build.

## Related materials

- Seed: [`issues/duration-units-remaining-options.md`](../../issues/duration-units-remaining-options.md)
- Prior cycle: [`spec/size-and-duration-units/`](../size-and-duration-units/GOAL.md) — the parser
  `parse_time` and `UnitError` (`src/hypershell/core/types.py:123-147`), the forms R1 names, and the
  config-default path (its PLAN, decision 1).
- Source: option declarations as tabled above; the delay logic `ClientThread.wait_start`
  (`src/hypershell/client.py:1333-1342`); the launched command line `client_options`
  (`src/hypershell/cluster/launch.py:432-467`); the relay's period (`src/hypershell/relay/site.py:85`).
- Docs: `docs/_include/{submit,server,client,cluster,task_wait,task_run}_help.rst`,
  `docs/_include/config_param_ref.rst`; completions `share/zsh/site-functions/_hs`,
  `share/bash_completion.d/hs`.
- Related seed: [`issues/client-bundlesize-config-key.md`](../../issues/client-bundlesize-config-key.md).
