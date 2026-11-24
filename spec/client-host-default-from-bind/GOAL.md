# GOAL — `hs client` dials `server.host` when no `-H` is given

> **Origin spec.** The *what* and *why* — the locked contract `hs-review` grades against.
> The *how* lives in [`PLAN.md`](PLAN.md) and [`TECH.md`](TECH.md) (written by `hs-plan`).
> Keep this at the right altitude: solved and bounded, but not over-specified — leave design
> freedom for the plan. Edit requirements here; do **not** silently drift them during build.

- **slug:** client-host-default-from-bind
- **kind:** fix
- **appetite:** small  ·  *One option default in `hs client`, the matching rule in the queue's
  configuration loader, a few tests, and the `[server]` and `hs client` docs. No protocol, schema
  or server change.*
- **target:** 2.9.0  ·  *from the seed; planned for 2.9.0a10. It blocks a site user guide's
  persistent-server example, which passes `-H` by hand until this lands.*

## Problem

The configuration has two server addresses with separate, documented jobs
(`docs/_include/config_param_ref.rst`, `[server]`): `.bind` is the address the server listens on,
and `.host` is "the address that `client` instances and queue-direct `submit --queue` dial to reach
the server". `hs client` never reads `.host`. On `develop` (`1dd734d7`) its `-H/--host` default is
`config.server.bind` (`src/hypershell/client.py:1657`), and `run()` dials `(self.host, self.port)`
(`:1747`). `hs submit --queue` reads `config.server.host` (`src/hypershell/submit.py:1614`), and so
does the queue's own loader, `QueueConfig.load()` (`src/hypershell/core/queue.py:1107`).

So the documented remote setup fails. A persistent server on a login node, with `bind = "0.0.0.0"`
and `host = "login00.example.edu"` in a shared configuration file, and clients started by batch jobs
reading that same file: every client dials `0.0.0.0`, which is its own node, and gets
`ConnectionRefusedError`. Reproduced for this shaping on `develop` (`1dd734d7`), by environment:

| `SERVER_BIND` | `SERVER_HOST` | `hs client` default (`ClientApp.host`) | `QueueConfig.load().host` |
|---|---|---|---|
| `0.0.0.0` | `login00.example.edu` | `0.0.0.0` | `login00.example.edu` |
| `10.0.0.5` | not given | `10.0.0.5` | `localhost` |
| not given | `localhost` | `localhost` | `localhost` |

The defect is **pre-existing**: `hs client` has defaulted to `server.bind` since 2.2.0 (`3a745445`),
and every 2.8 release does it (`2.8.7:src/hypershell/client.py:1520`). The `[server] .host` row was
added to the configuration reference only in the 2026-07-05 options audit (`1d9ccb6e`, unreleased);
the 2.8 reference documents `.bind` alone. A 2.8 user who wanted configured clients to reach a
remote server therefore had one documented key that worked, `bind` set to the server's real name,
and that setup works today only because the client dials `bind`. Changing the default must not break
it silently (see Clarifications, Q1).

The clients a `cluster` or a relay launches are unaffected: every launch passes `-H` explicitly
(`src/hypershell/cluster/launch.py:526`), computed by the 2.9 `advertised_host` rule (`:89-107`):
`advertise` when given, else the bind address when it is one specific address, else this host's own
name. A local `cluster` dials `localhost` explicitly (`src/hypershell/cluster/local.py:251`).

## Outcome / vision

A client started without `-H` dials `server.host` whenever the configuration gives it, as the
reference says, so one shared configuration file serves a server bound to every interface and its
clients on other nodes. `-H` still wins. A configuration that never gives `server.host` keeps
working as it does today. The configuration reference and `hs client --help` state the one rule.

## Acceptance criteria (the contract)

- **R1** — WHEN `hs client` starts without `-H/--host` and `server.host` is given in any
  configuration layer (a system, user or local file, or `HYPERSHELL_SERVER_HOST`), the client SHALL
  dial `server.host`, whatever `server.bind` is. The seed's case, `bind = "0.0.0.0"` with `host` set
  to another name, dials that name.
- **R2** — WHEN `hs client` is given `-H/--host ADDR`, it SHALL dial `ADDR`, whatever `server.host`
  and `server.bind` are.
- **R3** — WHEN `hs client` starts without `-H` and `server.host` is not given in any layer, the
  client SHALL dial `server.bind` if that is one specific address, and `localhost` (the `server.host`
  default) if `server.bind` binds every interface (`0.0.0.0`, or empty). With neither key given it
  dials `localhost`, as today. [NEEDS CLARIFICATION: Q1 — this is option (b), recommended. Under
  option (a) R3 reads instead: "…the client SHALL dial `localhost`, the `server.host` default,
  whatever `server.bind` is."]
- **R4** — WHEN `hs submit --queue` starts without `-H`, and WHEN `QueueConfig.load()` builds a
  connection from the configuration, each SHALL choose the address by the same rule as R1 and R3.
  [NEEDS CLARIFICATION: Q3 — recommended: one rule for every configured client-side dial. Under
  option (a) of Q1 they already follow it and nothing changes for them.]
- **R5** — No other client-side dial path SHALL take its address from `server.bind`. `hs relay`'s
  upstream link SHALL keep reading `relay.upstream.host` (`-H/--upstream-host`), never `[server]`.
  The Python API defaults, `ClientThread.new` / `run_client` (`DEFAULT_HOST`) and the `QueueConfig`
  field default, SHALL keep their built-in value `localhost` and SHALL NOT read the loaded
  configuration. [NEEDS CLARIFICATION: Q2 — recommended option (a).]
- **R6** — The server side SHALL be unchanged: `hs server -H/--bind` and `hs cluster -H/--bind`
  still default to `server.bind`; `hs server`'s Python default bind (`DEFAULT_BIND`) stays
  `localhost`; the address a `cluster` or relay tells its launched clients is unchanged.
- **R7** — Tests SHALL show, through `hs client`'s option defaults with the configuration given
  (by file or environment): R1's seed case; R2 over a configured `server.host`; each branch of R3;
  and that `server.host` given as `localhost` is dialled as `localhost` even when `server.bind` names
  another address. Where R4 changes `hs submit --queue`, a test SHALL show its default too.
- **R8** — The docs SHALL state the rule: the `[server]` `.host` and `.bind` entries in
  `docs/_include/config_param_ref.rst` (`.host`'s default becomes the R3 rule, as `.advertise`'s is
  "by `.bind`"), and `-H/--host` in `hs client --help` and `docs/_include/client_help.rst`, and in
  `hs submit --help` and `docs/_include/submit_help.rst` if R4 changes it. Each change carries a
  `.. versionchanged:: 2.9.0` marker, placed per `AGENTS.md` (beside the `.. include::` for
  generated help). The user-facing note travels in the PR's squash commit body, which `hs-release`
  drafts the 2.9.0 release notes from; it SHALL name the change and say who it affects (under Q1
  option (a): a configuration that sets `bind` to a real name and leaves `host` unset now dials
  `localhost`, and must set `host`). Docs name top-level commands only and never show a key on the
  command line.

## Non-goals (no-gos)

- **Server-side addresses.** `server.bind`'s default and meaning, `hs server`/`hs cluster` `-H`,
  `server.advertise` and the `advertised_host` rule are unchanged (R6).
- **The relay.** Its upstream link already reads `relay.upstream.host` only; its site clients are
  launched with `-H`. No change.
- **`hs wait`, `hs info`, `hs list`, `hs run`, `hs update`.** They read the database; none dials the
  queue.
- **This host's own name as a fallback.** `advertised_host` falls back to `HOSTNAME` for a wildcard
  bind because it runs on the server's host; a client runs elsewhere, so its own name would be wrong.
  A wildcard bind with no `host` dials `localhost`.
- **An empty `HYPERSHELL_SERVER_HOST`.** `server.host` is not in the empty-is-default set
  (`EMPTY_UNSET`, `src/hypershell/core/config_schema.py:149`), unlike `server.bind`, so an empty
  variable holds an empty value. Whether it should count as not given is a separate question; record
  a seed if wanted.
- **Checking the address.** No resolution or reachability check, and no new hint on
  `ConnectionRefusedError`.
- **New keys or renames.** No `client.host`, no deprecation of either key.
- **IPv6** wildcard binds (`::`) — [`issues/ipv6-server-listener.md`](../../issues/ipv6-server-listener.md).

## Clarifications

The maintainer could not be asked during this shaping (unattended run, 2026-11-23). Each question
below carries options and a recommendation; the markers in R3, R4 and R5 block `/hs-plan` until they
are answered.

- **Q1 (seed R4) — compatibility.** A configuration that sets `bind` to a real name and leaves `host`
  unset dials `bind` today. [NEEDS CLARIFICATION: choose one.]
  - **(a)** Switch to `server.host` outright and name the break in the release notes. Simplest rule;
    `hs config get server.host` is always the address dialled. Breaks every 2.8 setup that used the
    only documented key (Problem), and a broken client fails only when it dials.
  - **(b)** Use `server.host` when it is given in any layer; when it is not, fall back to
    `server.bind` if that is one specific address, else `localhost`. *Recommended.*
  - **Can "unset" be told apart from "set to localhost"?** Yes. The configuration keeps its layers,
    and `config.which('server', 'host')` names the layer that gives the value: `default` when no file
    or variable gives it, and `env`, `user`, `system` or `local` when one does, even when the value
    is `localhost` (verified for this shaping with the three cases in Problem: `default` when not
    given, `env` for `HYPERSHELL_SERVER_HOST=localhost`; it is the same test `core/logging.py:1017` already uses for
    `logging.file`). So (b) keys off the layer, not the value, and a `host = "localhost"` written by
    the user is honoured.
  - **Why (b).** It is exactly today's behaviour for every configuration that does not give `host`
    (a wildcard bind dialled as `0.0.0.0` reaches the local host today; `localhost` is the same
    target, stated plainly), and it fixes every configuration that does. It mirrors the 2.9
    `advertise` rule users already meet in `hs cluster` ("by `.bind`" when one specific address).
    Its cost is one rule to document and an effective default that `hs config get` does not print;
    R8 covers the documentation.
- **Q2 — the Python API defaults.** `ClientThread.new`/`run_client` default to `DEFAULT_HOST`, which
  is `QueueConfig.host`, whose field default is the *built-in* `default.server.bind`
  (`core/queue.py:1089`); `hs server`'s `DEFAULT_BIND` (`server.py:1142`) and `hs submit`'s help
  default (`submit.py:1497`) read the same field. All are `localhost`, and none reads user
  configuration, so `:1089` is not a live bug. [NEEDS CLARIFICATION: choose one.]
  - **(a)** Keep the built-in value `localhost`; only the source changes, so no client-side default
    names `bind` (the server keeps its own built-in `bind` default). No behaviour change.
    *Recommended.*
  - **(b)** Make the Python API defaults follow the loaded configuration (R1/R3). A behaviour change
    for library callers, read at import time, beyond a small fix; callers who want configuration
    already have `QueueConfig.load()`.
  - **(c)** Leave the Python API untouched, `:1089` included.
- **Q3 — one rule or two.** Under Q1 (b), `hs submit --queue` and `QueueConfig.load()` read
  `server.host` with no fallback, so they would differ from `hs client` for a configuration that sets
  `bind` to a specific address and leaves `host` unset: the client dials `bind`, `hs submit --queue`
  dials `localhost`, where that server is not listening. [NEEDS CLARIFICATION: choose one.]
  - **(a)** Apply the R3 rule to both, so `.host` has one documented meaning. Changes `hs submit
    --queue` only in a case that cannot reach the server today. *Recommended.*
  - **(b)** Apply it to `hs client` only, and document the difference.
- **Q4 — appetite.** The seed's `appetite: small` stands; it is `kind: fix` and stays within one
  option default, one loader and docs (resolved 2026-11-23, from the seed).

## Related materials

- Seed: [`issues/client-host-default-from-bind.md`](../../issues/client-host-default-from-bind.md)
  (ROADMAP entry "`hs client` dials `server.bind`, not `server.host`").
- Found by a review of a site user guide's workflow-automation chapter, 2026-11-23; the guide passes
  `-H` explicitly until this lands.
- Source: `src/hypershell/client.py:1202`, `:1657`, `:1747`; `src/hypershell/submit.py:1497`,
  `:1614`; `src/hypershell/core/queue.py:1089`, `:1107`; `src/hypershell/server.py:1142`;
  `src/hypershell/core/config.py:142-144`; `src/hypershell/cluster/launch.py:85-107`, `:526`;
  `src/hypershell/relay/upstream.py:160-166`.
- Docs: `docs/_include/config_param_ref.rst` (`[server]` `.bind`, `.host`, `.advertise`),
  `docs/_include/client_help.rst` (`-H`), `docs/_include/cluster_help.rst` (`-H`, `--advertise`),
  `docs/_include/config_intro.rst` (empty values), `docs/security.rst` (TLS deployment examples).
- Prior art: `spec/relay-advertise/GOAL.md` and #107 (the `advertise` rule).
