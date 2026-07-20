# GOAL — Expose a per-executor TASK_SLOT to tasks

> **Origin spec.** The *what* and *why* — the locked contract `hs-review` grades against.
> The *how* lives in [`PLAN.md`](PLAN.md) and [`TECH.md`](TECH.md) (written by `hs-plan`).
> Keep this at the right altitude: solved and bounded, but not over-specified — leave design
> freedom for the plan. Edit requirements here; do **not** silently drift them during build.

- **slug:** task-slot
- **kind:** feature
- **appetite:** small  ·  *Small body of work (two env vars, two template placeholders, a
  re-based counter, docs). The one spot needing genuine design care is how the placeholder
  interacts with the existing template/tag expansion — see Clarifications.*

## Problem

HyperShell targets embarrassingly-parallel HPC workloads, and the heaviest of those are
**resource-sensitive**: CPU- and GPU-bound simulations that must not oversubscribe a node. When
a client runs `N` executors concurrently on one machine, every task competes for the same cores,
memory controllers, and accelerators. To behave well, such codes need to *pin* themselves to a
non-overlapping slice of the node — set `OMP_NUM_THREADS`/`OMP_PLACES`/`OMP_PROC_BIND`, wrap in
`taskset -c <cores>` / `numactl --interleave=<nodes>`, or select a device with
`CUDA_VISIBLE_DEVICES` (and the equivalents for ROCm/oneAPI). Newer accelerator frameworks
(OpenXLA/JAX and friends) rely on exactly this kind of process-level affinity.

The blocker is simple: **a task has no way to know which of its client's concurrent slots it is
occupying.** Without a stable "you are executor *k* of *N* on this node" signal, a user cannot
compute a slice that is guaranteed not to collide with the sibling tasks running beside it.
Everything else (cores-per-slot, GPU ordinal, NUMA node) is derivable arithmetic *once that index
exists* — so the missing primitive is the index itself.

## Outcome / vision

Every task can discover its slot. HyperShell publishes a **0-based executor index** (`TASK_SLOT`)
and the **executor count** (`TASK_SLOT_COUNT`) both as environment variables (alongside the
existing `TASK_*` family in `task_env()`) and as command-template placeholders resolved at client
run time. A user pins resources with no HyperShell code change — e.g.
`CUDA_VISIBLE_DEVICES=$TASK_SLOT`, or a thin wrapper that computes `cores_per_slot =
ncores / TASK_SLOT_COUNT` and calls `taskset -c` — confident that concurrent siblings on the same
node receive distinct, stable slots. It is documented with at least one worked pinning example.

This deliberately stops at *exposing the primitive*. Turnkey pinning (HyperShell setting
`OMP_*`/`taskset`/`CUDA_VISIBLE_DEVICES` itself, with resource-count awareness) is a separate,
larger follow-up that builds on this signal — see Non-goals.

## Acceptance criteria (the contract)

- **R1** — The client SHALL assign each of its `N` task executors a distinct slot index in the
  range `0..N-1` (numbered from 0), covering every value exactly once.
- **R2** — WHEN a task subprocess is launched, the client SHALL set `TASK_SLOT` in the subprocess
  environment (via `task_env()`) to the launching executor's slot index, as a decimal, 0-based
  integer string.
- **R3** — WHEN a task subprocess is launched, the client SHALL set `TASK_SLOT_COUNT` (proposed
  name; final name fixed in `TECH.md`) in the subprocess environment to `N`, the number of
  executors on that client.
- **R4** — WHEN a task's command template is expanded at client run time, the engine SHALL make
  the slot index and the slot count available as template placeholders (proposed `{slot}` /
  `{slot_count}`), substituting the launching executor's values.
- **R5** — The engine SHALL ensure `TASK_SLOT` and `TASK_SLOT_COUNT` are always defined for any
  executed task; in a single-executor execution context `TASK_SLOT` SHALL be `0` and
  `TASK_SLOT_COUNT` SHALL be `1`.
- **R6** — WHILE a client is running, a given executor's slot index SHALL remain constant across
  every task that executor runs (stable for the executor's lifetime), so a resource pin stays put.
- **R7** — The documentation SHALL describe `TASK_SLOT`, `TASK_SLOT_COUNT`, and the template
  placeholders in the task-environment reference (and update the affected `docs/_include/*.rst`
  help snippets and `share/` man pages), including at least one worked resource-pinning example
  (e.g. `taskset` / `OMP_*` / `CUDA_VISIBLE_DEVICES`).

## Non-goals (no-gos)

- **Turnkey pinning.** HyperShell will not itself set `OMP_NUM_THREADS`/`OMP_PLACES`/
  `OMP_PROC_BIND`, invoke `taskset`/`numactl`, or set `CUDA_VISIBLE_DEVICES`. It exposes the slot;
  the user (or a wrapper) does the pinning. Automatic pinning is the deferred follow-up feature.
- **Resource-count awareness.** HyperShell will not detect the node's core/GPU/NUMA topology, nor
  validate that `N` fits the hardware. Turning a slot into a concrete slice is the user's math.
- **A cluster-global rank.** `TASK_SLOT` is **per client** — two clients each number their
  executors `0..N-1`. No cross-node globally-unique id/rank is introduced (resource pinning is a
  node-local concern, so per-client is sufficient).
- **Changing executor concurrency / `-N` semantics.** The number and lifecycle of executors is
  unchanged; this feature only surfaces an index into the existing set.
- **1-based numbering.** Unlike GNU Parallel's 1-based `{%}` job slot, `TASK_SLOT` is 0-based so it
  maps directly onto core indices and CUDA device ordinals.

## Clarifications

- **Q:** Env var only, or also a command-template placeholder? — **A:** Both — an environment
  variable set in `task_env()` *and* a run-time command-template placeholder (resolved 2026-07-19).
- **Q:** Also expose the executor count, not just the index? — **A:** Yes — a companion count
  variable/placeholder (`TASK_SLOT_COUNT` proposed) so a portable wrapper can compute an even,
  non-overlapping slice without hardcoding `-N` (resolved 2026-07-19).
- **Note (for `hs-plan`):** the slot should derive from the executor's existing per-thread
  identifier, **re-based to start at 0** (the current number does not necessarily start at 0).
- **Note (for `hs-plan`):** the one design-care point despite the small appetite is the
  **template placeholder** — the exact syntax (`{slot}`/`{slot_count}` are proposals) and its
  interaction with the existing `{}` / `{N}` / named-tag expansion path (where
  `TASK_TEMPLATE_ERROR` originates) is a `TECH.md` decision. Whatever the syntax, the requirement
  is that a per-executor slot/count value is substitutable at run time. Recall the JSON-mode rule
  that clients are sent `DEFAULT_TEMPLATE` (not the user template) to avoid double expansion —
  confirm where the slot enters expansion accordingly.

## Related materials

- Source (likely touchpoints, to be confirmed by `hs-plan`): `src/hypershell/client.py`
  (`TaskExecutor` / `task_env()` — where the `TASK_*` env family and the per-executor thread id
  live), `src/hypershell/core/template.py`, `src/hypershell/core/tag.py`.
- Docs & assets: task/template environment reference under `docs/`, `docs/_include/*.rst` help
  snippets, and man pages under `share/`.
- Prior art / motivation: GNU Parallel's job-slot `{%}` (1-based) is the closest analog; the wider
  motivation is resource-sensitive HPC pinning (`OMP_*`, `taskset`/`numactl`,
  `CUDA_VISIBLE_DEVICES`, OpenXLA/JAX affinity). Identified as a gap for resource-sensitive
  workflows; a turnkey-pinning feature will build on this primitive.
