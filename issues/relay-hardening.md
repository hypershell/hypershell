---
status: adopted:relay-hardening
kind: fix
appetite: small
lane: public
---

# Relay hardening: a TLS mismatch retried forever, idle zones ended early, an unexplained end-of-run tail

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

`hs relay` shipped in 2.9.0a6 (#82) with a handful of rough edges its review recorded and left for
later. None loses or doubles a task; each costs an operator time or trust. On `origin/develop`
(`b4b37cd`, 2.9.0a6):

- **A TLS on/off mismatch with the upstream server is retried forever (C4-2).** The upstream link
  treats every `OSError`, `ssl.SSLError` included, as a transport error a new connection may cure
  (`TRANSPORT_ERRORS`, `src/hypershell/core/reconnect.py:92`; `FATAL_ERRORS` at `:89` holds only
  certificate verification and the version, auth and TLS-setup errors), and `attempt`
  (`:416-427`) retries those with backoff. Probed against a real upstream: a relay with TLS against
  a `--no-tls` upstream was still logging `SSLError: [SSL: WRONG_VERSION_NUMBER]` and "trying again"
  at 40 s, and a `--no-tls` relay against a TLS upstream spent the full 30 s deadline
  (`relay.upstream.deadline`) on each attempt and was still retrying at 50 s. A plain `hs client`
  fails at once in both cases, and a wrong key or `cafile` ends the relay in about 2 s. The relay
  never starts its site while it waits, and its log names the error, but it never exits.
- **An idle zone can end before taking rows staged for it (C5-1).** In a relay serving several
  zones, once one zone has had a shutdown reply, `finish_idle`
  (`src/hypershell/relay/upstream.py:383-394`, called at `:361`) ends every zone with no site
  client and nothing held, on the premise that "the upstream server finishes for every zone at once"
  (`upstream.py:238`, repeated in `tests/test_relay_connector.py:559`). That premise is too strong:
  `finish()` sets one flag for every pool, but a request is told to shut down only once its own
  zone's pool is empty (`_done`, `src/hypershell/core/dispatch.py:303-305`). If upstream finishes
  with rows still staged for a zone (a drain by SIGUSR1/2, a non-forever run stopped on a failed
  group, or a server without a database) and that zone has no site client at that gate pass (its
  fleet still launching, or its one client between exit and relaunch), the zone ends and never
  takes them. With a database the rows go back unscheduled when the upstream server exits
  (`ServerThread.revert_staged`, `src/hypershell/server.py:1158-1166`); without one, `take_staged()`
  returns nothing to put back and they never run in that server's run, with nothing logged.
  Reproduced at unit level against the real `Dispatcher`, in both forms. Next to it, documented but
  not fixed: a relay with no site client in **any** zone never asks, so never hears the shutdown and
  waits for a drain or a signal (`docs/_include/relay_desc.rst:49-52`); a non-forever upstream
  server waits for it too, since its identities still beat, and the docs do not say so.
- **An end-of-run throughput tail, cause unknown.** In the review's Linux throughput runs
  (`spec/relay/drive/relay_throughput.py`, 2000 × 0.2 s, 32 executors), the steady relay overhead
  was 1.2–1.4× a direct cluster, but four of nine runs carried an extra 6–9 s before the upstream
  server exited, and one about 60 s. Neither `bundlewait` nor the upstream scheduler's backoff
  explains it: both were tested and failed. The scratch directories were node-local and gone, so no
  log survives. A stall near the autoscale period (60 s) or the reconnect backoff cap (60 s)
  deserves a look.
- **Eviction ages a heartbeat by the sender's clock.** `HeartMonitor.check_clients` computes
  `age = self.last_check - hb.time` (`src/hypershell/server.py:866`), and `hb.time` is stamped by
  the relay. Cross-site clock skew beyond `server.evict` evicts a live relay at every check and puts
  back everything it holds. `docs/cli/relay.rst:34-36` lists this as a limitation and requires
  synchronised clocks; the plan named it a candidate seed. The fix changes eviction for every
  client, not only relays.
- **The man page's relay synopsis is malformed.** The two-line `hs relay` entry in
  `docs/manual.rst:22-24` indents its continuation line deeper than its description, so docutils
  warns "Block quote ends without a blank line" and `man hs` shows an extra blank line there.

## Why it was deferred

Recorded by `relay`'s review cycles 4 and 5 (2026-11-06; C4-2, C5-1 and the throughput note) and the
plan's risk list, ruled follow-ups by the maintainer for 2.9.0a6, and listed as known follow-ups on
#82. C4-2 is outside R2's fail-fast requirement, which covers only the protocol version. C5-1 is the
residue of the C4-1 fix (before it, each of those cases was a hang). The tail is not a GOAL
criterion: throughput is a stated non-goal, and the 1.5× limit was a rider. The man page warning
arrived with #82's docs. All introduced with relay (#82), except the sender-clock eviction, which
is **pre-existing** for every client and only made likelier by sites with their own clocks.

## Outcome / vision

A relay that cannot speak to its upstream server because one end has TLS off says so and exits, as
a client does. A zone stops only when upstream has nothing left for it. The end-of-run tail is
explained, and fixed or documented. Eviction tolerates a sender's clock skew, or the docs and a
runtime warning make the requirement unmissable. The man page renders cleanly.

## Sketch of the acceptance criteria

- **R1** — WHEN the relay's first connection to the upstream server fails because one end uses TLS
  and the other does not, the relay SHALL exit non-zero with an error naming the TLS setting on both
  ends, without launching site clients. A later outage SHALL still be retried.
- **R2** — The relay SHALL NOT end a served zone while the upstream server still holds rows staged
  for it; the code comment and the test docstring SHALL state the upstream guarantee exactly.
- **R3** — The docs SHALL say that a non-forever upstream server also waits for a relay with no site
  client in any zone.
- **R4** — A traced run SHALL name the cause of the end-of-run tail; the fix, or the documented
  bound, follows from it.
- **R5** — Eviction SHALL NOT evict a live relay whose clock differs from the upstream server's by
  less than the eviction period, or the relay SHALL warn at registration when the skew it can see
  exceeds a bound.
- **R6** — `docs/manual.rst` SHALL build without the block-quote warning and the `hs relay`
  synopsis SHALL render like its neighbours in `man hs`.

## Open questions for shaping

- **C4-2: fail fast, or hint?** The review offered two remedies: treat a `WRONG_VERSION_NUMBER`-class
  `ssl.SSLError` on the first connection as fatal, or add "check `--no-tls` / `relay.upstream.tls`
  on both ends" to the "Cannot reach" warning (`reconnect.py:494`) after N attempts. The `--no-tls`
  relay against a TLS upstream shows no SSL error at all, only a deadline, so fail-fast may not
  cover both directions.
- **C5-1: which rule?** The review's optional fix ends an idle zone only once it has had no site
  client for a whole site period. Alternatives: a shutdown reply that carries whether anything is
  staged anywhere (a wire change), or one more request per idle zone at the finish.
- **Clock skew: where to measure.** Ageing by the server's own receive time removes the dependency,
  but changes eviction for every client; a skew warning keeps today's semantics.

## Notes

- Tail investigation, as the review set it out: run `relay_throughput.py` with
  `HYPERSHELL_LOGGING_LEVEL=DEBUG` and the scratch kept until an outlier occurs, then read the relay
  log from "Upstream server is shutting down" to "Upstream heartbeat - final" and the upstream log
  around the relay's FINISHED beats.
- A confirmation replayed after an outage shorter than `relay.upstream.evict_hint` can, after an
  upstream restart the relay did not see, stamp the relay onto a reverted row; the worst case is one
  more at-least-once run. A confirmation matched to its dispatch stamp would close it; that is the
  token question in [`stale-confirm-hides-orphan.md`](stale-confirm-hides-orphan.md).
- Related: [`relay-abort-races.md`](relay-abort-races.md) (the relay's abort paths),
  [`relay-test-strength.md`](relay-test-strength.md) (its test gaps),
  [`docs-and-runtime-surfacing.md`](docs-and-runtime-surfacing.md) (L10: the clock assumption in
  timing aggregates), [`coordinator-resilience-retry-backoff.md`](coordinator-resilience-retry-backoff.md)
  (the shared transport/fatal split C4-2 would refine), `spec/relay/REVIEW.md` (cycle 4: C4-2 and
  the throughput note; cycle 5: C5-1 and the all-zones-idle note), `spec/relay/PLAN.md` (risks:
  sender-clock eviction, confirm replay).
- Found by: `relay` review cycles 4 and 5, and its plan, 2026-11-06.
