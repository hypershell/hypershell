---
status: shaped
shaped-as: task-output-stored
kind: feature
appetite: big
target: 2.10.0
lane: public
---

# Stored output: small tasks' output kept in the database, sent through the server in chunks

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`. Do not copy
> it verbatim. Shaped with the maintainer on 2026-11-26. This is the third of three output cycles. It
> depends on `task-output-storage` (`core.io`, the push thread, retention).

## Problem

Fleets of very many small tasks with small outputs are better served by keeping the output in the
database than by any file or object per task: there are no inodes, retrieval is a query, and the
database's own storage compresses it (PostgreSQL TOAST, TimescaleDB columnstore). But clients never
open the database. They talk only to the server, so the output has to travel through the server.
Today the only path from client to server is the result bundle. A bundle of `bundlesize` results is
encoded, sent, decoded and written as one message. The wire has no byte cap (the codec limits
nesting and item counts only, `core/codec.py:60-63`). Megabytes of output inside the results would
hold memory at every hop, delay the results that drive scheduling, and fill a relay's
`relay_result.record`.

## Outcome / vision

A `stored://` destination sends each task's output to the server in chunks of about 256 KiB. They
travel over a new request on the queue manager that is acknowledged per chunk and separate from the
result bundles. The server writes the chunks to an output table. The client's push thread releases
the task's result into the normal bundle only after its last chunk is acknowledged, so a result never
points at output that is not stored.

The client always writes its local spool too, as today. Up to a cap (about 1 MB per stream by
default) the stored copy is the whole stream. Past the cap the stored copy is the part that fits, and
the row says so: it records the cut, the original size, and the spool's location, from which the full
output stays recoverable.

## Sketch of the acceptance criteria

- **R1** — `stored://` SHALL send output over a dedicated request with a reply, in chunks, never
  inside a result bundle, and the server SHALL commit each chunk before it acknowledges it.
- **R2** — A task's result SHALL be sent only after its last chunk is acknowledged.
- **R3** — The cap SHALL be configurable per stream, defaulting to about 1 MB. A stream over it SHALL
  be stored up to the cap, and its locator SHALL record that it was cut, its original size, and the
  client spool location of the full copy.
- **R4** — The client SHALL always keep the spool. A spool file that holds the only full copy SHALL
  NOT be deleted until retention allows (`task-output-storage` R6).
- **R5** — A client that meets a server without the request (older than 2.10) SHALL refuse at start
  or fall back to a configured destination, never send output inside the result.
- **R6** — Chunks MAY be compressed per chunk (zstd or gzip, off by default), and the locator SHALL
  record the codec.
- **R7** — `hs info`, `hs run` and `hs wait` SHALL read stored output by its locator through
  `core.io`. When a stream was cut, they SHALL say so and name the spool location.
- **R8** — The output table SHALL work on SQLite, PostgreSQL and TimescaleDB, with chunk rows
  (task, stream, sequence, bytes). On TimescaleDB it SHALL compress as `task` does, and on SQLite it
  SHALL follow the partitions.
- **R9** — Through a relay, chunks SHALL be forwarded upstream without being stored whole in the
  relay's result table.

## Clarifications

- **Q:** How does output reach the database? — **A:** Through the server, over a dedicated,
  acknowledged, chunked request, not inside the task bundles (maintainer, 2026-11-26).
- **Q:** What size limit? — **A:** Up to about 1 MB per stream by default, behind a configurable cap
  (maintainer, 2026-11-26).
- **Q:** What happens past the cap? — **A:** The client always writes its local spool as well, so a
  stream the cap cuts stays recoverable in full from the spool, and the row records where (maintainer,
  2026-11-26).
- **Q:** Compression? — **A:** Optional per chunk, off by default, with the codec recorded
  (maintainer, 2026-11-26).

## Open questions (for planning)

- **Retention of the output table itself.** Should it rotate with `hs initdb --rotate` on SQLite? On
  TimescaleDB, should it have a retention policy, or be dropped together with `task` chunks?
- **The output request's name and limits** (chunk size, concurrency per client), and how the server
  applies backpressure across many clients.
- **Live output for `hs run`.** Sending chunks while a task runs, rather than only at its end, would
  let `hs run` tail its task. That is a follow-on in `run-srun-semantics`.
