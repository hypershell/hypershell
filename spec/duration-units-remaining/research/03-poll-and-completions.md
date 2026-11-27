# 03 — `-Q/--poll` fractions, and the completion candidates

Two small GOAL-deferred questions, researched by the planner from the source on develop.

## Should `-Q/--poll` take fractions below one second?

`server.py:156-168`: the scheduler's poll starts at `SERVER_POLL_MIN`, doubles on each empty poll and
is capped at `poll_max = poll` (`:288`); the wait itself is `dispatcher.wait_need(…, timeout=self.poll)`
(`:290`), which takes a float. So a fractional cap would work mechanically. But:

- `type=int` refuses `-Q 0.5` today (probe: `argument -Q/--poll: invalid int value: '0.5'`), so
  rounding a fraction up refuses nothing that works now and changes no value read today (R5).
- `server.poll` defaults to 30 and is documented in whole seconds; the sub-second poll that exists is
  the relay's `relay.poll` (0.1), a non-goal key.
- Every other option in R1 rounds up through `parse_time`; a separate unrounded path for one option
  is complexity the GOAL does not ask for.

**Recommended:** `-Q` uses `parse_time` like the rest (`-Q 0.5` → 1, `-Q 1m` → 60). A TOML or env
*number* (`poll = 0.5`, `HYPERSHELL_SERVER_POLL=0.5` → float) still skips `type=` and keeps today's
meaning.

## Completion candidates

zsh (`share/zsh/site-functions/_hs`) carries a description and candidate values per option; bash
(`share/bash_completion.d/hs`) carries **candidate values only** (`compgen -W "…"`, no description
field). The precedent from a7 (`-W` on `hs server`, zsh `:566`) puts the forms in the zsh description
(`(seconds, 1h30m, or 1.5h)`); a7 left bash unchanged.

`-d` offers `30 60 600 -60 -600` in both. A candidate is completed as a separate word after `-d `,
and the two-word `-d -10m` fails on Python 3.11–3.13 (01). Bare negative numbers (`-60`, `-600`)
work as separate words on every version.

**Recommended:**

- zsh: each in-scope option's description states the forms in a7's short style (`seconds, 30s, 5m,
  1h30m, or a Slurm time`); `-d`'s also states the random form written attached
  (`--delay-start=-10m`). Keep `[`, `]` and `:` out of the description text: in an `_arguments` spec
  `]` ends the description and `:` separates the message and action fields (a7 avoided both).
- Candidates (zsh and bash): keep today's bare numbers and add a few unit forms (e.g. `-w`: `5 30s 1m
  10m`; `-P`: `30 60 5m 10m`; `-d`: `30 60 10m -60 -600`). Never offer a signed unit form
  (`-10m`) as a candidate: it completes as a separate word.
- bash: the candidate list is the only place it can "describe" forms, so R7 for bash is met by the
  unit-form candidates. A foreman decision (PLAN §2, D6).
- Python 3.11 note for the help: write the random form attached everywhere (R7).
