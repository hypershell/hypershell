# PLAN — Expose a per-executor TASK_SLOT to tasks

> **Status:** Draft for review · **Last updated:** 2026-07-19
> **Authoritative technical design.** The *how*. Vision/contract is [`GOAL.md`](GOAL.md);
> the phased executable roadmap is [`TECH.md`](TECH.md). Every design element traces to a GOAL R-ID.

## 1. Summary

Publish the executing task's **0-based slot** (`TASK_SLOT`) and the client's **executor count**
(`TASK_SLOT_COUNT`) to every task, so resource-sensitive workloads can pin a non-colliding slice of
the node. The entire change lives in `src/hypershell/client.py`: thread the slot/count onto the
`TaskExecutor`, add the two env vars in `task_env()`, and feed a `context={'slot', 'slot_count'}`
to the **existing** client-side `Template.expand` call for the `{slot}`/`{slot_count}` placeholders.
No change to `core/template.py`, `server.py`, or any CLI flag — a genuinely small slice that fits
the appetite.

## 2. Design

**The slot already exists at the construction site.** `ClientThread.__init__`
(`client.py:1215`) builds executors with `TaskThread(id=count+1, …) for count in range(num_threads)`
— so `count` is already the 0-based index `0..N-1` and `num_threads` is `N`. We surface both:

- **`TaskExecutor` / `TaskThread` (`client.py:616,911`)** — add one constructor param
  `slot_count: int = 1` (stored as `self.slot_count`), and expose the 0-based index as a property
  `slot = self.id - 1` (ids are exactly `1..N` and immutable, so `slot` is `0..N-1` and constant for
  the executor's lifetime). `ClientThread` passes `slot_count=num_threads` to each `TaskThread`.
  *(No new `slot` param — it is definitionally `id - 1`; deriving keeps the constructor churn to a
  single argument.)*
- **Env vars — `task_env()` (`client.py:439`)** — widen the signature to
  `task_env(task, slot: int = 0, slot_count: int = 1)` and add `'TASK_SLOT': str(slot)` and
  `'TASK_SLOT_COUNT': str(slot_count)` to the returned dict. The single call site
  (`client.py:743`, inside `TaskExecutor.start_task`) becomes `task_env(self.task, self.slot,
  self.slot_count)`. Defaults (`0`/`1`) make the vars always-defined for any caller (R5).
- **Template placeholders — `TaskExecutor.create_task` (`client.py:711`)** — the run-time expansion
  `self.template.expand(self.task.args)` gains
  `context={'slot': self.slot, 'slot_count': self.slot_count}`. `Template.expand(args, context=…)`
  already resolves named `{key}` from `context` (`template.py:90,109-110`) via `render_value`
  (ints → `'0'`, `'4'`), *after* the built-in simple/complex patterns — so `{slot}`/`{slot_count}`
  add no new syntax and cannot collide with `{}`/`{N}`/named-tag expansion. **`template.py` is
  untouched.**

**Reach of the placeholder (the one subtlety, per GOAL).** `server.py:819` sets
`submit_template = template if from_json else DEFAULT_TEMPLATE`. So in **DB-backed mode (the default,
`in_memory=False`) the client expands the real template at run time** → `{slot}` resolves. In
`--no-db`/JSON mode the template is expanded submit-side (slot unknown there) and the client runs
`DEFAULT_TEMPLATE` → the placeholder is unavailable, but **`$TASK_SLOT` (env var) works in every
mode**. Documented, not worked around (keeps us inside invariant §11).

### Requirement → design map

| R-ID | Design element(s) that satisfy it |
|------|-----------------------------------|
| R1 | Executors built over `range(num_threads)`; `slot = id-1` yields distinct `0..N-1`, each once. |
| R2 | `task_env(task, slot, …)` sets `TASK_SLOT=str(slot)`; called with `self.slot` at `client.py:743`. |
| R3 | Same `task_env` sets `TASK_SLOT_COUNT=str(slot_count)`; `slot_count=num_threads` threaded from `ClientThread`. |
| R4 | `create_task` passes `context={'slot','slot_count'}` to the existing `Template.expand` (client-side, run time). |
| R5 | `slot=0, slot_count=1` defaults on `task_env` and constructors; `N≥1` ⇒ single executor `id=1`→`slot 0`, count `1`. |
| R6 | `slot` derives from immutable `self.id` fixed at construction → constant across every task the executor runs. |
| R7 | `docs/getting_started.rst` (env vars) + `docs/templates.rst` (placeholders) + a worked pinning example; man pages regenerated iff `manual.rst` is affected. |

## 3. Invariant gate (AGENTS.md constitution check)

Checked against [`invariants.md`](../../.agents/factory/invariants.md) before research and again
after this design.

- **§5 Concurrency (FSM + Thread)** — we add two constructor params, a read-only `slot` property,
  and one `context` dict read inside existing states. No change to `TaskState`, `actions`, `HALT`,
  the `stop()`→`machine.halt()` override, signal polling, or blocking timeouts. Honored.
- **§7 Resource accounting** — `slot` is an identity, not a counter; `acquire`/`release` under
  `executor_lock` are untouched and remain balanced. Honored.
- **§11 Cluster orchestration** — **no launched-client argv change** (no new flag; slot/count are
  derived internally from the existing `-N`), so the "replicate across local/remote/ssh/autoscale"
  rule does not trigger. The JSON-mode `DEFAULT_TEMPLATE` rule is *honored* (placeholder resolves
  only where the client expands the template; env var covers JSON mode). Honored.
- **§2 exit_status** — no new sentinels. A `{slot}` used where no slot context exists (submit-side /
  `--no-db`) raises `Template.UnmatchedPattern`, routed through the existing template-error path
  (`TASK_TEMPLATE_ERROR` on the client). Honored.
- **§1 Task lifecycle** — no query or nullable-column predicate touched. Honored.
- **§12 Conventions** — docs updated in the same commit; no CLI flag ⇒ `docs/_include/` help
  snippets and `share/` completions are unaffected; new tests tagged `unit`/`integration`; version
  single-sourced/untouched; concise Python (verified `render_value`/`str` equivalence for ints).

### Deviation justifications

| Deviation | Why needed | Simpler alternative rejected because |
|-----------|-----------|--------------------------------------|
| —         | —         | — |

## 4. Rabbit holes (resolved)

- **Where does the slot enter without double-expansion?** → the client-side `Template.expand`
  (`client.py:711`) via the pre-existing `context=` mechanism; `server.py:819` confirms DB mode
  expands client-side (placeholder available) and JSON mode expands submit-side (env var only). No
  `template.py` change, minimal blast radius. *(Small appetite: no research fan-out; settled by
  direct reads of `client.py`, `core/template.py`, `server.py`.)*

## 5. Risks & open questions

- **Placeholder is DB-mode-only** (unavailable under `--no-db`/JSON, where the template is
  pre-expanded submit-side). *Mitigation:* `$TASK_SLOT`/`$TASK_SLOT_COUNT` env vars work in all
  modes; document both, lead with the env var as the universal mechanism. Inherent to §11 — not a
  deviation.
- **Integration determinism.** Executor→task assignment is nondeterministic, so tests assert the
  **set** of slots observed over enough tasks equals `{0..N-1}` (not a per-task mapping).
- **Man-page scope.** `share/man/man1/*.1` regenerate from `docs/manual.rst` via `sphinx-build -b
  man`; only regenerate if `manual.rst` actually renders the new sections (getting_started/templates
  may not feed the man page). Verify in P3; if unaffected, `share/` stays unchanged.
- **`client.py` has no dedicated test file.** P2 adds `tests/test_client.py` (first one) for
  `task_env`, keeping the highest-risk edit covered.

## 6. Verification strategy

Drive the real CLI in a throwaway site (DB-backed by default, so placeholders resolve client-side):

- **Placeholder + distinctness (R1/R3/R4):**
  `.agents/factory/bin/temp_site.sh sh -c "seq 40 | uv run hsx -N4 -t 'echo {slot}' 2>/dev/null | sort -un"`
  → expect `0 1 2 3`.
- **Env var, all modes (R2):** `… -t 'printenv TASK_SLOT' … | sort -un` → `0 1 2 3`
  (`printenv` sidesteps outer-shell `$` quoting).
- **Single-executor degenerate (R5):**
  `… -N1 -t 'echo {slot}/{slot_count}' …` → every line `0/1`.
- **Automated (P2):** unit tests for `Template.expand(context=…)` and `task_env(...)` (incl.
  defaults), plus one `integration` cluster test asserting the slot set. `uv run pytest -v -k slot`.
- **Docs (P3):** `uv run sphinx-build docs docs/_build` clean (modulo known toctree warnings) and
  `TASK_SLOT` present in `getting_started.rst`/`templates.rst`.

---

*Small appetite — no `research/` fan-out; design settled by targeted source reads.*
