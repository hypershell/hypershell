# META — `hs client` dials `server.host` when no `-H` is given

> **Harness feedback log** for this feature — the producer artifact of the factory's self-improvement
> loop. Written by the lifecycle skills (`hs-feature` / `hs-plan` / `hs-build` / `hs-review`) when the
> **skillset itself** costs something; read by `hs-publish` (surfaced in the PR) and applied by
> `/hs-harness`. This file is **orthogonal** to the `GOAL → PLAN → TECH → REVIEW` spine — it is about
> the *toolchain*, not the feature — and is retained on merge like the rest of `spec/{slug}/`.
>
> **Silence is the default.** The bar for a finding is one test: *was this the **skill's** fault — not
> mine, not the task's?* A merely-hard task, a self-inflicted error, or a one-off content/code issue
> (that belongs in `GOAL.md` / `REVIEW.md`) is **not** a finding. The blind `hs-review` correctness
> reviewer never reads this file — it would leak author intent.

- **slug:** client-host-default-from-bind

## F1 — Hand-written `verify:` scalars break the TECH frontmatter
`origin=hs-plan:step6 severity=low category=template status=open target=.claude/skills/hs-plan/SKILL.md`
- **What happened:** Step 6 says to copy the template and author the phases. Its `verify:` values
  are plain YAML scalars. A shell command containing `': '`, such as `grep -q 'host: str = …'`,
  made the frontmatter invalid (`mapping values are not allowed here`). The frontmatter had to be
  regenerated through `yaml.safe_dump` before `set_phase.py --touch` and `next_phase.py` would
  accept it.
- **Skill cause:** The template shows unquoted one-line `verify:` scalars, and Step 6 gives no
  serializer path. Every cycle with a non-trivial verify command hits this, or hand-quotes it.
- **Recommended fix:** In Step 6, author the frontmatter as data and emit it through the
  `_fsm.py`/`set_phase.py` dumper (for example a `set_phase.py --add-phase … --verify …` sequence,
  or a `--from-json` mode), or quote `verify:` in the template.
  `spec/contended-writes/META.md` has a related finding on the dumper's restyle diff.
- **Confidence:** high · **Effort:** small
