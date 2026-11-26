---
status: shaped
shaped-as: task-output-storage
kind: feature
appetite: big
target: 2.10.0
lane: public
---

# Captured output for millions of tasks: one I/O layer, destinations by URL, retrievable anywhere

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim. Shaped with the maintainer
> on 2026-11-26. This is the first of three output cycles. The second is `task-output-bundles`
> (`tar://`) and the third is `task-output-stored` (`stored://`). `run-srun-semantics` builds on
> this one.

## Problem

HyperShell schedules millions of tasks over long campaigns, but captured output has no design to
match. Mapped on `develop` a093341e (2026-11-26):

- **One flat directory, kept forever.** Under `--capture`, `task_env()` (`client.py:559-586`) writes
  `<lib>/task/<id>.out`, `.err` and `.csv` into one directory per site, with no sharding.
  `TaskExecutor.start_task()` hands the files to `Popen` (`client.py:883-900`), and nothing ever
  removes them. Millions of small files in one POSIX directory is bad form on any filesystem, and
  worse on Lustre and GPFS.
- **Retrieval ignores the path stored on the row.** `outpath`/`errpath`/`csvpath` are unbounded
  `TEXT` holding an absolute path on the client host (`data/model.py:623-625`). `hs info --stdout`
  recomputes the path from the reader's own site (`task.py:299-312`). Only when that file is missing
  does it SFTP the files from `client_host` (paramiko, `core/remote.py`, `AutoAddPolicy`, no
  timeout), and it keeps the copies in the reader's `lib/task/` for good. Reads are in text mode, so
  binary output fails.
- **Relays strand output.** A relay's site clients never capture (`relay/site.py:138, 164-189`), and
  `hs relay` has no `--capture`. A result carries site-node paths upstream as they are, which the
  relay GOAL made a non-goal (`spec/relay/GOAL.md:203-205`).
- **Capture is a command-line flag only.** `--capture` has no configuration key. The launch contract
  (`AGENTS.md:502-505`) forbids new options on launched clients, so a destination must arrive by
  configuration.
- **S3 is already built on a branch that will not merge soon.** `feature/turso` P5 (commit 1d619254)
  added `core/s3.py`, `task.capture`, `TaskState.PUT_OUTPUT` and an `[s3]` extra with boto3. It
  spools locally, uploads at task end, records the `s3://` URI, and `hs info` reads it back. It was
  tied to Turso only by packaging; every document calls it provider-independent. Turso itself is
  blocked in review. The design is in `spec/turso/research/03-s3-output.md` on that branch.

## Outcome / vision

A deployment chooses where captured output lands, by configuration. Every command that shows output
finds it from the task row alone, wherever the task ran.

- **One module, `core.io`,** with one interface: put a spooled file at a destination and get back a
  locator; open a locator for reading. Schemes register behind it, so `TaskExecutor` does not grow a
  branch per backend. This cycle brings `file://` (today's paths, sharded) and `s3://` (turso P5,
  extracted). `tar://` and `stored://` follow in their own cycles.
- **The task always spools locally,** as today. A client-side push thread moves each finished task's
  files to the destination and records the locator on the row. A retention policy, modelled on the
  log handler's rotation thread, cleans up the spool.
- **Retrieval goes through `core.io`.** `hs info`, `hs run` and `hs wait` read the locator from the
  row, open it through `core.io`, and stream the bytes unchanged. SFTP becomes one transport behind
  `file://` on another host, not the retrieval design.

## Sketch of the acceptance criteria

Draft R-IDs, to be firmed up at promotion.

- **R1** — `core.io` SHALL define a destination interface (put, open, exists, delete) and a scheme
  registry, and `TaskExecutor` SHALL reach output storage only through it.
- **R2** — The destination SHALL be set by configuration (for example a `task.output` URL template),
  SHALL reach launched clients and relay site clients through configuration rather than options, and
  SHALL default to today's behaviour.
- **R3** — `file://` SHALL shard its directory so that no directory holds more than a bounded number
  of entries, and SHALL still read the flat paths written before 2.10.
- **R4** — `s3://` SHALL upload from the spool at task end, record the URI on success, and on failure
  keep the spool path and leave `exit_status` untouched (the turso P5 behaviour).
- **R5** — `hs info --stdout/--stderr/--perf`, `hs run` and `hs wait` SHALL read output from the
  row's locator, for every registered scheme, binary-safe, from any host that can reach the
  destination. A bare path SHALL read as `file://`.
- **R6** — A client thread SHALL delete a spooled file once its push is recorded. It SHALL never
  delete the only full copy. Under `file://` it SHALL keep spool files by an age or count policy, like
  the log handler's `keep`. The default SHALL keep everything, as today.
- **R7** — A relay SHALL pass the output destination to its site clients by configuration, so
  upstream `hs info` reaches output that ran at a site under `s3://`.

## Clarifications

- **Q:** fsspec, or our own backends? — **A:** Our own small backends behind the one `core.io`
  interface: boto3 loaded lazily for s3, paramiko for sftp, plain files locally (maintainer,
  2026-11-26).
- **Q:** What about S3 from `feature/turso` P5? — **A:** Extract it into `core.io` as the `s3://`
  backend. Turso's GOAL drops R13–R15 when that cycle is next picked up (maintainer, 2026-11-26).
- **Q:** Where does the locator live? — **A:** In the existing `outpath`/`errpath`/`csvpath` TEXT
  columns, as a URL. A bare path reads as `file://`. The locator needs no schema or wire change
  (maintainer, 2026-11-26).
- **Q:** How is the spool cleaned up? — **A:** By a client thread with a `keep`-style policy,
  modelled on the log handler's rotation thread. It never deletes the only full copy. The default
  keeps everything (maintainer, 2026-11-26).
- **Q:** Are relays in scope? — **A:** Yes, by configuration: the relay passes the destination to its
  site clients (maintainer, 2026-11-26).
- **Q:** How is the work cut? — **A:** Into three cycles, each in its own 2.10.0 alpha: this one,
  then `task-output-bundles`, then `task-output-stored` (maintainer, 2026-11-26).
- **Q:** Which comes first, this or `run-srun-semantics`? — **A:** This one; `hs run` builds on its
  retrieval (maintainer, 2026-11-26).

## Notes

- **Related seeds:**
  - `client-io-hygiene`: binary output, `ENOSPC`, stdin, and the dead transport code in
    `core/remote.py`.
  - `output-interleaving-line-atomic`: the merged `-o` stream.
  - `docs-and-runtime-surfacing` R2: document stranded output; this cycle makes that the fix rather
    than a caveat.
  - `turso` R13–R15.
- **Constraints:**
  - The launch contract allows no new client options.
  - `Task.from_json` rejects unknown keys, so a new column breaks peers on mixed versions.
  - A new `TaskExecutor` state needs an `actions` entry, and upload time holds an executor slot,
    which is one reason to push from a separate thread.
  - Optional backends need lazy imports (boto3 adds about 0.17 s at start-up).
  - `revert_columns` clears `outpath`/`errpath` but not `csvpath`, an existing inconsistency.
- **Precedents:**
  - Log rotation in `core/logging.py`: the size and cron handlers, naming policies, background
    compression, `keep`, and `claim_file_slot`.
  - SQLite partitions: `hs initdb --rotate` and the `part` column.
