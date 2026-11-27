# 00 — Digest

One brief ([01](01-start-up-seams.md)), written by the planner: `appetite: small`, `kind: fix`, both
root causes reproduced in shaping. The high-blast-radius exception fired because the design edits
`server.py` (its `-P` declaration and `check_args()` only), so the touched contracts were pinned by
probes rather than a fan-out.

Decisions:

1. **`--no-db`:** guard `ClusterApp.__enter__`'s whole database step with `if not self.in_memory`.
   Nothing else opens the database under `--no-db` (probe, local and launcher modes, fresh and
   unopenable paths).
2. **Where the floor lives:** one `parse_period` in `core/types.py` (string → `parse_time` then the
   floor; number → the floor only). It is `-P`'s `type=` on `hs cluster` and `hs server` (option
   text and key strings, named `-P/--period` with the value as written), runs again on the parsed
   value at the top of each app's argument check (key and environment numbers, named
   `autoscale.period`), and replaces `RelayApp.site_period`'s own branch (`hs relay`).
3. **Not in the autoscalers:** `cluster/remote.py`, `cluster/topology.py` and `relay/site.py` are
   left alone; every command route is refused before anything listens.
4. **Tests cannot hang on the base:** period tests run in-process or in a fresh interpreter with the
   serve/launch functions recorded, never a live server.
5. **xdist:** `pytest -n auto` fails collection on this suite (parametrized ids differ between
   workers); the whole-suite gate runs serially.
