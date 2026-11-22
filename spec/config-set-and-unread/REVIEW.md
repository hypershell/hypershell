# REVIEW — `hs config set` writes each key by its type, and `hs server` and `hs cluster` read the keys they document

> Adversarial QA by `hs-review`, run in an isolated/clean context. The correctness pass grades the
> branch diff against [`GOAL.md`](GOAL.md) + the AGENTS.md invariants **only** — it does not see
> `PLAN.md`/`TECH.md` (avoids grading-its-own-homework / plan-sycophancy). Every finding cites an
> **executed** command, not an assertion.

- **Reviewed commit:** `f8293303` (a merge of `origin/develop` `6a0c28ea` onto the built branch at
  `00485180`)  ·  **Base:** `origin/develop` `6a0c28ea`  ·  **Date:** 2026-11-21
- **Verdict:** changes-requested
- **Cycle:** 1 of ≤3 — mirrors `review.cycle` in `TECH.md`

## Pre-flight

- Branch `fix/config-set-and-unread`, `kind: fix`, TECH `status: in_review`, every phase `done`.
- Contract drift: `GOAL.md` has two commits on the branch, the shaping commit `28c4df99` and the GOAL
  gate `81b762aa`, which only appends the dated "Ruled at the GOAL gate" block (14 added lines, no
  requirement edited). Both predate the first build commit; graded as the locked contract.
- Inputs given to the blind reviewer: `GOAL.md`, the spec-excluded diff
  (`git diff origin/develop...HEAD -- . ':(exclude)spec/'`), the **base** (`origin/develop`) copies of
  `invariants.md`, `AGENTS.md` and `review-rubric.md` (the diff edits §10 and §11 of the first two),
  and the plan-gate rulings S1–S5 restated as binding contract readings (the brief's direction; see
  `META.md`). It did not read `PLAN.md`, `TECH.md`, `research/` or `META.md`.

## Verification run

- Full suite, serial, Python 3.14: `uv run --frozen pytest -q` → 3776 passed, 138 skipped, 0 failed (25 min 32 s).
- Targeted (reviewer): `tests/test_config_set_types.py tests/test_config_unread.py
  tests/test_cluster_evict.py tests/test_config_schema.py` → 322 passed; the merged-in sides
  (`test_client_return.py test_finish_wake.py test_relay_advertise.py test_cluster_advertise.py
  test_cluster_launch.py test_env_config.py test_client_request.py`) → 339 passed, 1 skipped.
- Red-to-green (reviewer): the three new test files against `origin/develop`'s `src` → 99 failed,
  63 passed; the 63 cover behaviour the branch keeps (defaults, `-R` refusing bad words, durations
  unchanged).
- Docs: `sphinx-build -W --keep-going -q -E -b html docs …` (Python 3.12, docs group) → exit 0, no
  warnings.
- CLI drives in throwaway sites (`HYPERSHELL_SITE`, `HOME` redirected; keys by
  `HYPERSHELL_SERVER_AUTH`; binds on 127.0.0.1): per-value `hs config set --local` writes compared
  with `read_environment` for the same text (R1–R4); refusals for `--local` and `--user` with file
  and backup hashes compared before and after, and `--system` in process with the system path
  redirected (R2); `hs server`/`hs cluster` with `server.eager`, `server.attempts`, `server.evict`
  and `client.ratelimit` from a file and from the environment, a recording `--launcher` for the
  launched argv, and a timed local run for a fractional rate (R5–R8). Each finding below was
  reproduced a second time by the orchestrator.
- Merge: `git merge-tree --write-tree 00485180 origin/develop` gives tree `6181a0da`, which is
  `HEAD^{tree}`: the merge is the automatic one, no conflict resolution and no hand edits
  (`git show --remerge-diff HEAD` is empty). Develop's #115 and #116 changes and the branch's own
  hunks are byte-identical across the merge; every `ratelimit` path in the merged `client.py` is a
  float; no duplicate option; the suites of both sides pass.

## Requirement → evidence matrix

| R-ID | Implemented by | Verified how | Status |
|------|----------------|--------------|--------|
| R1 | `config.py` `ConfigSetApp` (`read_value(match_path(…))`), `core/config_schema.py` `match_path` | `hs config set` per value vs the environment layer: `server.auth 0012345678901234`, `autoscale.launcher false`, `export.RUN_ID 007`, `logging.datefmt null/None/''/TRUE/1e3`, `server.auth_env 007`, `ssh.nodelist.grp 007`, `relay.zone.gpu.launcher false`, all equal; `printenv RUN_ID` under `hs cluster --no-db` printed `007`; `hs server` accepted the all-digit key from the file | ✅ |
| R2 | as R1; `read_value` `LOGGING_FILE` refuses `''` | typed values equal the environment layer (`007`→7, `1_000`, `+5`, booleans in any case, `<none>`, zone `size`, `logging.file`); `abc`, `1.5`, `0x1F`, `''`, `maybe`, `inf`, `<NONE>`, `logging.file false` refused, exit 2, naming key, form and value; file and backups byte-identical for `--local`, `--user`, `--system` | ✅ |
| R3 | `read_value` for durations and sizes | `600`, `0.5`, `1e3`, `1_000` written as numbers; `10m`, `16G`, `inf`, `''` as text | ✅ |
| R4 | `read_untyped` via `match_path` → `UNTYPED` | `5`, `1_000`, `2.5`, `true`, `0x1F`→31, `0o17`→15, `0b11`→3 typed; `True`, `null`, `007`, `''`, `.5`, `5.`, `wal` text; equal to the environment layer | ❌ F1 |
| R5 / S1 | `server.py` `configured_eager`; `--eager` default from the key in both apps | `test_eager_*`; file and env true give eager, `--eager` still turns it on, env false over a file true; `"maybe"`, `1.0`, `2`, `[1]` exit 3 in both commands naming the key and file | ✅ |
| R6 | `ClusterApp.max_retries = config.server.attempts - 1` | `attempts = 3` gives `-r 2` in both commands; `-r 5` overrides | ✅ |
| R7 / S2 | `check_evict`; `evict_after` through `LocalCluster`, `RemoteCluster`, `SSHCluster`, `AutoScalingCluster` | `test_cluster_evict.py`: `1m30s` reaches the server in local, launcher, mpi, ssh and autoscaling from file and env; default 600; `inf`, `nan`, `-inf`, `"inf"`, `9`, `"9.5s"` refused with `hs server`'s message and status before initdb, checkdb or a port probe | ✅ (F3 is an edge beside it) |
| R8 / S3 | `parse_rate`, `plain_rate`, `ClusterApp.ratelimit = config.client.ratelimit or None` | launched argv: `0.5`→`-R 0.5`, `5` and `5.0`→`-R 5`, `0`→no `-R`, `-R 0.25` overrides, `task.ratelimit = 7`→no `-R`; local `ratelimit = 0.5`, one thread, four tasks: 9 s against 1 s unlimited | ❌ F2 |
| R9 | `docs/_include/config_set_desc.rst`, `config_set_help.rst`, `config_param_ref.rst`, `client_help.rst`, `cluster_help.rst`, `docs/cli/*.rst` | pages, help and `versionchanged:: 2.9.0` markers present; reference names `hs cluster` for attempts, eager, evict (the "stop before they start" sentence included) and `client.ratelimit`; top-level commands only; docs build `-W` clean | ❌ F5, F6 |
| S4 | markers | child-interpreter and CLI tests `@mark.integration`, in-process `@mark.unit`; strict markers pass | ✅ |
| S5 | one docs commit | `4937576b` | ✅ |

Unmapped changes (possible scope creep): none. The `LOGGING_FILE` `text != ''` change is R2 and does
not reach the environment layer (`logging.file` is in `EMPTY_UNSET`); the AGENTS.md and
`invariants.md` §10/§11 edits record the S3 bend and the new rule; the seed and ROADMAP status flips
to `adopted:config-set-and-unread` match reality. `share/` completions list option names only, and no
option was added or removed.

## Findings

No invariant violation (§1–§12) was found.

### [MEDIUM/CONFIRMED] F1 — A VALUE that is not valid UTF-8 truncates the config file to 0 bytes, with a traceback
- **Where:** `src/hypershell/core/config.py:697-698` (`update()` opens the file with `mode='w'`, then
  `tomlkit.dump` encodes); the value reaches it from `src/hypershell/config.py:302-318`.
- **Failure scenario:** a VALUE with a byte that is not UTF-8 arrives as a surrogate-escaped string;
  the text and untyped readers pass it through, and encoding fails after the file is truncated.
  Exit 5, a traceback file, `config.toml` at 0 bytes (the pre-write `.config.<timestamp>.toml`
  backup keeps the old content).
- **Evidence:** `printf '[server]\nport = 54321\n' > config.toml; hs config set --local
  database.pragmas.x "$(printf 'a\xff')"` → `rc=5`, `UnicodeEncodeError: 'utf-8' codec can't encode
  character '\udcff' … surrogates not allowed`, `Exception traceback written to …`, `config.toml`
  0 bytes. Identical on `origin/develop`'s `src` (pre-existing).
- **Touches requirement:** R4, "No VALUE SHALL make `hs config set` fail with a traceback". Not
  deferrable: refusing such a value before `update()` (exit 2, as R2's refusals) repairs it without
  touching how a value merges, so no criterion requires preserving it.

### [LOW/CONFIRMED] F2 — A non-finite `client.ratelimit` in a file makes every launched client refuse its command line
- **Where:** `src/hypershell/cluster/__init__.py:241-242` (the configured rate is never read as a
  rate); `src/hypershell/cluster/launch.py:490-502`, `542` (`plain_rate`).
- **Failure scenario:** `[client] ratelimit = inf` (or `nan`), a TOML float written by hand, is
  launched as `-R Infinity` (`-R NaN`); every launched client exits 2, so `--launcher`, `--mpi`,
  `--ssh` and autoscaling clusters fail, while `hs client` and a local `hs cluster` reading the
  same file run with no limit. `hs config set` and `HYPERSHELL_CLIENT_RATELIMIT` both refuse these
  values, so only a hand-written file reaches it.
- **Evidence:** with that file, `ClusterApp.ratelimit` is `inf` and `plain_rate(inf)` is
  `'Infinity'`; the reviewer's recording `--launcher` showed `-R Infinity` (and `-R NaN` for `nan`); `hs client -R Infinity …` → rc 2,
  `argument -R/--ratelimit: Rate 'Infinity' is not valid: expected a number`. Introduced: before the
  diff `hs cluster` did not read `client.ratelimit`.
- **Touches requirement:** R8, "every `hs cluster` client SHALL be limited … in every mode, as an
  `hs client` reading the key is".

### [LOW/CONFIRMED] F3 — `server.evict` beyond `timedelta`'s range passes `check_evict` and crashes with a traceback
- **Where:** `src/hypershell/server.py:811-817` (`check_evict` bounds the floor and finiteness
  only), `server.py:873-874` (`timedelta(seconds=…)` overflows).
- **Failure scenario:** `[server] evict = 1e300` (any period past about 8.6e13 seconds) is accepted
  by `check_evict`, then building the `HeartMonitor` raises `OverflowError`: exit 5 through the
  traceback writer, not the clean refusal S2 gives an infinite period.
- **Evidence:** `check_evict(1e300)` returns `1e+300`; `timedelta(seconds=int(1e300/10))` →
  `OverflowError: Python int too large to convert to C int`; `hs cluster t.in --no-db -N1 -H
  127.0.0.1` with that file → rc 5, `CRITICAL [hypershell.cluster] OverflowError …`, `Exception
  traceback written to …`. `hs server` on `origin/develop`'s `src` fails the same way
  (pre-existing there); in `hs cluster` it is new with the diff, which made `hs cluster` read the key
  and rewrote this check. The crash comes before any client is launched.
- **Touches requirement:** R7 and S2 (clean refusal of an unusable period). Repairable in
  `check_evict` for both commands at once, keeping R7's parity.

### [LOW/CONFIRMED] F5 — `hs config set`'s page says an empty variable never overrides a text key
- **Where:** `docs/_include/config_set_desc.rst:12-13`.
- **Failure scenario:** "An empty value for a key that takes text is written as `""`, so it
  overrides the key in a lower file, where an empty variable would not." That holds only for the
  optional text keys of `EMPTY_UNSET`; for any other text key an empty variable does override, as the
  guide itself says.
- **Evidence:** with `[logging] datefmt = "%H"` in the site file, `HYPERSHELL_LOGGING_DATEFMT= hs
  config get -r logging.datefmt` printed an empty value (overridden); without the variable it printed
  `%H`. The reviewer saw the same for `HYPERSHELL_AUTOSCALE_LAUNCHER=` over `launcher = "srun"`. Introduced.
- **Touches requirement:** R9 (the page states the rule; its prose must be accurate).

### [LOW/CONFIRMED] F6 — The `server.eager` reference omits `1`/`0` and says any other value stops the command
- **Where:** `docs/_include/config_param_ref.rst:289-291`.
- **Failure scenario:** the entry lists `"yes"`/`"no"`, `"on"`/`"off"`, `"true"`/`"false"` and then
  says "any other value stops the command before it starts"; `1` and `0`, as integers or text, are
  accepted. The `hs config set` page and the refusal message both list `1`/`0`.
- **Evidence:** with `eager = "1"`, `1`, `"0"` in the site file, `configured_eager(config.server.eager)`
  gave `True`, `True`, `False`; `"maybe"` raised `ConfigurationError … expected a boolean
  (true/false, yes/no, on/off, 1/0)`. Introduced.
- **Touches requirement:** R9.

### Considered and not raised
- A value of more than 4300 digits for a duration, size or untyped key is refused with exit 2 and a
  message carrying Python's integer-digit-limit text (`read_number`/`read_untyped` call `int()`).
  `hs config set` reads it exactly as the environment layer does (exit 3 there since #113), which is
  R3's and R4's stated anchor, and wide integers in `hs config set` are already the subject of the
  filed seed `issues/config-set-wide-integers.md`. Pre-existing in the shared reader; belongs with
  that seed.

## Human-gate triggers

Gate list per `review-rubric.md`: `data/model.py`, `server.py`, `client.py`,
`core/queue|tls|fsm|thread|signal.py`, `cluster/remote|ssh.py`, or a security / DB-lifecycle
invariant.

- **Triggered by F3** (`server.py`, `check_evict` and `HeartMonitor.__init__`). F3 is LOW and its
  repair is a bound in `check_evict`, but the rule is categorical: explicit human sign-off is
  required before any further step.
- F1 (`core/config.py`), F2 (`cluster/__init__.py`, `cluster/launch.py`), F5 and F6 (docs) do not
  touch the gate list. No security or DB-lifecycle invariant is touched.

## Routing

`changes-requested`: back to `/hs-build` for F1 (R4), F2 (R8), F3 (R7/S2), F5 and F6 (R9), after
the human sign-off F3 requires.
