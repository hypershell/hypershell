# GOAL — `hs client` dials `server.host` when no `-H` is given

> **Origin spec.** The *what* and *why* — the locked contract `hs-review` grades against.
> The *how* lives in [`PLAN.md`](PLAN.md) and [`TECH.md`](TECH.md) (written by `hs-plan`).
> Keep this at the right altitude: solved and bounded, but not over-specified — leave design
> freedom for the plan. Edit requirements here; do **not** silently drift them during build.

- **slug:** client-host-default-from-bind
- **kind:** fix
- **appetite:** small  ·  *One shared rule for the option defaults of `hs client` and `hs submit
  --queue` and for the queue's configuration loader, a few tests, and the `[server]`, `hs client`
  and `hs submit` docs. No protocol, schema or server change.*
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
clients on other nodes. Failing that it dials `server.advertise`, the address `hs cluster` already
tells the clients it launches, and failing that `server.bind` when it is one specific address, so a
configuration that sets only `bind` keeps working. `-H` still wins. One rule answers "where does a
client dial" for `hs client`, `hs submit --queue` and the queue's loader, and the configuration
reference and the `--help` of both commands state it.

## Acceptance criteria (the contract)

- **R1** — WHEN `hs client` starts without `-H/--host` and `server.host` is given in any
  configuration layer (a system, user or local file, or `HYPERSHELL_SERVER_HOST`), the client SHALL
  dial `server.host`, whatever `server.advertise` and `server.bind` are. The seed's case,
  `bind = "0.0.0.0"` with `host` set to another name, dials that name.
- **R2** — WHEN `hs client` is given `-H/--host ADDR`, it SHALL dial `ADDR`, whatever `server.host`,
  `server.advertise` and `server.bind` are.
- **R3** — WHEN `hs client` starts without `-H`, the client SHALL dial the first of:
  1. `server.host`, if any configuration layer gives it (R1);
  2. `server.advertise`, if any layer gives it;
  3. `server.bind`, if that is one specific address;
  4. `localhost`.

  So a configuration that gives neither `host` nor `advertise` dials `server.bind` when it names one
  address, as today, and `localhost` when it binds every interface (`0.0.0.0`, or empty) or is not
  given. A key counts as given by the layer that decides it, not by its value, so a `host` written as
  `localhost` is dialled as `localhost`. The marker that stands for an unset `advertise` is never
  dialled.
- **R4** — WHEN `hs submit --queue` starts without `-H`, and WHEN `QueueConfig.load()` builds a
  connection from the configuration, each SHALL choose the address by the same rule as R1 and R3,
  through one shared implementation and one documented statement of the rule.
- **R5** — No client-side dial path other than R3's rule SHALL take its address from `server.bind`
  or `server.advertise`. `hs relay`'s upstream link SHALL keep reading `relay.upstream.host`
  (`-H/--upstream-host`), never `[server]`. The Python API defaults, `ClientThread.new` /
  `run_client` (`DEFAULT_HOST`) and the `QueueConfig` field default, SHALL keep their built-in value
  `localhost` and SHALL NOT read the loaded configuration, and no client-side default SHALL take its
  value from `bind`.
- **R6** — The server side SHALL be unchanged: `hs server -H/--bind` and `hs cluster -H/--bind`
  still default to `server.bind`, and `hs server`'s Python default bind (`DEFAULT_BIND`) stays
  `localhost`. `server.advertise` keeps its meaning for launched clients: the address a `cluster` or
  relay tells the clients it launches (the `advertised_host` rule) is unchanged. The client side now
  also reads `server.advertise`, as R3's second choice.
- **R7** — Tests SHALL show, through `hs client`'s option defaults with the configuration given
  (by file or environment): R1's seed case; R2 over a configured `server.host` and
  `server.advertise`; each branch of R3 in its order (`host` over `advertise`, `advertise` over a
  specific `bind`, a specific `bind`, a wildcard `bind`, nothing given); and that `server.host` given
  as `localhost` is dialled as `localhost` even when `server.advertise` or `server.bind` names another
  address. Tests SHALL show `hs submit --queue`'s `-H` default and `QueueConfig.load()` following the
  same rule (R4).
- **R8** — The docs SHALL state the rule: the `[server]` `.host`, `.advertise` and `.bind` entries in
  `docs/_include/config_param_ref.rst` (`.host`'s default becomes the R3 rule, as `.advertise`'s is
  "by `.bind`", and `.advertise` and `.bind` say that clients fall back to them), and `-H/--host` in
  `hs client --help` and `docs/_include/client_help.rst`, and in `hs submit --help` and
  `docs/_include/submit_help.rst`. Each change carries a `.. versionchanged:: 2.9.0` marker, placed
  per `AGENTS.md` (beside the `.. include::` for generated help). The user-facing note travels in
  the PR's squash commit body, which `hs-release` drafts the 2.9.0 release notes from; it SHALL name
  the change and say who it affects: a configuration that gives `host` now has `hs client` dial it;
  one that gives `advertise` but not `host` now has `hs client`, `hs submit --queue` and
  `QueueConfig.load()` dial `advertise`; one that gives only a specific `bind` now has
  `hs submit --queue` and `QueueConfig.load()` dial it, where they dialled `localhost`. Docs name
  top-level commands only and never show a key on the command line.

## Non-goals (no-gos)

- **Server-side addresses.** `server.bind`'s default and meaning and `hs server`/`hs cluster` `-H`
  are unchanged. `server.advertise` keeps its meaning for launched clients, and the
  `advertised_host` rule is unchanged (R6); the client side now reads `advertise` as R3's fallback,
  which is this fix, not a change to the key.
- **The relay.** Its upstream link already reads `relay.upstream.host` only; its site clients are
  launched with `-H`. No change.
- **`hs wait`, `hs info`, `hs list`, `hs run`, `hs update`.** They read the database; none dials the
  queue.
- **This host's own name as a fallback.** `advertised_host` falls back to `HOSTNAME` for a wildcard
  bind because it runs on the server's host; a client runs elsewhere, so its own name would be wrong.
  A wildcard bind with neither `host` nor `advertise` given dials `localhost`.
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
below carried options and a recommendation, and the markers in R3, R4 and R5 blocked `/hs-plan`.
They were answered at the GOAL gate the same day (below), and R3–R8 and the Non-goals were rewritten
to the rulings.

- **Q1 (seed R4) — compatibility. Resolved 2026-11-23** (maintainer): **a wider rule than (b).**
  With no `-H`, a client dials the first of `server.host` if any layer gives it, `server.advertise`
  if any layer gives it, `server.bind` if it is one specific address, and `localhost` (R3). The
  maintainer was given the history (Related materials): 2.1 dialled the built-in `localhost`, 2.2.0
  switched to `server.bind` so that configuration reached the client, 2.8.0 added `server.host` for
  clients and `hs client` never read it, and 2.9 added `advertise`, the address `hs cluster`
  (`server.advertise`) and `hs relay` (`relay.advertise`) tell the clients they launch. One rule answers "where does a client dial"
  consistently with launched clients, and configurations that set only `bind` keep working. As
  shaped: a configuration that sets `bind` to a real name and leaves `host` unset dials `bind` today.
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
- **Q2 — the Python API defaults. Resolved 2026-11-23** (foreman, under the maintainer's mandate):
  **option (a).** The built-in `localhost` stays the API default, and no client-side default names
  `bind` (R5). As shaped: `ClientThread.new`/`run_client` default to `DEFAULT_HOST`, which
  is `QueueConfig.host`, whose field default is the *built-in* `default.server.bind`
  (`core/queue.py:1089`); `hs server`'s `DEFAULT_BIND` (`server.py:1142`) and `hs submit`'s help
  default (`submit.py:1497`) read the same field. All are `localhost`, and none reads user
  configuration, so `:1089` is not a live bug.
  - **(a)** Keep the built-in value `localhost`; only the source changes, so no client-side default
    names `bind` (the server keeps its own built-in `bind` default). No behaviour change.
    *Recommended.*
  - **(b)** Make the Python API defaults follow the loaded configuration (R1/R3). A behaviour change
    for library callers, read at import time, beyond a small fix; callers who want configuration
    already have `QueueConfig.load()`.
  - **(c)** Leave the Python API untouched, `:1089` included.
- **Q3 — one rule or two. Resolved 2026-11-23** (maintainer): **one rule.** `hs client`,
  `hs submit --queue` and `QueueConfig.load()` share one helper and one documented rule (R4). As
  shaped: under Q1 (b), `hs submit --queue` and `QueueConfig.load()` read
  `server.host` with no fallback, so they would differ from `hs client` for a configuration that sets
  `bind` to a specific address and leaves `host` unset: the client dials `bind`, `hs submit --queue`
  dials `localhost`, where that server is not listening.
  - **(a)** Apply the R3 rule to both, so `.host` has one documented meaning. Changes `hs submit
    --queue` only in a case that cannot reach the server today. *Recommended.*
  - **(b)** Apply it to `hs client` only, and document the difference.
- **Q4 — appetite.** The seed's `appetite: small` stands; it is `kind: fix` and stays within one
  shared rule, its three readers and docs (resolved 2026-11-23, from the seed).

**Ruled at the GOAL gate** (2026-11-23). Approved by the foreman, gate judge under the maintainer's
mandate, with the maintainer's own rulings on Q1 and Q3.

- **Q1 (maintainer, 2026-11-23):** with no `-H`, a client dials the first of `server.host` (any
  layer), `server.advertise` (any layer), `server.bind` when it is one specific address, and
  `localhost`. R3 is rewritten to this rule. `advertise`'s own meaning for launched clients is
  unchanged; the client side now reads it as a fallback (R6, Non-goals).
- **Q2:** option (a). The built-in `localhost` stays the Python API default, and no client-side
  default names `bind`.
- **Q3 (maintainer, 2026-11-23):** `hs client`, `hs submit --queue` and `QueueConfig.load()` share
  one helper and one documented rule.
- **Q4:** `appetite: small` stands.
- The contract is locked; `hs-plan` designs against it.

## Related materials

- Seed: [`issues/client-host-default-from-bind.md`](../../issues/client-host-default-from-bind.md)
  (ROADMAP entry "`hs client` dials `server.bind`, not `server.host`").
- Found by a review of a site user guide's workflow-automation chapter, 2026-11-23; the guide passes
  `-H` explicitly until this lands.
- History of the client's default address:
  - 2.1: `hs client` dialled the built-in `localhost` (`QueueConfig.host`).
  - 2.2.0 (`3a745445`): it switched to `server.bind`, so that configuration reached the client.
  - 2.8.0 (`415d21f1`): `server.host` was added to the defaults as the key "used by clients";
    `hs client` never read it.
  - 2.9: `advertise` was added, the address launched clients are told to dial: `server.advertise`
    for `hs cluster` (#107) and `relay.advertise` for `hs relay` (#116). A relay does not read
    `server.advertise`.
- Source: `src/hypershell/client.py:1202`, `:1657`, `:1747`; `src/hypershell/submit.py:1497`,
  `:1614`; `src/hypershell/core/queue.py:1089`, `:1107`; `src/hypershell/server.py:1142`;
  `src/hypershell/core/config.py:142-144`; `src/hypershell/cluster/launch.py:85-107`, `:526`;
  `src/hypershell/relay/upstream.py:160-166`.
- Docs: `docs/_include/config_param_ref.rst` (`[server]` `.bind`, `.host`, `.advertise`),
  `docs/_include/client_help.rst` (`-H`), `docs/_include/submit_help.rst` (`-H`),
  `docs/_include/cluster_help.rst` (`-H`, `--advertise`), `docs/_include/config_intro.rst` (empty
  values), `docs/security.rst` (TLS deployment examples).
- Prior art: `spec/relay-advertise/GOAL.md` and #107 (the `advertise` rule).
