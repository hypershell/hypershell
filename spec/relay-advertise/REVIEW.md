# REVIEW — `hs relay` names the address its site clients dial

> Adversarial QA by `hs-review`, run in an isolated/clean context. The correctness pass grades the
> branch diff against [`GOAL.md`](GOAL.md) + the AGENTS.md invariants **only** — it does not see
> `PLAN.md`/`TECH.md` (avoids grading-its-own-homework / plan-sycophancy). Every finding cites an
> **executed** command, not an assertion.

- **Reviewed commit:** 33b25dacf52c2c6bfad4e796d5ae36d5ea65049e  ·  **Base:** `origin/develop` (d9901cf0; merge-base 08d867df)  ·  **Date:** 2026-11-21
- **Verdict:** approved
- **Cycle:** 1 of ≤3 — mirrors `review.cycle` in `TECH.md`

The diff edits `invariants.md` §11 and `AGENTS.md` together, so the reviewer graded against the
**base** copies (`git show origin/develop:…`), not the branch's. Contract readings passed to it as
binding: the GOAL-gate block in `GOAL.md` and the plan-gate rulings (names `--advertise` and
`relay.advertise`, unset by default; `server.advertise` never reaches the relay; only an empty or
blank `--advertise` refused, exit 2, before the database; no new version markers; TLS motivation
narrowed to the system CA bundle; code and docs split across two build commits, restored at the
squash; `hs config get relay.advertise` prints `<none>` when unset; no per-zone address). Contract
drift: `GOAL.md` changed on the branch only in its shaping and GOAL-gate commits.

## Verification run

Python 3.14 (and 3.11 where noted), serial pytest; CLI drives in `.agents/factory/bin/temp_site.sh`
sites bound to 127.0.0.1, keys only by `HYPERSHELL_SERVER_AUTH` / `HYPERSHELL_RELAY_UPSTREAM_AUTH`,
a recording launcher capturing each site client's argv.

- `pytest tests/test_relay_advertise.py tests/test_cluster_advertise.py tests/test_config_schema.py tests/test_env_config.py tests/test_completions.py` → 296 passed (3.14); 296 passed (3.11).
- `pytest tests/test_relay_launch.py tests/test_relay_topology.py tests/test_relay_db.py tests/test_cluster_launch.py tests/test_env_blame.py tests/test_main.py` → 379 passed, 1 skipped (3.14).
- Orchestrator re-run: `pytest tests/test_relay_advertise.py tests/test_config_schema.py` → 157 passed (3.14).
- Mutation checks (each reverted): `zone_launch` back to the develop rule → 3 fail; only the first zone advertised → 3 fail; `RelayThread` drops `advertise=` → 1 fails; `RelayApp.run` drops it → 3 integration tests fail; falls back to `server.advertise` → 1 fails; refuse empty only → `[blank]` fails; refusal moved after the database → 4 fail.
- Byte-for-byte default path: every fleet's argv for a `RelayThread` with zones `cpu`, `gpu` and the default, a launcher with arguments, at binds 127.0.0.1, 0.0.0.0 and localhost — develop's `src` (`git archive origin/develop src`) and the branch identical; 0.0.0.0 gives this host's name. `QueueConfig(host, port)` is never mutated after construction, so `cluster.advertised` equals the old `(advertised_host(queue.host), queue.port)`.
- Drives: no override → `-H 127.0.0.1` (both zones with `--zone a --zone b`); `--advertise localhost` → `-H localhost`, both zones; `HYPERSHELL_RELAY_ADVERTISE=localhost` → `-H localhost`; with `--advertise 127.0.0.1` too → `-H 127.0.0.1`; `[relay] advertise = "localhost"` in a file → `-H localhost`; `HYPERSHELL_RELAY_ADVERTISE=` → `-H 127.0.0.1`; `HYPERSHELL_SERVER_ADVERTISE=localhost` alone → `-H 127.0.0.1`.
- Topology fleets: upstream `hs server --autoscaling=fixed -P 1`, relay `--zone a --zone b -N 2 --advertise localhost` → the topology launch for zone `a` got `-H localhost --zone a`; with the zones swapped (`a` built by `zone_fleet`), the same.
- Refusals: `--advertise ''`, `' '` and a tab → exit 2, `Expected an address for --advertise (given an empty value)`; a listener on the upstream port logged no connection and no `relay/*.db` or lock file was created. Positive control with `--advertise localhost`: connections logged, database and lock created.
- Key: `hs config get relay.advertise` → `<none>` unset, `localhost` from the variable, `<none>` for an empty variable; `hs config which relay.advertise` names `HYPERSHELL_RELAY_ADVERTISE`.
- Fence: no `HYPERSHELL_RELAY_*` name in any site client's environment; the site configuration copy held no `[relay]` table and no `advertise`.
- Healthy run: 6 tasks submitted upstream, relay `--advertise localhost` with a launcher that ran the client → `hs wait` exit 0, `hs list` all 6 exit 0.
- Docs: `sphinx-build -W --keep-going -q -E -b html docs …` → exit 0, no warnings.
- Merge: `git merge-tree --write-tree origin/develop HEAD` → clean. Against `origin/fix/relay-shutdown-tail` (#115): the only conflicts (`ROADMAP.md`, `src/hypershell/server.py`) are #115's own with develop, present in `merge-tree origin/develop origin/fix/relay-shutdown-tail` too; this branch's `ROADMAP.md` line merges clean, and the files both branches touch (`AGENTS.md`, `invariants.md`, `config_param_ref.rst`) auto-merge in separate passages. #115 changes no source file this branch changes.

## Requirement → evidence matrix

| R-ID | Implemented by (file/commit) | Verified how | Status |
|------|------------------------------|--------------|--------|
| R1 | `relay/__init__.py` (`RelayApp` → `RelayThread` → `site_cluster`), `relay/site.py` (`zone_launch` dials `cluster.advertised`); 1662afb7 | unit tests for site and further zones, specific and wildcard bind; drives for one and two zones; topology drive with both zone orders; mutations M1, M2, M5 | ✅ |
| R2 | `RelayApp.advertise_host` (option > `relay.advertise` > none); 1662afb7 | `test_relay_app_advertise_host`; drives by variable, by file, and option over variable | ✅ |
| R3 | default path unchanged; `server.advertise` not read | byte-for-byte argv check against develop at three binds; drives with no override, empty variable, `HYPERSHELL_SERVER_ADVERTISE`; mutation M4 | ✅ |
| R4 | `RelayApp.check_args`, before `bind_database` | refusal drives for empty, blank and tab with an upstream listener and database-file check; mutations M6, M7 | ✅ |
| R5 | `core/config.py` default, `config_schema.py` `KEYS` and `EMPTY_UNSET` | `hs config get`/`which`; schema and environment unit tests; site environment and configuration copy inspected | ✅ |
| R6 | `APP_USAGE`/`APP_HELP`, `relay_usage.rst`, `relay_help.rst`, `config_param_ref.rst`, `config_intro.rst`, `security.rst`, bash and zsh completions, `AGENTS.md`, `invariants.md`; 6c221428 | `hs relay --help` order; TLS sentence checked against `core/tls.py:229-232` and `core/queue.py:351-353`; completion tests; `-W` docs build | ✅ |

Unmapped changes (possible scope creep): none. The `ROADMAP.md` and
`issues/advertised-host-ignores-bind.md` status lines (`adopted:relay-advertise`) match reality.

## Findings

No CONFIRMED findings.

### [LOW/PLAUSIBLE] F1 — The `--bind` entry reads as if `--advertise` lifts the need for the relay to be reachable
- **Where:** `docs/_include/relay_help.rst:57-58`
- **Failure scenario:** "…so it must be reachable from the nodes they run on, unless `--advertise`
  names another address for them to dial." The advertised name must still reach an interface the
  relay listens on: `--bind 127.0.0.1 --advertise head.site` gives site clients that never connect.
  A reader of this sentence could take `--advertise` as a substitute for a reachable bind.
- **Evidence:** by reading; not reproduced (it needs a non-loopback address, outside the review's
  127.0.0.1 rule). The `--advertise` entry itself, `[relay] .advertise` and the help text are
  accurate. Wording only; for the human to triage, not blocking.
- **Touches invariant / requirement:** R6 (prose accuracy).

Checked and dropped: a blank key value (`relay.advertise = " "`) is passed on as given — the
contract refuses only a blank option and the non-goal says "passed on as given", matching
`server.advertise` on develop (executed); a non-string TOML value is stringified, the same code as
`ClusterApp.advertise_host`, no crash; `HYPERSHELL_SERVER_ADVERTISE` still reaches a site client's
environment, but it is not a relay variable and site clients do not read it.

## Human-gate triggers

- Not triggered. No CONFIRMED finding, and the diff touches no high-blast-radius file
  (`relay/__init__.py`, `relay/site.py`, `core/config.py`, `core/config_schema.py` are outside the
  list). The security-adjacent paths (§10 relay variable fence, §11 `fence_site` on every site
  launch) were exercised and hold. The diff does rewrite the §11 address rule in `invariants.md` and
  `AGENTS.md`; the new text matches the code as reviewed.

## Optional completeness sub-pass (separate reviewer; may see TECH.md)

- Not run (not requested).

## Review cycle 2 — approved (2026-11-21)

- **Reviewed commit:** 4a27eda33589dd8234d3321093a0cb63c030a6c5  ·  **Base:** `origin/develop` (d9901cf0; merge-base 08d867df)
- **Mode:** remediation-scoped, docs-only, per the binding "Review-cycle-1 ruling" in `PLAN.md`.
  Scope is `33b25dac..4a27eda3`; its one build commit (4a27eda3) changes
  `docs/_include/relay_help.rst` (one sentence, the `--bind` entry) and appends the ruling to
  `PLAN.md`. No `src/` or `tests/` change (`git diff --stat 33b25dac..4a27eda3 -- src tests` empty),
  so no test suite was run. `GOAL.md` unchanged since its gate commit.

### Verification run

- F1 remediation: the `--bind` entry now reads "…so it must be reachable from the nodes they run
  on. ``--advertise`` changes only the address they are told to dial." The "unless `--advertise`…"
  clause is gone; nothing in the entry suggests `--advertise` substitutes for a reachable bind.
- Accuracy against the code: `site_cluster` passes `address` to `AutoScalingCluster(bind=…)` and
  `advertise` only into `advertised_host` (`relay/site.py:125-146`, `cluster/remote.py:1015`);
  `zone_launch` dials `cluster.advertised` (`relay/site.py:181`). Executed in a `temp_site.sh` site
  (Python 3.12): `site_cluster(address=("127.0.0.1", 50402), size=1, advertise="head.site")` →
  server binds `('127.0.0.1', 50402)`, clients dial `('head.site', 50402)`. The bind and the port
  are untouched; only the dialed host changes, as the sentence says.
- Consistency: reads with the `--advertise` entry ("must be reachable from the nodes the *clients*
  run on"), the same file's `--launcher` passage and `APP_HELP` (both state which address clients
  are told, not reachability), `config_param_ref.rst` `[relay] .bind` ("The *clients* at the site
  connect here") and `.advertise`, and `cluster_help.rst`'s `-H`/`--advertise` passages, which the
  ruling keeps as is. No contradiction found.
- Docs: `sphinx-build -W --keep-going -q -E -b html docs /tmp/claude-501/docs-html-radrv2` → exit 0,
  no Sphinx warnings (only the docs environment's `pkg_resources` import warning and the
  matplotlib social-cards notice, neither a build warning). The sentence renders in `cli/relay.html`
  and `manual.html`.
- Merge: `git fetch origin`; `git merge-tree --write-tree origin/develop HEAD` → clean (tree
  c5b2daac). `origin/develop` still d9901cf0.

### Findings

No CONFIRMED findings. F1 (LOW/PLAUSIBLE, cycle 1): **resolved** at 4a27eda3.

### Human-gate triggers

- Not triggered. Docs-only delta; no high-blast-radius file, no security or DB-lifecycle invariant.
