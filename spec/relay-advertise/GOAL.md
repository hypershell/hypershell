# GOAL — `hs relay` names the address its site clients dial

> **Origin spec.** The *what* and *why* — the locked contract `hs-review` grades against.
> The *how* lives in [`PLAN.md`](PLAN.md) and [`TECH.md`](TECH.md) (written by `hs-plan`).
> Keep this at the right altitude: solved and bounded, but not over-specified — leave design
> freedom for the plan. Edit requirements here; do **not** silently drift them during build.

- **slug:** relay-advertise
- **kind:** feature
- **appetite:** small  ·  *One launch site (`relay/site.py::zone_launch`, which every zone's fleet
  uses, the first included), one option and one key on `hs relay`, one entry in the key table, and
  their docs. It mirrors a design #107 already built and reviewed for `hs cluster`, so the plan has
  no open design question to research.*
- **target:** 2.9.0  ·  *`hs relay` (#82) and `hs cluster --advertise` (#107) are both new in 2.9, so
  this completes the 2.9 relay interface before release.*

## Problem

A relay tells its site clients which address to dial, and the user cannot choose it. On `develop`
(`08d867df`) every site client of every zone is launched by `zone_launch`
(`src/hypershell/relay/site.py:175`): the first zone's fleet is built as an `AutoScalingCluster` and
then given `zone_launch`'s invocation (`:145`), and each further zone's fleet is built from it too
(`src/hypershell/relay/__init__.py:544`). `zone_launch` passes `advertised_host(queue.host)`, the
rule #107 gave every launch (`src/hypershell/cluster/launch.py:89-107`), with no override: the
relay's bind address when it binds one address, and this host's own name when it binds every
interface. `relay.bind` defaults to `0.0.0.0` (`src/hypershell/core/config.py:210`), so by default a
relay tells its site clients this host's own name.

`hs cluster` has an override, `--advertise` or `server.advertise` (`src/hypershell/cluster/__init__.py:215`,
`:343-347`; `src/hypershell/core/config.py:144`). `hs relay` has none, and `server.advertise` is
documented not to reach it (`docs/_include/config_param_ref.rst`, `[server] .advertise`). So a relay
whose site clients must dial a name that is neither its bind address nor this host's own name cannot
say so. Two cases:

- **Routing.** The site's compute nodes reach the relay host by a name or address that this host's
  own name does not resolve to (an internal-network name, an alias), while the relay must keep
  listening on every interface. Binding that one address with `--bind` is the only workaround, and
  it fails when the relay must also be reachable on another interface or when the name is not an
  address the host can bind.
- **TLS.** When the site clients verify the site server's certificate against the system CA bundle
  (`[server.tls] cafile` empty) and no `servername` is set, they check it against the host they
  dialed (`src/hypershell/core/tls.py:229-232`, `src/hypershell/core/queue.py:351-354`), so the
  dialed name must be one the certificate carries. With HyperShell's default `cafile = "<auto>"`, or
  any `cafile`, the host name is checked only against `servername` (`core/tls.py:225-228`), and
  fingerprint pinning checks no name; there the advertised name matters for routing only.

Reproduced on `develop` for this shaping in a throwaway site (upstream `hs server --forever --no-db
--no-tls -H 127.0.0.1`, keys by `HYPERSHELL_SERVER_AUTH` and `HYPERSHELL_RELAY_UPSTREAM_AUTH`, a
relay with `--bind 127.0.0.1` and a launcher that records its argv):

- The site client is launched with `-H 127.0.0.1`: the default rule works.
- `hs relay --advertise localhost` exits 2: `unrecognized arguments: --advertise localhost`.
- `HYPERSHELL_RELAY_ADVERTISE=localhost` is accepted without a word: `hs config get relay.advertise`
  prints `localhost`, because the environment layer holds an undeclared name under the closed
  `[relay]` table as an untyped key (`src/hypershell/core/config_schema.py`, `match_name`). The
  relay ignores it, and its site client is still launched with `-H 127.0.0.1`.

The gap is **pre-existing** in the sense that no relay release has had an override, but both
`hs relay` and `--advertise` are unreleased 2.9 features: #107 deferred the relay's override only
because `relay/__init__.py` was then being changed by relay-hardening (#103), which has since landed.

## Outcome / vision

`hs relay --advertise ADDR`, or `advertise` in `[relay]` (`HYPERSHELL_RELAY_ADVERTISE`), names the
address every site client of every zone is told to dial, exactly as `hs cluster --advertise` and
`server.advertise` do for a cluster's clients. The option wins over the key. With neither, a relay
tells its site clients what it tells them today. The relay's help, the configuration reference and
the security page say so, beside `--bind`.

## Acceptance criteria (the contract)

- **R1** — WHERE `hs relay` is given `--advertise ADDR`, every site client the relay launches, for
  every zone it serves and in both fleet modes (fixed, and following an upstream topology), SHALL be
  launched with `-H ADDR`, whatever `--bind` or `relay.bind` is.
- **R2** — WHERE `--advertise` is not given and the configuration key `relay.advertise` is set (in a
  configuration file's `[relay]` table, or by the environment variable `HYPERSHELL_RELAY_ADVERTISE`),
  every site client SHALL be launched with `-H` set to that value, as in R1. WHERE both are given,
  the option SHALL take precedence.
- **R3** — WHERE neither is given, or `relay.advertise` is empty, `hs relay` SHALL tell its site
  clients exactly what it tells them on `develop`: its bind address when it binds one address, and
  this host's own name when it binds every interface (the default `0.0.0.0`). `server.advertise`
  (and `HYPERSHELL_SERVER_ADVERTISE`) SHALL NOT change the address a relay's site clients are told.
- **R4** — IF `hs relay` is given `--advertise` with an empty or blank value, THEN it SHALL exit with
  a usage error (status 2) naming `--advertise`, before it opens its database or connects upstream.
- **R5** — `relay.advertise` SHALL be a declared text key in the configuration key table
  (`src/hypershell/core/config_schema.py`: `KEYS`, and `EMPTY_UNSET` beside `relay.bind`), so
  `HYPERSHELL_RELAY_ADVERTISE` maps to it exactly and is read as text, and an empty variable sets
  nothing. Like every relay setting, it SHALL NOT reach the site clients: it is removed from the
  environment they inherit and from the configuration copy they are given, as on `develop`.
- **R6** — `--advertise` SHALL be documented after `--bind` in `hs relay --help` (usage line and
  options) and in `docs/_include/relay_usage.rst` and `relay_help.rst`, and SHALL be completed by
  the bash and zsh completions. `relay.advertise` SHALL be documented after `.bind` in the `[relay]`
  section of `docs/_include/config_param_ref.rst`, naming its environment form and that the option
  takes precedence; `[server] .advertise` SHALL point a relay user to it in place of saying only that
  a relay ignores it. The help text and `docs/security.rst` (the passage on the advertised address)
  SHALL say that, under TLS with the system CA bundle and no `servername`, the advertised name must
  be one the site server's certificate carries. Every passage that states the site clients' address
  rule (the `--launcher` entry in `relay_help.rst`, `AGENTS.md`, `.agents/factory/invariants.md`
  "the relay has no override") SHALL state the new one. Docs name top-level commands only.

## Non-goals (no-gos)

- **A per-zone address.** Every zone's clients dial the one site server, so there is one address per
  relay; `[relay.zone.NAME]` gains no `advertise` key.
- **`hs cluster`, `hs server` and the upstream link.** `--advertise` and `server.advertise` keep
  #107's behaviour; `hs server --autoscaling` launches nothing and is unchanged; the relay's
  upstream address (`-H`/`--upstream-host`, `relay.upstream.host`) is unchanged.
- **Checking the address.** As in #107, the value is passed on as given: not resolved, not checked
  for reachability or against the bind or the certificate. A wrong address fails as a wrong
  `--bind` would, at the site clients.
- **TLS behaviour.** No `servername` forwarding to site clients, no certificate generation for the
  advertised name; `[server.tls] servername` stays the way to name the certificate apart from the
  dialed address.
- **A relay whose site clients all fail.** #107 R5 stands: the relay keeps relaunching and warns; no
  stop or limit here.
- **IPv6.** A wildcard `::` bind is [`issues/ipv6-server-listener.md`](../../issues/ipv6-server-listener.md).
- **The word "advertise" elsewhere in the relay.** Each zone already "advertises its client bounds
  upstream" (`docs/cli/relay.rst`, *Topology*); that wording and the code's `RelayThread.advertised`
  bounds are not renamed. The new docs make clear the option is about the address site clients dial.
- **Undeclared keys in general.** The silent acceptance of an undeclared `[relay]` key by the
  environment layer is not changed here; this cycle only declares `relay.advertise`.
- **No 2.8-line backport** (`hs relay` is not on the 2.8 line).

## Clarifications

Proposed in shaping on 2026-11-21 by the shaping agent; each is **pending the maintainer's sign-off
via the foreman** before `/hs-plan`.

- **Q:** Option name and key (the seed's open question)? — **A:** `--advertise ADDR` and
  `relay.advertise` (`HYPERSHELL_RELAY_ADVERTISE`), unset by default, to match `hs cluster
  --advertise` and keep the relay's settings in `[relay]` beside `relay.bind`. `server.advertise`
  does not reach the relay (R3), as the configuration reference already says; a relay's `[server]`
  settings describe its site server's TLS and eviction, not where its clients dial. The name shares
  a word with the zone bounds a relay "advertises" upstream; the docs disambiguate rather than pick a
  second name for the same concept #107 named.
- **Q:** Is `--advertise` refused in some relay mode, as #107 refuses it for a local cluster? —
  **A:** No. A relay always launches its site clients as separate processes that dial the site server
  over the network, even with no `--launcher`, so the option applies to every relay. Only an empty
  or blank value is refused (R4), as `hs cluster --advertise ""` is; an empty key or variable reads as
  unset (R3, R5), as `server.advertise` does.
- **Q:** Version markers? — **A:** None new. The seed asked for `.. versionadded::` on the option and
  key, but the `hs relay` page (`docs/cli/relay.rst`) and the `[relay]` section of the configuration
  reference already carry `versionadded:: 2.9.0`, and `AGENTS.md` places a marker at the narrowest
  accurate scope, which for a new option of a wholly new command is the page. The security-page
  passage already carries #107's marker; the plan may widen its text to name `relay.advertise`.
- **Q:** The TLS case in the seed? — **A:** Narrowed in **Problem**: the dialed name is checked only
  with the system CA bundle and no `servername`. HyperShell's default `cafile = "<auto>"` checks no
  host name unless `servername` is set. R6's TLS sentence states that precisely, matching
  `docs/security.rst`.
- **Q:** Appetite? — **A:** `small`, as the seed says: one launch site serves every zone, and #107's
  `ClusterApp.advertise_host` (option > key > none) is the pattern to mirror. No research fan-out.
- **Left for `/hs-plan`** (not clarification markers): how the value reaches `zone_launch` (a
  parameter threaded from `RelayApp` through `RelayThread` and `site_cluster`, constructors never
  reading configuration, as #107 did); whether the relay logs the address it tells its site clients;
  the exact help and error wording; the man page, which is regenerated at release (#107's review did
  not grade it).

## Evidence plan

What `hs-review` should be able to run against the branch:

- **Unit:** `zone_launch` with and without an advertised address (a specific bind, a wildcard bind,
  an override) gives `-H` as R1/R3 say; `RelayApp`'s resolution: option > `relay.advertise` > none,
  an empty key unset, `server.advertise` ignored; `--advertise ""` and `--advertise " "` refused with
  status 2; `match_name('HYPERSHELL_RELAY_ADVERTISE')` gives `('relay', 'advertise')` as text, and an
  empty variable sets nothing; `tests/test_config_schema.py`'s reference check passes with the new
  entry; the credential fence removes `HYPERSHELL_RELAY_ADVERTISE` from the site environment.
- **`.agents/factory/bin/temp_site.sh` drive** (no `-k`; keys by `HYPERSHELL_SERVER_AUTH` and
  `HYPERSHELL_RELAY_UPSTREAM_AUTH`; binds `127.0.0.1`): an upstream `hs server --forever` and a
  relay `--bind 127.0.0.1` with a recording launcher, as in **Problem**:
  - no override → `-H 127.0.0.1` (R3);
  - `--advertise localhost` → `-H localhost` (R1); with `--zone a --zone b`, both zones' launches;
  - `HYPERSHELL_RELAY_ADVERTISE=localhost` → `-H localhost`; with `--advertise 127.0.0.1` too →
    `-H 127.0.0.1` (R2);
  - `HYPERSHELL_SERVER_ADVERTISE=localhost` alone → `-H 127.0.0.1` (R3);
  - `--advertise ""` → exit 2 naming the option, no database file created (R4);
  - a healthy run with `--advertise localhost` and a launcher that runs the client completes the
    upstream tasks (`hs wait`, `hs list` show them complete).
- The test suite under Python 3.11 and 3.14; the Sphinx `-W` build; `hs relay --help` lists
  `--advertise` after `--bind`; the completion tests.

## Related materials

- Issue: none yet (seeded out of cycle).
- Seed: [`issues/advertised-host-ignores-bind.md`](../../issues/advertised-host-ignores-bind.md).
- Prior cycles: [`spec/cluster-launch-failures/`](../cluster-launch-failures/GOAL.md) (#107:
  `advertised_host`, `hs cluster --advertise`, `server.advertise`; its Non-goal "The relay beyond R5
  and R6"), [`spec/relay/`](../relay/GOAL.md) (#82: `--advertise` left for later),
  [`spec/relay-hardening/`](../relay-hardening/GOAL.md) (#103: `relay/__init__.py`, landed),
  [`spec/env-config-keys/`](../env-config-keys/GOAL.md) (#113: the key table and `EMPTY_UNSET`).
- Source: `src/hypershell/relay/site.py` (`site_cluster`, `zone_launch`),
  `src/hypershell/relay/__init__.py` (`RelayApp`, `RelayThread`, `APP_USAGE`, `APP_HELP`),
  `src/hypershell/cluster/launch.py` (`advertised_host`), `src/hypershell/cluster/__init__.py`
  (`ClusterApp.advertise_host`, the pattern), `src/hypershell/core/config.py` (`default['relay']`,
  `is_relay_variable`), `src/hypershell/core/config_schema.py` (`KEYS`, `EMPTY_UNSET`).
- Docs: `docs/_include/relay_usage.rst`, `relay_help.rst`, `config_param_ref.rst`,
  `docs/security.rst`, `share/bash_completion.d/hs`, `share/zsh/site-functions/_hs`.
- Invariants: `.agents/factory/invariants.md` §11 (one rule for the advertised address).
