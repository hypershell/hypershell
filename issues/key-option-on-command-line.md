---
status: adopted:key-option-refused
kind: fix
appetite: small
lane: public
---

# The queue key can still be given on a command line

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

Since 2.9.0a5 and 2.8.5, `hs cluster` hands its run key to the clients it launches in their
environment, never on a command line, where a process listing shows it to every user on the host.
The same key can still be put on a command line by hand, through the `-k`/`--auth` option of three
commands:

- `hs client -k KEY` (`src/hypershell/client.py:1594`)
- `hs server -k KEY` (`src/hypershell/server.py:1623`)
- `hs submit -q -k KEY` (`src/hypershell/submit.py:1308`)

The 2.8 line has the same three (`client.py:1527`, `server.py:1418`, `submit.py:942` on
`master`).

So the hardening covers the clients a cluster launches and leaves out the processes a person
starts, which are the long-lived ones: a persistent `hs server` and the `hs client` processes that
join it hold the same key on their command lines for as long as they run. It is also what a
reader copies. The help text, the usage lines, the man pages, the shell completions, and the
tutorials all show the option.

Nothing needs the option. Each of the three commands already takes the key without it:

- `hs client` reads `config.server.auth` when no `-k` is given (`client.py:1593`, and its `run`).
- `hs server` and `hs submit -q` default the option to `config.server.auth`
  (`server.py:1622`, `submit.py:1304`).
- `config.server.auth` comes from `HYPERSHELL_SERVER_AUTH` or from `server.auth` in a
  configuration file.

`hs relay` already has no key option at all, by design: its upstream key comes from
`[relay.upstream]` only.

## Why it was deferred

The hardening that shipped (#80, #81) was scoped to the clients that `hs cluster` launches, and
left a client started by hand as it was. **Pre-existing** in `develop` and in the 2.8 line.

## Outcome / vision

On 2.8, the option keeps working, for compatibility, and says loudly each time that it should not
be used and what to use instead. On 2.9, the option is gone: giving it is an error that names the
environment variable and the configuration key. No documentation, help text or completion shows a
key on a command line.

## Sketch of the acceptance criteria

For the 2.8 line:

- **R1** — WHEN `-k` or `--auth` is given to `hs client`, `hs server` or `hs submit`, the command
  SHALL log a warning that the key is visible on its command line, and that
  `HYPERSHELL_SERVER_AUTH` or `server.auth` is the way to give it, and SHALL then run as before.
- **R2** — The warning SHALL never contain the key.

For 2.9:

- **R3** — WHEN `-k` or `--auth` is given to `hs client`, `hs server` or `hs submit`, the command
  SHALL exit with a bad-argument error that names `HYPERSHELL_SERVER_AUTH` and `server.auth`, and
  SHALL not start.
- **R4** — The error SHALL never contain the key.
- **R5** — A key given through `HYPERSHELL_SERVER_AUTH` or `server.auth` SHALL work exactly as
  before, on all three commands, and so SHALL the clients that `hs cluster` and `hs relay` launch.
- **R6** — The usage lines, help text, man pages, shell completions and documentation SHALL no
  longer offer the option, and every example SHALL give the key through the environment or the
  configuration. On 2.8 they SHALL mark it as deprecated.
- **R7** — Tests SHALL cover the warning on 2.8, the refusal on 2.9, and a key from each remaining
  source on each command.

## Notes

- **The maintainer's direction** (2026-11-11): for 2.8.x keep `-k` for compatibility but warn
  loudly not to use it; for 2.9.x do not allow `-k` at all.
- Size of the change, on `develop`: the option is declared in three places; 49 lines in 14 test
  files pass it; 12 lines of documentation outside the man pages show it; the Bash completions and
  three man pages carry it. The tests are most of the work.
- `src/hypershell/cluster/launch.py:122-123` redacts `-k` and `--auth` from logged client
  commands. With the option gone on 2.9 that redaction has nothing left to do; whether it stays as
  a guard is for `/hs-plan`.
- Open question for shaping: whether 2.9 offers a replacement on the command line that names a
  file, not a key (`--auth-file PATH`), for a site that will not use the environment. The
  configuration file already serves that case.
- Open question for shaping: `hs submit -q` documents that it "requires `-H`, `-p`, and `-k`".
  Its help text changes with this.
- Open question for `/hs-plan`: the Python API's `auth=` parameters are not a command line and
  are not in scope; say so in the GOAL so nobody removes them.
- Related: `spec/server-auth-variant-collision/` (#97). The `auth_env` and `auth_eval` forms are
  the other way to keep a key out of a file; that cycle made them work in a configuration file.
- Related: [`env-config-key-mapping.md`](env-config-key-mapping.md), on which configuration keys
  an environment variable can reach.
- Related: `spec/authkey-handling-hardening/` and `spec/launch-hint-followups/` (#80, #81), where
  the launched-client hardening was scoped.
- The website's tutorial and its 2.9 release notes were moved to the environment-variable style
  on 2026-11-11. Earlier release notes keep their examples as published.
- Found by: the maintainer, reading the 2.9 release notes (2026-11-11).
