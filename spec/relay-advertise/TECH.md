---
slug: relay-advertise
title: hs relay names the address its site clients dial
kind: feature
appetite: small
status: in_progress
branch: feature/relay-advertise
base: develop
current_phase: P3
last_updated: '2026-11-21'
phases:
- id: P1
  name: 'Tests first: the red list on 08d867df (tests only)'
  status: done
  satisfies:
  - R1
  - R2
  - R3
  - R4
  - R5
  - R6
  depends_on: []
  parallel: false
  hammerable: false
  hill: uphill
  verify: git diff --quiet 08d867df -- src docs share AGENTS.md .agents && test -z
    "$(git diff --name-only 08d867df -- . ':!spec' ':!tests' ':!issues' ':!ROADMAP.md')"
    && test "$(grep -cvE '^#|^$' spec/relay-advertise/red.txt)" -eq 29 && RED_BASE_REV=08d867df
    RED_LIST="$PWD/spec/relay-advertise/red.txt" sh spec/relay-advertise/drive/bound.sh
    900 sh spec/relay-hardening/drive/red_check.sh && RED_LIST="$PWD/spec/relay-advertise/red.txt"
    sh spec/relay-advertise/drive/bound.sh 900 sh spec/contended-writes/drive/rest_green.sh
    tests/test_relay_advertise.py tests/test_config_schema.py tests/test_env_config.py
    tests/test_cluster_advertise.py tests/test_relay_launch.py tests/test_key_option.py
- id: P2
  name: 'The option, the key and the launch: --advertise and relay.advertise reach
    every site client'
  status: done
  satisfies:
  - R1
  - R2
  - R3
  - R4
  - R5
  depends_on:
  - P1
  parallel: false
  hammerable: false
  hill: uphill
  verify: sh spec/relay-advertise/drive/bound.sh 1200 uv run pytest -q -p no:cacheprovider
    -k 'not documented_in' tests/test_relay_advertise.py tests/test_config_schema.py
    tests/test_env_config.py tests/test_cluster_advertise.py tests/test_relay_launch.py
    tests/test_relay_topology.py tests/test_key_option.py && sh spec/relay-advertise/drive/bound.sh
    120 .agents/factory/bin/temp_site.sh sh -c 'uv run hs relay --help' | awk '/^
    +--bind /{b=NR} /^ +--advertise /{a=NR} END{exit !(b && a && b < a)}' && for m
    in told empty healthy; do sh spec/relay-advertise/drive/bound.sh 400 .agents/factory/bin/temp_site.sh
    sh "$PWD/spec/relay-advertise/drive/advertise_drive.sh" $m || exit 1; done
- id: P3
  name: Docs, completions and the agent docs
  status: pending
  satisfies:
  - R6
  depends_on:
  - P2
  parallel: false
  hammerable: false
  hill: uphill
  verify: sh spec/relay-advertise/drive/bound.sh 900 uv run pytest -q -p no:cacheprovider
    tests/test_relay_advertise.py tests/test_completions.py tests/test_config_schema.py
    && bash -n share/bash_completion.d/hs && zsh -n share/zsh/site-functions/_hs &&
    ! grep -n 'the relay has no override' AGENTS.md .agents/factory/invariants.md
    && grep -q 'relay\.advertise' AGENTS.md && grep -q 'relay\.advertise' .agents/factory/invariants.md
    && UV_PROJECT_ENVIRONMENT=/tmp/claude-501/docs-venv-rad sh spec/relay-advertise/drive/bound.sh
    900 uv run --python 3.12 --group docs sphinx-build -W --keep-going -q -E -b html
    docs /tmp/claude-501/docs-html-rad
- id: P4
  name: 'Gate: every drive mode, the whole suite serially under 3.14 and 3.11, the
    docs'
  status: pending
  satisfies:
  - R1
  - R2
  - R3
  - R4
  - R5
  - R6
  depends_on:
  - P3
  parallel: false
  hammerable: false
  hill: uphill
  verify: for m in told empty healthy; do sh spec/relay-advertise/drive/bound.sh 400
    .agents/factory/bin/temp_site.sh sh "$PWD/spec/relay-advertise/drive/advertise_drive.sh"
    $m || exit 1; done && PATH=/tmp/claude-501/venv314-rad/bin:$PATH UV_PROJECT_ENVIRONMENT=/tmp/claude-501/venv314-rad
    sh spec/relay-advertise/drive/bound.sh 5400 uv run --python 3.14 pytest -p no:cacheprovider
    -q && PATH=/tmp/claude-501/venv311-rad/bin:$PATH UV_PROJECT_ENVIRONMENT=/tmp/claude-501/venv311-rad
    sh spec/relay-advertise/drive/bound.sh 5400 uv run --python 3.11 pytest -p no:cacheprovider
    -q && UV_PROJECT_ENVIRONMENT=/tmp/claude-501/docs-venv-rad sh spec/relay-advertise/drive/bound.sh
    900 uv run --python 3.12 --group docs sphinx-build -W --keep-going -q -E -b html
    docs /tmp/claude-501/docs-html-rad
review:
  last_reviewed_commit: ''
  verdict: none
  blocked_reason: ''
  cycle: 0
---
# TECH.md — `hs relay` names the address its site clients dial

The **context engine and finite-state machine** for building this feature. The YAML
frontmatter above is the resume ground-truth (read it with
`uv run python .agents/factory/bin/next_phase.py spec/relay-advertise/TECH.md`); the per-phase
checklists below are the work. `hs-build` executes the next actionable phase, runs its
`verify:` command, updates state via `uv run python .agents/factory/bin/set_phase.py …`, and makes
one atomic code+state commit.

- **Vision / requirements (locked):** [`GOAL.md`](GOAL.md) — R-IDs are the contract.
- **Authoritative design:** [`PLAN.md`](PLAN.md). Lean plan: no research fan-out.
- **Test prototype:** [`research/prototypes/relay_advertise_proto.py`](research/prototypes/relay_advertise_proto.py),
  named so the suite never collects it. P1 copies it to `tests/test_relay_advertise.py` (with
  `REPO = Path(__file__).resolve().parent.parent` and the prototype sentence dropped from its
  docstring). Run the prototype itself with
  `PYTHONPATH=$PWD uv run pytest -p tests.conftest spec/relay-advertise/research/prototypes/relay_advertise_proto.py`.
- **Red list:** [`red.txt`](red.txt), 29 node ids, checked by the shared
  `spec/relay-hardening/drive/red_check.sh` (each must fail on `08d867df` by an assertion, or by the
  ` raises=` error its line names) and `spec/contended-writes/drive/rest_green.sh` (everything else in
  the named files passes).
- **Drive:** [`drive/advertise_drive.sh`](drive/advertise_drive.sh), modes `told`, `empty`,
  `healthy` (the GOAL's evidence plan, one to one). The builder may amend it; a changed check is
  noted in the commit. `tests/test_key_option.py` sweeps `spec/*/drive/*` for a key option, so the
  drive never writes the short key flag, even in a comment (the plan-time suite run caught one).
- **Bound:** [`drive/bound.sh`](drive/bound.sh) `SECONDS COMMAND...` bounds every verify step
  (macOS has no `timeout`): SIGINT to the step's own process group at the bound, so pytest tears its
  relays down, then SIGKILL 15 s later; status 124 at the bound.

## Running the verify commands here

- Run every `verify:` from the worktree root, with these exported (the drive's `uv run` and every
  subprocess test inherit them):
  `UV_PROJECT_ENVIRONMENT=/tmp/claude-501/venv314-rad`, `UV_CACHE_DIR=/tmp/claude-501/uv-cache-rad`,
  `TMPDIR=/tmp/claude-501`, and `PATH=/tmp/claude-501/venv314-rad/bin:$PATH`. The worktree's own
  `.venv` is not used. P4 sets its own environment per Python (3.11 in `/tmp/claude-501/venv311-rad`).
- The docs build runs with the sandbox off, in **`/tmp/claude-501/docs-venv-rad`**, a docs venv of
  this cycle's own. The shared `/tmp/claude-501/docs-venv` was being rebuilt by another session while
  this plan was dry-run (its `pyvenv.cfg` and `sphinx-build` were gone), and `uv run` re-installs the
  project from whichever worktree ran last, so autodoc could document another tree. Everything else ran
  in the sandbox at plan time.
- `pytest -n auto` is broken on this suite: everything runs serially.
- Every relay, server and client binds `127.0.0.1`; keys go only through `HYPERSHELL_SERVER_AUTH`
  and `HYPERSHELL_RELAY_UPSTREAM_AUTH`, never `-k`. The drive stops every process it starts by PID
  (its tree), and the tests' `relays` fixture kills each relay's process group.
- Another session plans and builds in the `relay-shutdown-tail` worktree: never stop its processes,
  never use its venvs.
- Expected durations (plan-time dry-run): P1 `red_check` 26 s (29 of 29 red), `rest_green` 60 s
  (333 passed, 29 deselected; `test_key_option.py` since added, 69 passed in 11 s); P2/P3 test sets ≈ 100 s (431 passed with the design emulated);
  drive `told` 22 s, `empty` 2 s, `healthy` 9 s; docs build ≈ 5–10 s; the whole suite ≈ 25 min per
  Python unloaded (bounded at 90 min each).

## Conventions (apply to every phase)

- Commit conventions, code style, and load-bearing invariants come from
  [`AGENTS.md`](../../AGENTS.md); the invariant checklist is
  [`.agents/factory/invariants.md`](../../.agents/factory/invariants.md) §10–§12.
- One phase per `hs-build` invocation; one atomic commit containing **both** the code and the
  `TECH.md` state change, subject `[feature] Build relay-advertise P<n>: …`, signed; **no
  `Co-Authored-By` trailer**. Squashed into the single PR-title commit at `hs-publish`.
- A CLI change updates `docs/_include/*.rst` and `share/` completions in the same commit (§12): here
  the option lands in P2 and its snippets and completions in P3, by the foreman's phase split
  (PLAN §3 deviation; squashed at publish). The key's reference entry lands with the key, in P2
  (§10, test-enforced).
- No new version marker: `docs/cli/relay.rst` and the `[relay]` section carry `versionadded:: 2.9.0`.
  Docs name top-level commands only.

---

## Phase P1 — Tests first: the red list on 08d867df (tests only)
**Satisfies:** R1–R6 · **Depends on:** —
**Goal:** every new test is in the tree and fails on `08d867df` for what it checks; everything else
in the touched test files passes.

- [x] `tests/test_relay_advertise.py` from the prototype (see above), unchanged otherwise.
- [x] `tests/test_config_schema.py` `MATCHES`: `'HYPERSHELL_RELAY_ADVERTISE': (('relay', 'advertise'), Kind.TEXT),`
      after `HYPERSHELL_RELAY_ZONE`.
- [x] `tests/test_env_config.py` `EMPTY_NOT_GIVEN`: `'relay.advertise': ('relay.advertise', 'HYPERSHELL_RELAY_ADVERTISE'),`
      after `relay.bind`.
- [x] Nothing outside `tests/` and `spec/` changes. A renamed test is renamed in `red.txt` too.
- **Verify:** see frontmatter (the fence; `red.txt` holds 29 ids; `red_check` 29 of 29 red;
  `rest_green` over the five touched or neighbouring files). Dry-run at plan time: as stated.
- **Touches:** `tests/test_relay_advertise.py`, `tests/test_config_schema.py`, `tests/test_env_config.py`.

## Phase P2 — The option, the key and the launch (code, key reference)
**Satisfies:** R1, R2, R3, R4, R5 · **Depends on:** P1
**Goal:** `hs relay --advertise ADDR` or `relay.advertise` names the address every site client of
every zone dials; neither leaves develop's address; a blank option exits 2 before the database.

- [x] `relay/site.py`: `site_cluster(..., advertise: Optional[str] = None)` → `AutoScalingCluster(...,
      advertise=advertise)`; `zone_launch` dials `cluster.advertised` (not `advertised_host(queue.host)`);
      drop the unused `advertised_host` import; module, `site_cluster` and `zone_launch` docstrings
      state the rule (PLAN §2.1).
- [x] `relay/__init__.py` `RelayThread.__init__(..., advertise: Optional[str] = None)` →
      `site_cluster(..., advertise=advertise)`; the further zones' `zone_launch` calls unchanged.
- [x] `RelayApp`: `--advertise` (default `None`) after `--bind`; `advertise_host` cached property
      (option, else `relay.advertise` unless `None`/`''`/`PARAM_UNSET`, else `None`; never
      `server.advertise`); `check_args` refuses an empty or blank option first, right after
      `--no-confirm`, `ArgumentError('Expected an address for --advertise (given an empty value)')`;
      `run()` passes `advertise=self.advertise_host`.
- [x] `APP_USAGE` (`[--bind ADDR] [--advertise ADDR]`, rewrapped) and `APP_HELP` (the option line
      after `--bind`; the description paragraph with the rule and the TLS case, PLAN §2.3).
- [x] `core/config.py`: `'advertise': PARAM_UNSET` in `default['relay']` after `bind`.
      `core/config_schema.py`: `'relay.advertise': T` after `'relay.bind'` in `KEYS`; `'relay.advertise'`
      after `'relay.bind'` in `EMPTY_UNSET`.
- [x] `docs/_include/config_param_ref.rst`: `[relay]` `.advertise` after `.bind` (PLAN §2.4), and the
      `[server]` `.advertise` pointer to it — in this commit, with the key.
- [x] `tests/test_cluster_advertise.py` module docstring: a relay's site clients have their own
      override (`hs relay --advertise`, `relay.advertise`).
- **Verify:** see frontmatter (every new test but the two `documented_in` ones; the schema, env,
  cluster-advertise, relay-launch and relay-topology suites; `hs relay --help` lists `--advertise`
  after `--bind`; the drive's three modes). Dry-run at plan time with the design emulated in a
  scratch copy: 431 passed; `told`, `empty`, `healthy` ok. On `08d867df` the drive fails at `told`
  (`option: relay exited`, argparse), `empty` (no refusal naming the option) and `healthy`.
- **Touches:** `src/hypershell/relay/site.py`, `src/hypershell/relay/__init__.py`,
  `src/hypershell/core/config.py`, `src/hypershell/core/config_schema.py`,
  `docs/_include/config_param_ref.rst`, `tests/test_cluster_advertise.py`.

## Phase P3 — Docs, completions and the agent docs
**Satisfies:** R6 · **Depends on:** P2
**Goal:** the relay's help pages, the security page and both completions offer and explain
`--advertise`; every passage stating the site clients' address rule states the new one.

- [ ] `docs/_include/relay_usage.rst`: ``[--advertise ADDR]`` after ``[--bind ADDR]``.
- [ ] `docs/_include/relay_help.rst`: the `--launcher` entry's address sentence; ``--bind`` gains
      "unless ``--advertise`` names another"; a new ``--advertise`` *ADDR* entry between ``--bind`` and
      ``--port`` (PLAN §2.5: precedence over ``relay.advertise``, ``server.advertise`` does not reach a
      relay, passed on as given, the TLS sentence, not the bounds a zone advertises, "See also
      ``relay.advertise``").
- [ ] `docs/security.rst` (the advertised-address paragraph and its `versionadded:: 2.9.0` body):
      a relay's site clients and ``relay.advertise``. `docs/_include/config_intro.rst`: ``relay.advertise``
      beside ``server.advertise`` among the optional text keys.
- [ ] `share/bash_completion.d/hs` and `share/zsh/site-functions/_hs`, `_hs_relay` (PLAN §2.5).
- [ ] `AGENTS.md` (the `client_launch()` bullet's "the relay has no override", and the `relay/`
      bullet if it states the address) and `.agents/factory/invariants.md` §11 (the one-rule bullet),
      in lockstep.
- **Verify:** see frontmatter (the whole new test file, completions and schema suites; `bash -n` and
  `zsh -n` on the completions; the old wording gone and the key named in both agent docs; the Sphinx `-W`
  build). Dry-run at plan time: the docs build passes on
  `08d867df` and with the emulated docs.
- **Touches:** `docs/_include/relay_usage.rst`, `docs/_include/relay_help.rst`, `docs/security.rst`,
  `docs/_include/config_intro.rst`, `share/bash_completion.d/hs`, `share/zsh/site-functions/_hs`,
  `AGENTS.md`, `.agents/factory/invariants.md`.

## Phase P4 — Gate: every drive mode, the whole suite serially under 3.14 and 3.11, the docs
**Satisfies:** R1–R6 · **Depends on:** P3
**Goal:** the change holds together: the drive, the whole suite (the red list now green within it),
both Pythons, the docs build.

- [ ] Create `/tmp/claude-501/venv311-rad` on first use (the verify's `uv run --python 3.11` does).
- [ ] Commit body: each drive mode's result and the suite totals per Python. A flaky failure is
      re-run once and recorded with its node id and both outcomes.
- **Verify:** see frontmatter. Dry-run at plan time: the whole suite on `08d867df` under both
  Pythons, serially, bounded (totals in the plan commit body); the drive and docs as in P2/P3.
- **Touches:** `spec/relay-advertise/TECH.md` only.

---

## How `hs-build` drives this

1. `next_phase.py` prints the next actionable phase.
2. Pre-flight: clean tree, on `feature/relay-advertise`, `develop` reachable.
3. Execute every `[ ]` in the phase (PLAN.md has the detail).
4. Run the phase's `verify:` — never advance on a checkbox alone.
5. Amend this file if reality diverges (state through `set_phase.py`; note it in the commit body).
   STOP and escalate only on a `GOAL.md` contradiction.
6. Mark the phase `done`, advance `current_phase`; one signed `[feature]` commit; stop and report.
