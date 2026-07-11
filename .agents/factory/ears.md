# EARS — Easy Approach to Requirements Syntax

A lightweight controlled-natural-language convention for writing acceptance criteria that are
**testable and low-ambiguity**. Used by `hs-feature` to shape `GOAL.md` acceptance criteria (R-IDs).

**Nudge, don't hard-enforce.** EARS reduces ambiguity; it does not eliminate it, and forcing it onto
genuinely exploratory or ubiquitous requirements stilts them. Prefer EARS where it clarifies; fall
back to plain, unambiguous prose where EARS would be contrived. Every criterion still gets a stable
R-ID.

## Generic template

> **While** \<optional precondition/state>, **when** \<optional trigger>, the \<component> **shall**
> \<observable response>.

Keep the `<component>` a real HyperShell part (`hs submit`, the `Scheduler`, the client executor,
the `Task` model, the queue) and the `<response>` **observable** (an exit status, a DB row state, a
log line, a printed message) so `hs-review` can check it by driving the CLI.

## The six patterns

| Pattern | Keyword | Form |
|---|---|---|
| **Ubiquitous** | *(none)* | The \<component> shall \<response>. |
| **State-driven** | `While` | While \<state>, the \<component> shall \<response>. |
| **Event-driven** | `When` | When \<trigger>, the \<component> shall \<response>. |
| **Optional-feature** | `Where` | Where \<feature is included>, the \<component> shall \<response>. |
| **Unwanted-behavior** | `If … Then` | If \<unwanted condition>, then the \<component> shall \<response>. |
| **Complex** | combo | While \<state>, when \<trigger>, the \<component> shall \<response>. |

## HyperShell-flavored examples

- **R1 (event):** *When* `hs submit <file>` is run and the file's fingerprint matches an existing
  source, the submit command *shall* refuse and exit non-zero with a message naming the prior
  source.
- **R2 (unwanted):** *If* `--update` is passed without `--restart` or `--repeat` to `hsx`, *then* the
  cluster command *shall* reject the invocation as ambiguous and exit non-zero.
- **R3 (state):** *While* running in `--restart` mode, the submitter *shall* submit only tasks whose
  fingerprints are absent from the database for the matched source.
- **R4 (ubiquitous):** The source-detection query *shall* add no more than one indexed lookup to a
  submit on a billion-row task table.

## Anti-patterns

- Untestable adjectives ("fast", "robust", "user-friendly") — replace with an observable threshold.
- Multiple requirements in one line — split so each has its own R-ID and pass/fail.
- Specifying the *how* (implementation) in a criterion — that belongs in `PLAN.md`.
