# Research digest — relay-topology

Three read-only briefs, run in parallel at `d0c8fd7`:
[01 upstream surfaces](01-upstream-surfaces.md), [02 server entry and autoscaler](02-server-entry-autoscaler.md),
[03 relay reconciler and evidence](03-relay-reconciler-evidence.md). This digest takes one decision for
each question the briefs touch. Where a brief and the plan differ, the plan's reading wins, and the
reason is given here.

## Decisions

1. **Entry point: `hs server --autoscaling[=MODE]`** (02). On `hs cluster`, "no launcher" already means
   a local `hs client`. A cluster also mints a fresh key on every run, has no `-k`, re-picks its port and
   ignores `[server] evict`, so relays could not join it. `hs server` already uses the operator's key, a
   fixed port and `evict`, and it has no launcher, so `--autoscaling` there can only mean "with sites".
   `hs cluster` stays byte-identical.
2. **Config keys** (02). `[autoscale] distribution = spread | fill` (the default is `spread`) and
   `[autoscale] sites`, an optional list. Each key is one word, because environment names split on `_`.
   `sites` also takes a comma string, which is how the environment delivers it. On the relay side,
   `[relay] min`, `max` and `init` sit flat beside `size`, and so do the per-zone keys in
   `[relay.zone.<name>]`. A nested `size.*` would collide with the scalar `size`.
3. **Advertisement on every zone identity's heartbeat** (01, 03). The beat carries `zone`, `bounds`
   `{min, max, init}` and `clients`, the relay's *launched* count for that zone. The keys are packed only
   when set. An old server ignores them (unpacking reads named keys). Sending them on every beat covers
   connect and every reconnect, and it survives a server restart. A `zone` field on roster entries was
   rejected: the relay-local `client` table has no zone, and the server cannot invert `uuid5(instance, zone)`.
4. **Counts are launched, not connected** (02, 03). That mirrors Z3, which computes demand from
   `launched`. A count of registered clients would re-grow a target every check while scheduler steps
   sit pending.
5. **Topology fetch: a new dispatcher method `topology(text)`** (01). A work-request field cannot reach a
   relay with no clients, which never asks, and a probe request may take tasks. A heartbeat has no reply.
   On an old server, `client.dispatcher.topology` is missing from the proxy (`AttributeError` locally,
   with no round trip and the link still up), so the relay falls back (R6). A server that is not
   autoscaling with sites answers `version: null`.
6. **Relay reconciler: keep the per-zone fleets and swap in a `SiteScaler` machine** (03, amended).
   Brief 03 recommends a target hook on `AutoScaler`, with the relay launching *up to* the target at
   each check. The plan departs from that in two ways:
   - **Apply each version once, as a delta.** A relay that tops up to a target replaces every client
     that idle-exits. Z3's demand never falls below the launched count, so the server's next target
     includes the replacement, and a fleet with no work would churn forever. The relay therefore
     launches only what a *new* version asks beyond what the server counted, `target − max(seen,
     launched)`, once per version. Between versions it launches nothing. Z3 behaves the same way: its
     actuator never replaces an exit, and only the next demand can.
   - **No `cluster/remote.py` edit.** The `SiteScaler(AutoScaler)` subclass lives in `relay/site.py` and
     is assigned as the fleets' machine after construction. The precedent is `strict = False` at
     `site.py:112`. When the view has no topology, the machine runs the stock fixed-policy fleet (R6).
7. **Zone exposure on every relay launch** (03). `zone_launch` passes `expose_zone=True`, and the first
   zone's launch is rebuilt the same way, so a `{zone}` launcher works whether or not a topology arrives.
   This changes the environment of fallback launches (`HYPERSHELL_ZONE`), so it is a deviation row and
   question Q1 in PLAN §5.
8. **Fallback floor** (03). Without a topology a zone keeps at least one client, `max(min, 1)`, because
   the relay pulls only for a zone that has a site client (D5). `size < 1` stays refused.
9. **Lowered `max` evidence = a Q2 handoff** (03). Configuration is read once per process (§10), and a
   restarted relay ends its own site clients (relay-cycle behaviour). So the only live way for a
   site-zone's advertised `max` to fall below its running clients is a new relay registering the same
   site with a lower `max` while the old one runs. The drive exercises that case.
10. **Evidence harness** (03). The laptop drive is built on `zone_drive.sh` and `temp_site.sh`. The cluster
    script mirrors the site-neutral shape of `relay_drive.sh`, with records holding counts and booleans
    only and a stdlib judge.

## Contradictions resolved

- Brief 02 would inject a `PublishActuator` into `AutoScaler`. The plan instead uses a standalone
  publisher state machine in `cluster/topology.py`, which reuses `read_zone_loads` and `zone_demand`
  unchanged. That keeps `cluster/remote.py`, a high-blast-radius file, untouched by this cycle. The
  group-read rule (D2) is mirrored in one small helper, pinned against `AutoScaler.current_group` by
  an equivalence test.
- Brief 03's "a topology with no entry for this zone gives 0" is kept. It is how the older relay of
  a duplicated site (Q2) is told zero.
