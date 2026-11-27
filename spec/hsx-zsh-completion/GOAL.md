# GOAL — Fix Zsh completion for `hsx` / `hs cluster`

> **Origin spec.** The *what* and *why* — the locked contract `hs-review` grades against.
> The *how* lives in [`PLAN.md`](PLAN.md) and [`TECH.md`](TECH.md) (written by `hs-plan`).

- **slug:** hsx-zsh-completion
- **kind:** fix
- **appetite:** small

## Problem

Zsh tab-completion is completely broken for `hsx` (and equivalently `hs cluster`). Any attempt to
complete after `hsx`/`hs cluster` immediately aborts with:

```
_arguments:comparguments:327: invalid option definition: (1)--from-json[Read tasks from a JSON file ("FILE[@path]")]:json spec:_files
```

`_arguments` rejects the option-spec for `--from-json` in the cluster completion function before it can
offer any candidates, so the user gets an error instead of completions. This appeared around the
introduction of the new `--from-json` option. Every other completion context (e.g. `hs submit`) still
works — only the cluster context is affected, because that is the one place `--from-json` is written
with the `'(1)--from-json…'` exclusion form (`share/zsh/site-functions/_hs:562`) rather than the
grouped `'(-f --task-file --from-json)…'` form used elsewhere (`:464`–`465`). Broken completions make
the primary `hsx` entry point unpleasant to use interactively and undermine confidence in the shipped
completion assets (which the wheel installs).

## Outcome / vision

Zsh completion for `hsx` / `hs cluster` works exactly like every other subcommand: pressing TAB offers
option and argument candidates with no `_arguments`/`comparguments` error. `--from-json` completes to a
file path in the cluster context, and no previously working completion is lost.

## Acceptance criteria (the contract)

- **R1** — WHEN a user triggers Zsh completion after `hsx` or `hs cluster`, the `_hs` completion SHALL
  run to completion without raising the `_arguments: comparguments … invalid option definition` error.
- **R2** — WHEN a user completes the `--from-json` option in the `hsx` / `hs cluster` context, the
  completion SHALL offer file-path candidates for its argument (it names a JSON file).
- **R3** — The corrected cluster completion SHALL continue to offer all other cluster options and the
  positional input-file argument that it offered before the regression — no loss of existing
  candidates.
- **R4** — The fix SHALL leave the Zsh completion self-consistent with how `--from-json` is completed
  in the other (working) contexts, so the same option does not use two conflicting, one-broken spec
  forms.

## Non-goals (no-gos)

- Restructuring or rewriting the `_hs` completion file beyond what is needed to fix the cluster block.
- Touching the bash completions under `share/bash_completion.d/` (not reported broken).
- Any change to `hs`/`hsx` CLI behavior, argument parsing, or `--from-json` runtime semantics.
- Adding new completion capabilities (e.g. completing inside the `FILE[@path]` sub-syntax).

## Clarifications

No open questions — the reported symptom, the affected context, and the expected behavior are
unambiguous.

## Related materials

- `share/zsh/site-functions/_hs` — the Zsh completion. `_hs_cluster()` block:
  - `:562` — `'(1)--from-json[…]:json spec:_files'` — the reported failure point.
  - `:608` — `'(--from-json)1:input file:_files'` — the paired positional argument.
  - `:464`–`:465` — the analogous `--from-json` in the submit block using the working grouped-exclusion
    form, for comparison.
- Completion assets are shipped in the wheel via `[tool.hatch.build.targets.wheel.shared-data]`, so a
  broken `_hs` reaches installed users.
