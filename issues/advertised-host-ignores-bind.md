---
status: unshaped
kind: feature
appetite: small
target: 2.9.0
lane: public
---

# `hs relay` cannot name the address its site clients dial

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

> **Narrowed at retirement, 2026-11-16.** `cluster-launch-failures` (#107) shipped the rest of this
> seed: one rule for the address every `hs cluster` launch mode tells its clients, the same default
> rule for a relay's site clients, and `hs cluster --advertise` with `server.advertise`. It deferred
> the relay's override ([`spec/cluster-launch-failures/GOAL.md`](../spec/cluster-launch-failures/GOAL.md)
> *Non-goals*, "The relay beyond R5 and R6"). That remainder is all this seed now asks for; the
> original text is in git history.

## Problem

On `develop` (`91c5aa9a`), every launch tells its clients the address `advertised_host` gives
(`src/hypershell/cluster/launch.py:89-107`): an override when one is given, else the bind address
when the server binds one address, and this host's own name when it binds every interface.
`hs cluster` takes the override from `--advertise` or `server.advertise`
(`src/hypershell/cluster/__init__.py:215`, `src/hypershell/core/config.py:138`).

`hs relay` takes none. Its first zone's fleet is an `AutoScalingCluster` given only the relay's
`--bind` (`src/hypershell/relay/site.py:132-133`), and each further zone's clients dial
`advertised_host(queue.host)` (`:174`), so the default rule is all a relay has. `relay.bind`
defaults to `0.0.0.0` (`src/hypershell/core/config.py:204`), so by default a relay tells its site
clients this host's own name. A relay whose site clients must dial some other name has no way to
say so: an address on the site's internal network that the host name does not resolve to, or the
name on the site server's TLS certificate. That matters under TLS: with the default CA verification
and no `servername`, a client checks the certificate against the host it dialed
(`src/hypershell/core/tls.py:229-232`, `src/hypershell/core/queue.py:351-354`).

## Why it was deferred

Cut from `cluster-launch-failures` at shaping (its GOAL *Non-goals* and *Clarifications*,
2026-11-14). The relay's command line, `relay/__init__.py`, was then being changed by
`relay-hardening` (#103), so that cycle limited its relay change to the default rule in
`relay/site.py`. `relay-hardening` has since landed, so that reason no longer holds. The relay
cycle (#82) had already left an `--advertise` option for later.

## Outcome / vision

`hs relay` takes an advertised address for its site clients, as `hs cluster --advertise` does for
its own: an option and a relay configuration key beside `relay.bind`, honoured by every zone's
fleet, with the option taking precedence over the key and today's rule when neither is given.

## Sketch of the acceptance criteria

- **R1** — WHERE the user gives `hs relay` an advertised address, by option or configuration key,
  every site client of every zone SHALL be told exactly that address, whatever `--bind`; the
  option SHALL take precedence over the key.
- **R2** — WHERE none is given, site clients SHALL be told what they are told today (the
  `advertised_host` rule).
- **R3** — The option and key SHALL be documented beside `--bind` in `hs relay --help`, its
  `docs/_include/relay_*.rst` snippets, the man page, the shell completions and the configuration
  reference, with `.. versionadded::` and a note that under TLS the advertised name must be one the
  site clients' certificate check accepts.

## Open questions for shaping

- **Option name and key.** `--advertise` and `relay.advertise`, to match `hs cluster`? The relay's
  settings live under `[relay]` (`relay.bind`, `relay.port`), so `server.advertise`, which is
  `hs cluster`'s, should not reach it.

## Notes

- Related: `spec/cluster-launch-failures/` (#107: `advertised_host`, its R6–R8),
  `spec/relay/` (#82: `--advertise` deferred), `spec/relay-hardening/` (#103),
  [`ipv6-server-listener.md`](ipv6-server-listener.md) (an IPv6 wildcard bind would be advertised by
  the same rule).
- Found by: out-of-cycle review, 2026-11-05; narrowed to the relay when `cluster-launch-failures`
  was retired, 2026-11-16.
