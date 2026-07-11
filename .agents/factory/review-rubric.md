# Review rubric — `hs-review`

The operating manual for the adversarial QA pass. The correctness reviewer runs in an **isolated
context** (fresh subagent) and grades the branch diff against `GOAL.md` + the AGENTS.md invariants
**only** — it is denied `PLAN.md`/`TECH.md` (showing the author's own rationale triggers
grading-its-own-homework and plan-sycophancy). Verification is by **executed command**, never by
assertion.

## What the reviewer sees

- ✅ `GOAL.md` (the locked contract — R-IDs)
- ✅ the branch diff (`git diff <base>...HEAD`) and the full runnable repo
- ✅ [`invariants.md`](invariants.md) (the footgun checklist) and `AGENTS.md`
- ❌ **NOT** `PLAN.md`, `TECH.md`, or `research/` (for the correctness pass)
- A **separate, later** completeness sub-pass *may* read `TECH.md` to ask "was every planned phase
  shipped? did scope balloon?" — kept isolated so the plan never contaminates the correctness verdict.

## Scope — flag ONLY

1. **Correctness bugs** — the code produces wrong behavior / crashes / data corruption.
2. **GOAL-requirement gaps** — an R-ID with no implementing change, or implemented incorrectly.
3. **AGENTS.md invariant violations** — auto-CRITICAL (see below).
4. **Scope creep** — changes that map to no R-ID (report, don't necessarily block).

**Do NOT** report style nits, speculative hardening, or "you could also…" gold-plating. A
gap-hunting reviewer manufactures gaps, which drives over-engineering. Silence on a clean diff is a
valid, valuable result.

## Refutation protocol (mandatory)

For every candidate finding, **try to disprove it first**:

1. Reproduce it — run the exact command / construct the exact input that triggers it.
2. If reproduced with observed wrong behavior → **CONFIRMED**.
3. If plausible by reading but not reproduced → **PLAUSIBLE** (needs human triage; does not auto-loop).
4. If it dissolves under scrutiny → drop it silently.

Default to dropping when uncertain. A single-model reviewer has self-preference bias even in a fresh
context, so lean on *executed evidence*, not opinion.

## Severity

| Severity | Meaning |
|---|---|
| **CRITICAL** | Data loss/corruption, security/auth weakening, or **any** AGENTS.md invariant violation (§1–§11 of `invariants.md`). |
| **HIGH** | A GOAL R-ID unmet or wrong; a real bug on a common path. |
| **MEDIUM** | A bug on an edge path; a partial/again-fragile requirement. |
| **LOW** | Minor correctness risk; missing-but-non-blocking test coverage of an R-ID. |

## Verdict & loop

- Emit findings via `ReportFindings` (most-severe first) **and** write `REVIEW.md`.
- **CONFIRMED** findings → set `TECH.md` `status: blocked` + `review.verdict: changes-requested`
  (via `set_phase.py`) and loop back to `hs-build`.
- **PLAUSIBLE** findings → surface to the human for triage, do not auto-loop.
- Clean pass → `review.verdict: approved`; proceed to `hs-publish`.
- **Bounded loop:** at most 2–3 review↔build cycles. On non-convergence, STOP and escalate to the
  human (self-correction does not reliably converge).

## Mandatory human sign-off gate

Regardless of auto-loop, a human must approve before `hs-publish` whenever a CONFIRMED finding
touches:

- the high-blast-radius core: `data/model.py`, `server.py`, `client.py`,
  `core/queue|tls|fsm|thread|signal.py`, `cluster/remote|ssh.py`; **or**
- a security / DB-lifecycle invariant (auth minimums, TLS posture, CANCEL_STATUS/exit_status
  filters, `completion_time` terminality, retry-chain UNIQUE, `in_memory` write guards).

## Optional debate variant (high-risk diffs)

For a diff touching the coupled core, run **two** independent fresh reviewers — one arguing "ship",
one arguing "block" — and reconcile. Independent instances beat single-model introspection. Reserve
for genuinely high-risk changes (cost).
