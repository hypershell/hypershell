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

## Review cycle 2 — changes-requested (2026-11-21)

- **Reviewed commit:** `d6d83afa`  ·  **Base:** `origin/develop` `6a0c28ea` (the merge base; the branch
  fast-forwards it)  ·  **Cycle:** 2 of ≤3
- **Mode:** remediation plus a fresh pass, as the foreman's brief scoped it. The orchestrator graded
  the remediation of F1, F2, F3, F5 and F6 and the maintainer's ruling on the `server.eager`
  message. A fresh blind reviewer passed over the delta `f8293303..d6d83afa`, spec-excluded, and
  over the full diff wherever the delta touches it.
- **Reviewer inputs:** `GOAL.md`; the spec-excluded delta and full diff; the **base** copies of
  `invariants.md`, `AGENTS.md` and `review-rubric.md`; and, as binding contract readings, the
  plan-gate rulings S1–S5 and the review-cycle-1 rulings. It did not read `PLAN.md`, `TECH.md`,
  `REVIEW.md`, `META.md` or `research/`.
- **Contract drift:** none since cycle 1. `GOAL.md` has the same two commits, `28c4df99` and
  `81b762aa`.
- **Human sign-off for cycle 1's gate (F3):** given before the remediation, and recorded in the
  review-cycle-1 rulings commit `537c068e`.

### Verification run

- **Whole suite**, serial, Python 3.14, `uv run --frozen pytest -q`: 3824 passed, 138 skipped, 0 failed (26 min 8 s).
- **Targeted runs (reviewer):**
  - `tests/test_config_set_types.py tests/test_config_unread.py tests/test_cluster_evict.py
    tests/test_config_schema.py`: 370 passed.
  - `test_server_fsm.py test_env_blame.py test_env_config.py test_env_duration.py
    test_cluster_launch.py test_relay_roster.py test_zone_dispatch.py`: 320 passed, 1 skipped.
- **Docs:** `sphinx-build -W --keep-going -q -E -b html docs …` (Python 3.12, docs group) exits 0
  with no warnings.
- **CLI drives**, all in throwaway sites (`temp_site.sh`, `HOME` redirected into the site), with the
  key from `HYPERSHELL_SERVER_AUTH`, binding 127.0.0.1, and every run bounded by `drive/bound.sh`:
  - `hs config set` with values that are not UTF-8 under `--local` and `--user`;
  - `hs server` and `hs cluster` with `server.eager` given directly and by `_env`/`_eval`, from a
    file and from the environment;
  - `server.evict` at the cap, at the cap plus one, as `1e300`, and as a 401-digit integer;
  - `client.ratelimit` non-finite in five modes (local, `--launcher`, `--mpi`, `--ssh`,
    autoscaling);
  - `hs config set` under `LC_ALL=en_US.ISO8859-1`;
  - `origin/develop` and `f8293303` driven from `git archive` extracts through `PYTHONPATH`.

  The orchestrator reproduced each finding below a second time.
- **Merge:** `origin/develop` is the merge base. `git merge-tree --write-tree HEAD origin/develop`
  gives `a61f9697`, which is `HEAD^{tree}`: it merges cleanly and fast-forwards.

### Remediation of cycle 1

| Item | Ruling | Status | Evidence |
|------|--------|--------|----------|
| F1 | refuse a VALUE that cannot be written as UTF-8, exit 2, nothing written | ✅ under a UTF-8 locale; F7 remains (same root cause) | **Branch:** `logging.datefmt`, `database.pragmas.x`, `server.port` and `server.eager` with a byte `\xff` each exit 2 with `<key>: expected text that can be written as UTF-8, given …`. The file stays 22 bytes, no new `.config.*` backup is made, no exception log is written, and `--user` creates no file. **Base and `f8293303`:** exit 5 and a 0-byte file. |
| F2 | `hs cluster` refuses a non-finite `client.ratelimit` at start, before anything launches | ✅ | **File `ratelimit = inf` or `1e400`:** in local, `--launcher`, `--mpi`, `--ssh` and autoscaling modes, exit 2 with `client.ratelimit: Rate inf is not valid: expected a finite number (from: …)`. Nothing launches and nothing listens. **Text forms:** `ratelimit_eval` printing `nan` exits 2 (refused by `-R`'s type); `HYPERSHELL_CLIENT_RATELIMIT=inf` exits 3 (the environment layer). |
| F3 | `check_evict` refuses a period too large for the heartbeat arithmetic, clean exit 5 | ⚠️ partly: F8 | `86399999913600` is accepted. `86399999913601`, `1000000000d` and `1e300` exit 5 cleanly in both commands. An integer beyond float range still ends in a traceback (F8). |
| F5 | the page limits the empty-variable rule to the empty-means-not-given set | ✅ | Over a file's `bind = "10.1.2.3"`, `HYPERSHELL_SERVER_BIND=` still gives that address, while `HYPERSHELL_LOGGING_DATEFMT=` gives `''`. `hs config set … ''` writes `""`, which overrides both. |
| F6 | `.eager` lists every accepted word, `1`/`0` included | ✅ | In both commands, `"no"`, `"off"`, `"0"`, `0` and `false` give off, and `1`, `"1"`, `true`, `"YES"` and `" on "` give on. `2`, `1.0`, `"maybe"` and `""` exit 3, naming the key and the file. |
| Message ruling | a bad `server.eager` names the key and its source, never a value resolved from `_env`/`_eval` | ✅ | Thirteen layerings (eight by the orchestrator, five by the reviewer) in both commands: file, environment, env-over-file and user-under-local, by `_env`, `_eval` and the direct form. Each bad value exits 3 and names `server.eager_env` or `server.eager_eval` and the right file or variable. The hidden value appears in no output and in no file of the site at TRACE. The direct form names its value, as the ruling allows. An `_env` naming an unset variable exits 3, naming it, as `config_seconds` does on the base. |

**The builder's three widenings, all sound:**
- **F1 also refuses a path that is not UTF-8.** Base and `f8293303` truncate the file the same way
  for `database.pragmas.\xff`. Refusing it before the write repairs the same data-loss defect, and
  the page documents it.
- **F2 exits 2 and checks only non-finite numbers.** Exit 2 is the status of `-R`'s own refusal
  and of the `autoscale.period` refusal beside it. Text from any source already reaches `-R`'s type
  as a string default.
- **F6's wording also changed on the `hs server` and `hs cluster` pages.** The same omission was
  there; it maps to R9.

**Unmapped changes:** none. Every hunk of the delta maps to F1, F2, F3, F5, F6 or the message
ruling.

### Findings (cycle 2)

No invariant violation (§1–§12) was found.

#### [MEDIUM/CONFIRMED] F7 — Under a locale whose encoding is not UTF-8, `hs config set` truncates the file or writes one no command can read
- **Where:** `src/hypershell/core/config.py:692,694,697`. `update()` opens the file with
  `open(config_path, mode=…)` and no `encoding=`, so it reads and writes in the locale's encoding.
  The configuration reader always decodes UTF-8. F1's guard (`src/hypershell/config.py:311-316`)
  checks against UTF-8 only.
- **Failure scenario:** the user runs `hs config set` under a locale such as ISO-8859-1, or under a
  Windows ANSI code page, a listed platform where Python 3.11–3.14 opens files in the code page.
  - A character the locale cannot encode: the file is truncated to 0 bytes, the command exits 5
    and a traceback file is written. The pre-write backup keeps the old content.
  - A character it can encode: the file holds bytes in that encoding, which is not TOML, and every
    later command exits 3 until the file is edited by hand.
- **Evidence:**
  - `printf '[server]\nport = 54321\n' > config.toml; LC_ALL=en_US.ISO8859-1 hs config set
    --local logging.datefmt '€'` → rc 5, `UnicodeEncodeError: 'latin-1' codec can't encode
    character '€'`, `config.toml` at 0 bytes. The next `hs config get server.port` prints the
    default `50001`. `database.pragmas.x '€'` fails the same way.
  - `… logging.datefmt 'é'` → rc 0. The next `hs config get server.port`, under a UTF-8 locale,
    gives rc 3 with `ConfigurationError: … UnicodeDecodeError`.
  - **Pre-existing:** identical on `origin/develop`'s `src`.
- **Touches requirement:** R1 (the file holds VALUE as a TOML string as given) and R4 (no VALUE
  makes `hs config set` fail with a traceback).
- **Not deferrable:** no criterion requires preserving this. Reading and writing the file as UTF-8
  repairs it without touching how a value merges (a non-goal). This is the root cause cycle 1's F1
  pointed at: the file is truncated before the encoding fails.

#### [LOW/CONFIRMED] F8 — An integer `server.evict` beyond float range escapes F3's cap and crashes with a traceback
- **Where:** `src/hypershell/server.py:824`. `math.isfinite(seconds)` raises `OverflowError` for an
  integer wider than a float, so the cap check at `:826` is never reached.
- **Failure scenario:** any of these, in `hs server` or `hs cluster`:
  - a file's `evict = 1` followed by 400 zeros;
  - `HYPERSHELL_SERVER_EVICT` set to such a number;
  - a duration text of that size, such as `"<401 digits>d"`.

  Each exits 5 through the traceback writer, not with the clean refusal the F3 ruling gives a
  period past the cap.
- **Evidence:**
  - `HYPERSHELL_SERVER_EVICT=1$(printf '%0400d' 0) hs server t.in --no-db -H 127.0.0.1` → rc 5,
    `CRITICAL [hypershell.server] OverflowError: int too large to convert to float`, `Exception
    traceback written to …`. The same holds for `hs cluster t.in --no-db -N1 -H 127.0.0.1` and for
    the file form.
  - In process, `check_evict(10**400)` raises `OverflowError`.
  - **Base:** `hs server` fails with `OverflowError: integer division result too large for a
    float`, so this is pre-existing there. `hs cluster` did not read the key.
- **Touches requirement:** R7 and the F3 ruling (a clean exit 5 for a period too large).

#### [LOW/CONFIRMED] F9 — A rate under about 1e-14 tasks per second per thread kills the task executor, and `hs cluster` hangs
- **Where:**
  - `src/hypershell/client.py:992`: `timedelta(seconds=1/self.ratelimit)` overflows.
  - The value is accepted by `parse_rate` (`src/hypershell/core/types.py:181-185`) and passes
    F2's finite check (`src/hypershell/cluster/__init__.py:388`).
- **Failure scenario:** `-R 1e-300`, or `[client] ratelimit = 1e-300`, under `hs cluster`.
  - The first task runs, then the executor raises `OverflowError` and the cluster never finishes.
  - A launched client (`--launcher`) dies the same way.
  - An integer rate beyond float range also fails, with an `OverflowError` traceback at start.
    That case predates the branch for `-R`, whose base type was `int`.
- **Evidence:**
  - `printf 'echo one\necho two\n' > t.in; hs cluster t.in --no-db -N1 -H 127.0.0.1 -R 1e-300`
    prints `one`, then `CRITICAL [hypershell.core.fsm] Uncaught exception from <class
    'hypershell.client.TaskExecutor'>` and `OverflowError: Python int too large to convert to C
    int`. It is still running at the 30 s bound (rc 124).
  - The file form behaves the same.
  - The reviewer saw the `--launcher` client fail the same way. `ratelimit = 0.5` completes.
  - **New with the branch:** the base refused `-R 1e-300` (`invalid int value`), and `hs cluster`
    did not read the key. `hs client` reading such a value from a file predates the branch.
- **Touches requirement:** R8 ("a fractional rate SHALL limit the rate, never make a launched client
  fail").
- **Note:** the input is absurd (one task in about three million years). It is raised because the
  maintainer's F3 ruling treated the matching absurd eviction period as one to refuse cleanly, and
  here the result is a hang, not a refusal. The human may waive it at the gate.

#### [PLAUSIBLE] F10 — Feature-scoped spec ids in the branch's test docstrings
- **Where:** 36 parenthesized ids such as `(R4)` and `(R7, S2)`, 9 of them added by this delta:
  `tests/test_config_unread.py` (22), `tests/test_config_set_types.py` (8) and
  `tests/test_cluster_evict.py` (6).
- **Rule:** `AGENTS.md`, "Working on this codebase as an agent": "**Never embed feature-scoped spec
  ids** (`R#`, `P#`) in source". The ids restart per feature and mean nothing in the merged tree.
- **Why PLAUSIBLE:** whether test docstrings are "source" under that rule is a reading for the
  human. The base has precedent: `src/hypershell/core/logging.py` (4) and `tests/test_logging.py`
  (8). The rule is not among `invariants.md` §1–§12. If the human reads tests as source, this grades
  HIGH as a project convention, and stripping the ids is mechanical. It does not auto-loop.

### Considered and not raised
- A `server.eager_env` that names an unset variable exits 3 instead of reading as unset. It matches
  the base's `config_seconds` rule for duration keys, and R5's "unset" means the key is not given.
- When the text form of `client.ratelimit` is refused, the message names `argument -R/--ratelimit`
  even though the value came from a file. Argparse parses a string default this way, and the
  behaviour predates the branch.

### Human-gate triggers
- **Triggered by F8** (`server.py`, `check_evict`) and **F9** (`client.py:992`, the
  `TaskExecutor`'s rate arithmetic). Both are LOW, but the rule is categorical: explicit human
  sign-off is required before any further step.
- F7 (`core/config.py`) and F10 (tests) are not on the gate list. No security or DB-lifecycle
  invariant is touched.

### Routing
`changes-requested`, cycle 2 of ≤3. After the human sign-off that F8 and F9 require, the branch goes
back to `/hs-build` for F7 (R1/R4), F8 (R7, the F3 ruling) and F9 (R8), and for F10 if the human
rules that it applies. One cycle remains before the bound. F8 and F9 are absurd-input edges, so the
human may narrow or waive either at the gate to keep the loop converging.

## Review cycle 3 — changes-requested (2026-11-22)

- **Reviewed commit:** `4ae6a324`  ·  **Base:** `origin/develop` `6a0c28ea` (the merge base; the branch
  fast-forwards it)  ·  **Cycle:** 3 of ≤3, the last
- **Mode:** remediation plus a fresh pass, as the foreman's brief scoped it. The orchestrator graded
  the remediation of F7, F8, F9 and F10, the maintainer's ruling on `hs config set`'s refusal
  messages, and the builder's recorded deviations. A fresh blind reviewer passed over the delta
  `d6d83afa..4ae6a324`, spec-excluded, and over the full diff wherever the delta touches it.
- **Reviewer inputs:** `GOAL.md`; the spec-excluded delta and full diff; the **base** copies of
  `invariants.md`, `AGENTS.md` and `review-rubric.md`; and, as binding contract readings, the
  plan-gate, review-cycle-1 and review-cycle-2 rulings. Each was handed over as a copy in a curated
  folder outside the repository. It did not read `PLAN.md`, `TECH.md`, `REVIEW.md`, `META.md`,
  `research/` or `drive/`.
- **Contract drift:** none since cycle 1. `GOAL.md` has the same two commits, `28c4df99` and
  `81b762aa`.
- **Human sign-off for cycle 2's gate (F8, F9):** given before the remediation, and recorded in the
  review-cycle-2 rulings commit `2f7587fc`.

### Verification run

- **Whole suite**, serial, Python 3.14, `uv run --frozen pytest -q -p no:cacheprovider`: 3860 passed, 138 skipped, 0 failed (26 min 41 s; the 2 warnings are the base's `test_logging.py` fork tests).
- **Targeted runs (reviewer):**
  - `tests/test_config_set_types.py tests/test_config_unread.py tests/test_cluster_evict.py`: 246
    passed. The two Latin-1 tests ran: this host has `en_US.ISO8859-1`.
  - `test_config_schema.py test_cluster_launch.py test_server_fsm.py test_zone_dispatch.py
    test_relay_roster.py test_topology_server.py`: 397 passed, 1 skipped.
- **Red on `d6d83afa`:** the delta's new tests, run from `HEAD` against a `git archive` extract of
  `d6d83afa`'s `src` through `PYTHONPATH`. The orchestrator saw 6 failed and 1 passed in
  `test_config_set_types.py`: the four key-form cases, the two Latin-1 tests, and the passing
  guard. It saw 26 failed and 34 passed in `test_cluster_evict.py` and `test_config_unread.py`: the
  18 wide-integer eviction cases, the 8 under-floor rate cases, and the guards passing. The
  reviewer's run agrees: 32 failed, 21 passed.
- **Docs:** `sphinx-build -W --keep-going -q -E -b html docs …` (Python 3.12, docs group) exits 0.
  The new sentences render in `config.html`, `cli/client.html`, `cli/cluster.html` and
  `cli/config_set.html`.
- **CLI drives**, all in throwaway sites (`temp_site.sh`, `HOME` redirected into the site), with the
  key from `HYPERSHELL_SERVER_AUTH`, binding 127.0.0.1, and every run bounded by `drive/bound.sh`:
  - `hs config set` under `LC_ALL=en_US.ISO8859-1` (`PYTHONUTF8=0`), and under `LC_ALL=C` with
    locale coercion off: `--local` and `--user`, a new file and an existing one, with an existing
    UTF-8 comment kept;
  - the six forms of `server.auth` and `relay.upstream.auth`, and `Server.Auth`, each with a byte
    that is not UTF-8;
  - `server.evict` from a file, the environment, `_env` and `_eval`: 401-digit integers (plain,
    negative and as days), `inf`, `-inf`, `nan`, `1e300`, the cap plus one, and the cap itself;
    then day counts of 4,296 to 4,401 digits, on the branch and on `origin/develop`;
  - `-R` and `client.ratelimit` (a file's number and text, `_env`, `_eval` and the environment
    variable) at, under and around the floor, under `hs client` and `hs cluster`, in local and
    `--launcher=env` modes;
  - `hs client` against an `hs server --no-db` with a file's `ratelimit = 1e-300`, on the branch
    and on `origin/develop`.

  The orchestrator reproduced each finding below itself.
- **Merge:**
  - `git merge-tree --write-tree HEAD origin/develop` gives `89b7b01f`, which is `HEAD^{tree}`:
    the branch merges cleanly and fast-forwards.
  - Against `origin/fix/submit-input-checks` (`44e177b4`, #118), the merge is clean (tree
    `2adadf38`). Its docs build `-W` exits 0. On that merged tree,
    `test_config_set_types.py test_config_unread.py test_cluster_evict.py test_cluster_launch.py
    test_group_integer.py test_reached_group.py test_resubmit_lower_group.py` gives 494 passed and
    44 skipped.

### Remediation of cycle 2

| Item | Ruling | Status | Evidence |
|------|--------|--------|----------|
| F7 | write the file, and its backups, as UTF-8 whatever the locale | ✅ | Under `en_US.ISO8859-1`, `logging.datefmt '€'` exits 0. The file holds `€` as UTF-8 beside the seed's `port`, the one backup is the seed byte for byte, and no exception log is written. An `é` set there, then a second set, reads back by `hs config get` under both locales. A new `--user` file is UTF-8, and an existing UTF-8 comment (`résumé €`) is kept as written. On `d6d83afa`, the first exits 5 with the file truncated, and the second exits 3 with a `UnicodeDecodeError`. |
| F8 | `check_evict` compares against the cap before any float conversion | ⚠️ partly: N2 | A 401-digit integer, from a file, the environment, `_env` or `_eval`, or as days, exits 5 with the cap's message in both commands, with no exception log. `inf` gives the finite-number message. `-inf`, `nan` and negative wide integers give the floor's message, as before. The cap passes and the cap plus one is refused. A day count of 4,298 digits or more still ends in a traceback (N2). |
| F9 | `-R` and `client.ratelimit` refuse a rate under a floor, exit 2 naming the option or key | ✅ for `-R` and for `hs cluster`; N1 open for triage | `-R` with `1e-300`, `9e-7`, `-1e-300`, `0.0000009`, `1e-7` and `5e-324` exits 2 on both commands, naming `-R/--ratelimit` and the floor. Under `hs cluster`, a file's `1e-300`, `-1e-300`, `9.99e-7` or `5e-324`, in local and `--launcher=env` modes, exits 2 naming `client.ratelimit` and the file. `HYPERSHELL_CLIENT_RATELIMIT=1e-300` names the variable. Text, `_env` and `_eval` forms exit 2 through `-R`'s type. `1e-6` passes the start check, and `0`, `-5` and `0.5` run to completion. `hs client` reading the key from a file or the environment is N1. |
| F10 | no spec ids in the branch's test docstrings | ✅ | No added line in `tests/`, `src/` or `docs/` of the full diff matches `(R#`, `(P#`, `(S#`, `F#`, `change N`, GOAL or Clarifications. All five test files the branch adds or edits carry none. |
| Refusal messages | a refusal in `hs config set` names the key | ✅ | Each of the six forms of `server.auth` and `relay.upstream.auth`, and `Server.Auth`, with a byte that is not UTF-8, exits 2 with `<path>: expected text that can be written as UTF-8`. The file is as before and no backup is left. A typed key's refusal is unchanged (`server.port: expected a whole number, given 'abc'`). Public text says only that a refusal exits 2. A key form takes text, so the UTF-8 refusal is the only one it can meet. |

**The builder's recorded deviations:**
- **The floor is by magnitude; `0` and negative rates are left alone.** Sound. A negative rate
  gives a negative wait, so it does not limit, as on the base, and `-5` runs to completion. A tiny
  negative overflows the wait the same way a tiny positive does, so refusing it is the same repair.
- **The Latin-1 tests skip on a host without such a locale.** Acceptable: the fix is an explicit
  `encoding='utf-8'` on each `open()` of the write path, and the tests ran and were red here. CI
  runs on `ubuntu-latest` only, whose default locales are UTF-8 ones, so CI will likely skip both
  tests (not verified here).
- **Gap: `hs client` reading a TOML number under the floor.** Graded as N1 below.
- **Gap: an integer rate wider than a float.** Not raised. Cycle 2 named it inside F9 and the
  maintainer's ruling asked for a floor only. It predates the branch for `-R`, whose base type was
  `int`. Under `hs cluster` a file's 401-digit `ratelimit` now exits 5 at start with a traceback,
  in local mode at `client.py`'s per-thread division and in `--launcher` mode at `plain_rate`'s
  `float()`. It never hangs. A candidate seed.

**Unmapped changes:** none. Every hunk of the delta maps to F7, F8, F9, F10 or the refusal ruling.

### Findings (cycle 3)

No invariant violation (§1–§12) was found.

#### [LOW/CONFIRMED] N2 — A day count of about 4,300 digits makes `check_evict`'s message raise instead of refusing
- **Where:** `src/hypershell/server.py:827` (and `:823`, `:826`). The message formats `given
  {seconds}`. Python refuses to turn an integer of more than 4,300 digits into text (its default
  int-to-text limit), so the `RuntimeError` is never raised.
- **Failure scenario:** `server.evict` given as a day, hour or minute count that the duration parser
  still reads (at most 4,300 digits) but whose seconds pass 4,300 digits. For example,
  `HYPERSHELL_SERVER_EVICT=1` followed by 4,298 zeros and `d`. `hs server` and `hs cluster` exit 5
  through the traceback writer, not with the cap's clean refusal that F8's ruling gives an integer
  wider than a float.
- **Evidence:**
  - With 4,298 or 4,299 zeros and `d`, both commands exit 5 with `ValueError: Exceeds the limit
    (4300 digits) for integer string conversion` and an exception log.
  - With 4,295 zeros they exit 5 cleanly with the cap's message.
  - In process, `check_evict(10**4300)` raises `ValueError`, and `check_evict(10**4299)` raises the
    cap's `RuntimeError`.
  - **Before the delta:** on `d6d83afa` the same input failed earlier, with an `OverflowError` from
    `math.isfinite`. The remediation narrowed F8 to this window without closing it.
  - **The same width one step earlier (context, not counted separately):**
    - A unit form of more than 4,300 digits (for example 4,400 zeros and `d`) fails before
      `check_evict`, inside `config_seconds` (`src/hypershell/core/config.py:367`).
      `_match_seconds` builds a `Fraction` from the digits and raises `ValueError`, and the
      helper catches only `OverflowError`.
    - The result is exit 5 with a traceback. `hs server` behaves identically on `origin/develop`.
      Under R7 `hs cluster` now does what `hs server` does; on the base it ignored the key.
    - A plain number that wide is refused cleanly by the environment layer, with exit 3.
- **Touches requirement:** R7 and the F8 ruling (an integer wider than a float gets the same clean
  exit 5).
- **Not deferrable:** no criterion requires preserving it.
- **Fix (small):** in `check_evict`, name the value only when it can be printed, for example
  through a helper that falls back to `an integer of N digits` (`len` from `math.log10`, or the
  bit length) when `str()` raises `ValueError`. Add one `REFUSALS` case to
  `tests/test_cluster_evict.py`: `'1' + '0' * 4298 + 'd'`, exit 5, no exception log.
- **Optional, same class:** `config_seconds` could take `_match_seconds` into its `try` and treat a
  `ValueError` like an `OverflowError`. Its own refusal then gives exit 3 naming the key, for
  `server.evict` and every other key it reads. `core/config.py` is not on the gate list.
- **Note:** the input is absurd. A cap of 86,399,999,913,600 seconds is already about 2.7 million
  years, and this one is a number of days with 4,299 digits. The human may waive it at the gate.

#### [PLAUSIBLE] N1 — `hs client` takes a `client.ratelimit` under the floor from a file or the environment
- **Where:**
  - `src/hypershell/client.py:1658`: the `-R` default is `config.client.ratelimit`, and argparse
    does not run a non-text default through `parse_rate`.
  - `ClientApp.check_args` (`client.py:1751`) has no floor check.
- **Failure scenario:** a file's `[client] ratelimit = 1e-300`, or
  `HYPERSHELL_CLIENT_RATELIMIT=1e-300`, under `hs client`. The first task runs. Then the executor
  dies with `OverflowError` at `client.py:992`, and the client does not exit by itself. The same
  value as TOML text (`"1e-300"`) is refused with exit 2 by `-R`'s type.
- **Evidence:** against an `hs server --no-db` on 127.0.0.1, the branch prints `one`, then
  `Uncaught exception from <class 'hypershell.client.TaskExecutor'>` and `OverflowError: Python int
  too large to convert to C int`. It is still running at the 30 s bound. `origin/develop` behaves
  identically, so this is pre-existing.
- **Why PLAUSIBLE:** the F9 ruling says "`-R` and `client.ratelimit` refuse a rate below a floor".
  Cycle 2's F9 concerned `hs cluster` (R8) and recorded `hs client`'s own reading as pre-existing.
  GOAL's non-goals keep `hs client` "unchanged except `-R`'s form", and the docs name only `-R`
  and `hs cluster`. Whether the ruling reaches `hs client`'s own reading of the key is the human's
  call. It does not auto-loop.
- **If ruled in scope:** in `ClientApp.check_args`, refuse a float `ratelimit` that is not finite,
  or whose magnitude is under `RATE_MINIMUM` (but not 0), with exit 2. Name `client.ratelimit` and
  its source, as `ClusterApp.check_arguments` does. Add tests for the file and environment forms.
  This touches `client.py`, so it is behind the gate.
- **If ruled out of scope:** a candidate seed, together with `hs client`'s non-finite file value,
  which behaves the same way.

### Considered and not raised
- **No `versionchanged` marker for writing UTF-8 in any locale.** This is a bug fix: 2.8 wrote a
  file no reader could decode. The prose is in the `hs config set` include, so it is not a §12
  finding.
- **`hs relay`'s copy of the configuration for a site** (`src/hypershell/relay/site.py:473`) is
  written in the locale's encoding. This predates the branch, `relay/` is unchanged, and `hs relay`
  is a non-goal. A candidate seed.
- **`hs cluster` with a rate at the floor does not finish within the bound.** This is the limiter's
  design: a result is held until 1/rate seconds have passed since the task was received.
- **The new `is_key_variable(env_variable(…))` line** raised nothing for odd paths: the reviewer
  fuzzed 20,000 random paths, and the orchestrator tried `server.`, `.x`, `a..b`, `relay.zone.x` and
  `relay.zone.`. The one-word path is refused before it, as before.

### Human-gate triggers
- **Triggered by N2** (`server.py`, `check_evict`). It is LOW, but the rule is categorical:
  explicit human sign-off is required before any further step.
- **N1**, if the human rules it in scope, touches `client.py` and is gated too.
- No security or DB-lifecycle invariant is touched.

### Routing
`changes-requested` at cycle 3 of ≤3. This is the bound, so the loop does not continue on its own:
it **escalates to the maintainer**.

**For N2**, one of:
- waive it as an absurd input, which approves the branch as it stands, apart from N1's triage; or
- approve the small fix above (a few lines in `check_evict` and one test case), with a confirming
  pass scoped to it.

**For N1**, rule it in scope (the fix above) or out of scope (a seed).

**Candidate seeds:**
- an integer rate wider than a float;
- `hs client`'s file rate checks, if N1 is ruled out;
- the relay site copy's encoding.
