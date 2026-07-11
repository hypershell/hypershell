# GOAL — Safe re-submission: `--restart` / `--repeat` / `--update` source gating

> **Origin spec.** The *what* and *why* — the locked contract `hs-review` grades against.
> The *how* lives in [`PLAN.md`](PLAN.md) and [`TECH.md`](TECH.md) (written by `hs-plan`).
> Keep this at the right altitude: solved and bounded, but not over-specified — leave design
> freedom for the plan. Edit requirements here; do **not** silently drift them during build.

- **slug:** cli-cluster-restart-repeat-update
- **kind:** feature
- **appetite:** big

## Problem

The very first thing new users tend to do is run the `hsx <taskfile>` workflow inside a Slurm job
with `--restart` so a requeued job can pick up where it left off. `--restart` has since been
**disabled** to protect users from accidentally double-submitting the same tasks into a live
database. The current safe pattern — run `hs submit` out-of-band (e.g. on a login node) once, then
put a bare restart-style `hsx` in the job script — is awkward to explain and is exactly the friction
a first-time user hits first.

We want re-submission to be safe *by construction* so that expressing intent (restart, repeat,
update) relaxes the refusal gate only where it is provably safe, and refuses loudly where it is not.
The engine already reverts interrupted tasks on server start; what is missing is a way to know
whether a given task file has already been ingested, whether all of its tasks actually landed, and
which tasks are genuinely new — cheaply, even on databases holding billions–trillions of rows.

## Outcome / vision

A user can put `hsx <taskfile> --restart` in a job script and requeue the job as many times as they
like: each run detects the prior submission, submits only the tasks that never made it, and lets the
existing revert-interrupted flow re-run anything that was mid-flight — never duplicating work. When a
user *changes* the file they get a clear path (`--update`) that submits only the novel tasks, and
when they genuinely want to run the same work again they get another clear path (`--repeat`).
Ambiguous or contradictory intent is refused with an actionable message. Detection stays fast and
well-logged at extreme scale.

To make this possible the database gains a **source** record per ingested named file (a unique
source UUID, the absolute path, a file-content fingerprint, the ingested task count, a timestamp),
each task is stamped with its **source** and a stable **identity fingerprint**, and submission
consults these to decide whether to proceed, de-dup, or refuse.

## Acceptance criteria (the contract)

### Source & task identity

- **R1** — The system SHALL record, for every ingested **named** task file, a source record
  capturing at least: a unique source UUID, the absolute file path, a file-content fingerprint
  (e.g. md5), the ingested task count, and a creation timestamp.
- **R2** — The system SHALL stamp every task with a stable **identity fingerprint** derived from a
  canonical (order-independent) hash of the task's **args**, **group**, and **tags**. `args` is the
  raw submitted argument string *before* template expansion — the meaningful unit of work, since the
  resolved command can differ by template. The UUID, attempt/retry counters, timing, exit status,
  and the execution template SHALL NOT participate in identity.
- **R3** — The system SHALL associate every task ingested from a named file with its source UUID.
  Single command-line submissions (`hs submit '<cmd>'`) SHALL use the reserved source `<direct>` and
  streamed/stdin submissions SHALL use `<stdin>`; both are considered explicit user intent and are
  **exempt** from all gating in R5–R14.

### `hs submit` gate matrix

- **R4** — WHEN `hs submit <file>` ingests a named file, the system SHALL read the file upfront to
  compute its source fingerprint and task count; `<stdin>` submissions SHALL remain streamed and are
  exempt from fingerprint/count gating (R3).
- **R5** — WHEN `hs submit <file>` is invoked with no gating flag and the file's (path, fingerprint)
  matches a prior source, the system SHALL refuse and exit without submitting, identifying the prior
  submission.
- **R6** — WHEN `hs submit <file>` is invoked with no gating flag and the path was seen before but
  the fingerprint differs, the system SHALL refuse and exit, suggesting `--update`.
- **R7** — WHEN a named file's source is known, the system SHALL compare the DB task count for that
  source against the source's recorded count and SHALL warn if fewer tasks landed than expected
  (an incomplete prior submission).
- **R8** — WHEN `hs submit <file> --repeat` is invoked, the system SHALL ingest the file as a new
  source and submit all of its tasks even if an identical prior source already exists.
- **R9** — WHEN `hs submit <file> --update` is invoked, the system SHALL create a new source record
  and submit only tasks whose identity fingerprint (R2) is not already present from the prior
  same-path source lineage, skipping tasks already submitted.
- **R10** — IF `hs submit` is invoked with both `--update` and `--repeat`, THEN the system SHALL
  reject the invocation as contradictory and exit non-zero.

### `hsx` / `hs cluster` gate matrix

- **R11** — WHEN `hsx <file>` is invoked with no gating flag, the system SHALL apply the same
  detection, refusal, and count-warning behavior as bare `hs submit` (R5–R7).
- **R12** — WHEN `hsx <file> --restart` is invoked, the system SHALL NOT refuse solely because the
  file was seen before. If the file fingerprint differs from the prior same-path source it SHALL
  alert and refuse, suggesting `--update`. If the fingerprint matches it SHALL submit only tasks
  whose identity fingerprint is not already present, relying on the existing revert-interrupted flow
  to re-run any interrupted tasks.
- **R13** — IF `hsx <file> --update` is invoked *without* `--restart` (and without `--repeat`), THEN
  the system SHALL reject the invocation as ambiguous and exit non-zero.
- **R14** — WHEN `hsx <file> --update --restart` is invoked, the system SHALL acknowledge the prior
  source and, if the fingerprint differs, create a new source and submit only tasks whose identity
  is not already present in the prior same-path source lineage (de-dup), adding only novel tasks.
- **R15** — WHEN `hsx <file> --repeat` is invoked, the system SHALL allow submission even if a
  matching prior source exists — the behavior `--restart` had in older versions — submitting all
  tasks; this MAY be combined with new tags to represent a new phase/trial.
- **R16** — IF `hsx <file> --update --repeat` is invoked, THEN the system SHALL reject the invocation
  as contradictory and exit non-zero.

### Scale & ergonomics

- **R17** — Source lookup, count checks, and de-dup SHALL be backed by appropriate indices (on
  source identity and task identity fingerprint) so that detection cost does not scale linearly with
  total task count and a bare `hs submit` on a very large table (targeting billions–trillions of
  rows on PostgreSQL/TimescaleDB) is not materially slower than today.
- **R18** — The system SHALL emit clear, helpful logging during upfront ingest and during detection
  (e.g. how many tasks were found, how many already existed, how many will be submitted, and why an
  invocation was refused).

## Non-goals (no-gos)

- **No migration/backfill** of databases created before this feature — the feature assumes a
  database initialized with `hs initdb` after the upgrade. The schema change is additive; historical
  tasks simply carry no source and are never retroactively de-duplicated.
- **No global content de-duplication** across unrelated files — de-dup is scoped to the same-path
  source lineage, not "have I ever run this command anywhere."
- **No intra-file de-duplication** — duplicate lines within a single file remain distinct tasks.
- **No change to the retry / revert-interrupted mechanism** — interrupted tasks continue to be
  re-run by the existing server revert flow; this feature only decides what to *submit*.
- **No source tracking for `in_memory` mode** — with no persistent database there is nothing to gate
  against; gating applies only to persistent SQLite/PostgreSQL.
- **No new DAG / dependency semantics** — HyperShell remains a flat many-task engine.

## Clarifications

- **Q:** Is task identity the fresh per-submit UUID, or something stable? — **A:** Stable: a
  canonical hash of **args + group + tags**, where `args` is the pre-template argument string (the
  resolved command can vary by template, so args is the meaningful identity). (resolved 2026-07-11)
- **Q:** Bare `hs submit`/`hsx` on a *changed* file at a previously-seen path (no flag)? — **A:**
  Refuse and exit, suggesting `--update`. (resolved 2026-07-11)
- **Q:** Which flag combos are valid on `hs submit` (which has no `--restart`)? — **A:** `--update`
  alone is valid; `--repeat` alone is valid; `--update`+`--repeat` is rejected. (resolved 2026-07-11)
- **Q:** How do databases predating the feature behave? — **A:** Require a fresh `hs initdb`; schema
  is additive, no backfill. (resolved 2026-07-11)
- **Assumption (confirm at plan time):** `--update` de-dup scope is the **same-path source lineage**
  for both `hs submit --update` (R9) and `hsx --update --restart` (R14). The seed text described the
  cluster case as de-dup "against existing tasks in the database"; treating both as same-path lineage
  keeps the two forms consistent.
- **Assumption (confirm at plan time):** because **tags** participate in identity (R2), changing
  only a task's tags between file versions makes it a *new* task under `--update` (it will be
  re-submitted). This is the price of letting `--repeat` re-tag a new phase; flag if unwanted.

## Related materials

- Issue: <https://github.com/hypershell/hypershell/issues/37>
- Task ingest & source of truth: `src/hypershell/submit.py` (`SubmitApp` / UUID assignment /
  `# HYPERSHELL:` tag parsing).
- Task state & identity queries: `src/hypershell/data/model.py` (`Task`/`Client` ORM, `Task.new`,
  state classmethods); schema/engine in `src/hypershell/data/core.py` (`InitDBApp`).
- Cluster entry / `--restart` wiring: `src/hypershell/cluster/__init__.py`, `src/hypershell/server.py`
  (revert-interrupted flow).
