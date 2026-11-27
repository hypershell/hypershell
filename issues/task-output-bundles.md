---
status: shaped
shaped-as: task-output-bundles
kind: feature
appetite: big
target: 2.10.0
lane: public
---

# Bundled output: append finished tasks' output to rolling tar archives

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`. Do not copy
> it verbatim. Shaped with the maintainer on 2026-11-26. This is the second of three output cycles.
> It depends on `task-output-storage` (the `core.io` layer, the push thread and retention).

## Problem

Under `task-output-storage`, every task still leaves two or three files at its destination. A fleet
of millions or billions of small tasks needs fewer, larger objects: far fewer inodes, far fewer
object-store requests, and output that can be kept, moved and purged as a unit. The context is in
`issues/task-output-storage.md`.

## Outcome / vision

A `tar://` destination tells the client's push thread to append each finished task's spooled output
to the current archive, one task at a time, and to roll to a new archive by a policy (size, count or
time), as log rotation does. An archive can sit on a local or shared filesystem or, through
`core.io`, in object storage. The locator on the row names the archive, the member, and the member's
byte offset and length. A reader range-reads exactly that member. A member never changes once it is
written, so a reader never has to ask whether the archive changed since it last looked.

## Sketch of the acceptance criteria

- **R1** — `tar://` SHALL append finished outputs to the current archive in a single writer per
  archive, and SHALL roll to a new archive by a configured size, count or time policy.
- **R2** — The locator SHALL record the archive, the member name, its offset and its length (and its
  codec, R4). Retrieval SHALL read that range only.
- **R3** — An archive SHALL stay a valid tar after every append, and SHALL be recoverable after the
  client is killed mid-append: a torn tail is trimmed or ignored, and members already recorded stay
  readable.
- **R4** — Each member MAY be compressed (zstd or gzip, set by configuration, off by default). The
  locator SHALL record the codec, so a reader decompresses after its range read.
- **R5** — A locator SHALL be recorded on the row only after its member is completely written, and
  the spool copy SHALL be released only then (retention, as in `task-output-storage` R6).
- **R6** — Archives SHALL be named so that clients on many hosts can share one destination directory
  without two writers ever appending to the same archive (compare `claim_file_slot`).

## Clarifications

- **Q:** Which formats? — **A:** tar only. zip writes its index at the end, which suits sealed
  archives, not one appended to while open (maintainer, 2026-11-26).
- **Q:** What does the locator record? — **A:** The archive, the member, and its byte offset and
  length, so a reader range-reads one member and never asks whether the archive changed (maintainer,
  2026-11-26).
- **Q:** Compression? — **A:** Optional, per member, zstd or gzip as the log rotation offers, off by
  default. The locator records the codec (maintainer, 2026-11-26).

## Open questions (for planning)

- **Locator syntax.** For example `tar:///site/out/part-0042.tar#<id>.out@<offset>+<length>[;zstd]`,
  or query parameters.
- **Whether a sealed archive gets an index member,** so a whole archive can be listed without
  scanning it.
- **Archives in object storage.** S3 objects cannot be appended to. Either the archive is built in a
  local spool and uploaded when sealed (members are readable only after sealing), or it is written
  in multipart pieces. Which one?
