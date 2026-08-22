---
status: unshaped | shaped | adopted:{slug} | declined | accepted-behaviour
kind: feature | fix | refactor | docs
appetite: small | big
lane: public
---

# {Title}

> **Candidate, not a contract.** This file records deferred work in enough detail that a future
> session does not have to re-derive it. It is **not** graded by `hs-review` and must never be
> copied into `spec/{slug}/GOAL.md` verbatim — `/hs-feature` promotes it, and that is where
> appetite, non-goals and the R-IDs get negotiated with a human. The `status:` field above is the
> guard.
>
> Deliberately **not** named `GOAL-{slug}.md`: every other GOAL in the factory is a locked
> contract, so a file carrying that name eventually gets treated as one.
>
> Body sections mirror [`GOAL.md`](../.agents/factory/templates/GOAL.md) so promotion is a
> move-and-fill rather than a rewrite. Links below are written relative to `issues/{slug}.md`, where
> the filled-in copy lives — not to this template.

## Status vocabulary

| `status:` | Means | What `/hs-feature` does with it |
|---|---|---|
| `unshaped` | A raw deferral. Evidence captured; appetite, non-goals and R-IDs are **not** agreed. | Full shaping conversation before a GOAL exists. |
| `shaped` | Already negotiated with a human — dated clarifications, agreed appetite, non-goals, R-IDs — but **not yet accepted into a cycle**. | Re-confirm scope against current `develop`, then adopt largely as written. |
| `adopted:{slug}` | Promoted; `spec/{slug}/` now owns it. | Nothing. The seed and its ROADMAP entry stay while the cycle is in flight, because it may bounce or be abandoned; `/hs-roadmap` deletes both once the branch lands on `develop`. |
| `declined` | Considered and **not** taken on as debt, with the reasoning and what would change the answer. | Nothing — it is not a candidate. |
| `accepted-behaviour` | Reported as a defect, judged **intended**. Exists so it is not re-filed. | Nothing — but read it before 'fixing' the behaviour. |

The last two are terminal records, not queue entries: `ROADMAP.md` lists them under *Settled
questions*, apart from the ordered Part I / Part II cycles, so the index stays an index of *work*.
A terminal record keeps its file, because nothing else in the repository records that the question
was ever asked. The hidden lane's counterpart heading is *Not scheduled* in `.security/ROADMAP.md`.

`shaped` is not a shortcut past the human gate. It records that the *shaping* happened, not that
the work was accepted; acceptance is still `/hs-feature` creating the branch and the GOAL.

`kind:` is the `AGENTS.md` commit category the work would land under — commonly `feature`, `fix`,
`refactor` or `docs`. That set is **not closed**; coin a new lowercase category when one genuinely
fits. At promotion, `kind: fix` branches as `fix/{slug}` and every other kind branches as
`feature/{slug}`.

`lane: public` lives in `issues/`. Security-sensitive deferrals use `lane: security` and live in
`.security/issues/` — see the deferral table in [`AGENTS.md`](../AGENTS.md).

Two optional keys are in use where they earn their place: `shaped-as:` on a `shaped` seed records
the GOAL slug the shaping conversation used, and `findings:` on a `lane: security` seed lists the
`.security/findings/F*.md` ids it covers. Neither is required.

## Problem

<What is wrong today, for whom, and why it matters. Include the evidence the finder had at hand —
`file:line`, the mechanism, the observed behaviour. This is the expensive part of a deferral and
the part a one-line roadmap seed throws away.>

## Why it was deferred

<Why it was not safe or sensible to fix in the pass that found it: scope, blast radius, a GOAL
non-goal, an appetite boundary, or a dependency on other work. Say plainly whether it is
**pre-existing** (present in `develop`) or introduced by that pass — a reviewer will ask.>

## Outcome / vision

<What "good" looks like when this is fixed.>

## Sketch of the acceptance criteria

Draft R-IDs, to be firmed up at promotion. Prefer EARS phrasing (see
[`.agents/factory/ears.md`](../.agents/factory/ears.md)).

- **R1** — WHEN <trigger>, the <component> SHALL <observable response>.

## Notes

- Related: <other `issues/` files, `spec/{slug}/` records, or GitHub issues>
- Found by: <slug + phase, e.g. `task-slot` P3 — or an out-of-cycle review>
