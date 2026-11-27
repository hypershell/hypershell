# Research digest — zone-autoscale (Z3)

Three briefs, fanned out in parallel at `hs-plan` Step 3 against `8a85d46`:
[`01-autoscaler-signal.md`](01-autoscaler-signal.md) (FSM, data-model signal, founding-case tests),
[`02-relay-and-topology-seam.md`](02-relay-and-topology-seam.md) (relay consumers, relay-topology seam),
[`03-launcher-docs-verify.md`](03-launcher-docs-verify.md) (R3 exposure, docs surface, laptop drive).
Each decision below settles any disagreement between them with one recommendation.

## Decisions

1. **Per-zone signal: zone filter on the existing helpers, per-zone rate and duration.**
   `count_remaining`, `effective_rate_by_client`, `effective_rate`, `avg_duration`,
   `time_to_completion` and `task_pressure` take `zone: str = None` through `Task._filter_zone`
   (None is every zone, today). New `Task.count_remaining_by_zone(group)` (one GROUP BY, NULL mapped to
   `DEFAULT_ZONE_ID`) and `Task.count_retryable_by_zone(attempts, group, eager)` (under
   `count_retryable`'s rule). Per-zone rate and duration, because a global rate makes a slow zone read
   near-zero pressure (01 §2). In a zone-less database filtered equals unfiltered, so R4 holds. The
   default zone is always passed as `DEFAULT_ZONE_ID`, never None.
2. **Retry edge closed through discovery.** A named zone's retry rows are created only while one of
   its clients asks for work (01 §2; `core/dispatch.py` `refills`). A named zone whose only
   current-group work is retryable failures would read zero remaining, go undiscovered and hold the
   group. Discovery and the "work remains" test therefore count those failures for named zones. The
   default zone's retries are created without clients, so its count is today's (R4 identity).
3. **Demand is a pure function over injected readings; actuation is a separate object** (02 rec. 4).
   `cluster/demand.py` holds `ZoneRef`, `ScalingKnobs`, `ZoneLoad`, `read_zone_loads` (the only DB
   reads), `zone_demand` and `fleet_demand` (pure). `cluster/launch.py` gains `LaunchActuator`
   (per-zone cached `ClientLaunch`, flat `clients`). `AutoScaler` keeps its FSM, state names and
   zero-argument `check_fixed()`/`check_dynamic()`, and becomes the glue. The demand grows a zone by at
   most one client per check, so today's pacing is kept per zone.
4. **Relay seam: S4** (02 §2). `AutoScalingCluster(discover_zones=True)` by default, so every caller
   gets R2. `relay/site.py::site_cluster` passes `discover_zones=False`, a one-keyword edit with no
   behaviour change. `AutoScaler`/`AutoScalerThread` discover only when given a per-zone launch
   factory, so `zone_fleet` is untouched. S3 (default off, zero edits in `relay/`) was rejected because
   library callers would silently miss R2.
5. **R3 lives inside `client_launch`, opt in** (03 rec. 1). `client_launch(..., expose_zone=False)`.
   When it is set (launcher path only), `{zone}` is replaced in each launcher word and launcher argument
   after `shlex.split` (`str.replace`, never `str.format`), and `HYPERSHELL_ZONE=<name>` goes in `env`.
   It is not forwarded through Open MPI. The `-t` template word is never touched. `zone_launch` in the
   relay stays without the flag, so the relay's `env == {AUTH_ENV: …}` assertions hold.
6. **Contracts kept:** `AutoScaler.clients` stays a flat, assignable `List[Popen]`, with an untracked
   entry counting as the own zone. `AutoScalingCluster.launch` is the own zone's launch and
   `machine.launch is cluster.launch`. `AutoScalerThread`'s signature only grows keywords. The
   autoscaler still never ends by itself.
7. **Registered counts are for logging only.** They come from `server.queue.dispatcher.clients(zone)`,
   injected as a callable. Decisions use launched counts, as today.
8. **Names:** discovered zone ids map to names through `Zone.names_for_ids`, with `DEFAULT_ZONE_ID`
   mapped to `default`. An id with no `Zone` row is skipped with one warning.
9. **Founding-case argv:** the own zone's launch keeps today's argv exactly, with `--zone` only when
   the cluster was given `--zone`. Every other zone's launch appends `--zone <name>`. Whether this
   reading of R3 stands for a zone-less cluster is a gate question (PLAN §5, Q1).
10. **Evidence:** a shim plus a drive script in `spec/zone-autoscale/drive/` (03's rehearsed drafts),
    run in a `temp_site` in founding, fixed and dynamic modes. Laptop scale, no cluster.
