---
slug: task-card-view
title: Rich "card" view for tasks
kind: feature
appetite: big
status: in_progress
branch: feature/task-card-view
base: develop
current_phase: P3
last_updated: '2026-08-01'
phases:
- id: P1
  name: Derive task status from lifecycle (Task.status_label)
  status: done
  satisfies:
  - R4
  depends_on: []
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run pytest -m unit -k status_label
- id: P2
  name: Card renderer (Panel + header + regions + lower-right status + responsive
    width)
  status: done
  satisfies:
  - R2
  - R3
  - R4
  - R5
  - R6
  depends_on:
  - P1
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run pytest -m unit -k card_render
- id: P3
  name: Wire card into hs list / hs search (+ search docs & completions)
  status: pending
  satisfies:
  - R1
  - R8
  depends_on:
  - P2
  parallel: false
  hammerable: false
  hill: crest
  verify: .agents/factory/bin/temp_site.sh sh -c "seq 20 | uv run hsx -t 'echo {}'
    -N4 && uv run hs list --format=card"
- id: P4
  name: Wire card into hs info (+ hs wait parity) with info/wait docs & completions
  status: pending
  satisfies:
  - R7
  - R1
  - R8
  depends_on:
  - P3
  parallel: false
  hammerable: false
  hill: crest
  verify: uv run pytest -m unit -k card_info
- id: P5
  name: Regenerate man pages, add integration test, confirm docs build clean
  status: pending
  satisfies:
  - R8
  - R1
  depends_on:
  - P4
  parallel: false
  hammerable: false
  hill: downhill
  verify: uv run pytest -m integration -k card
review:
  last_reviewed_commit: ''
  verdict: none
  blocked_reason: ''
  cycle: 0
---
# TECH.md — Rich "card" view for tasks

The **context engine and finite-state machine** for building this feature. The YAML frontmatter is the
resume ground-truth (`uv run python .agents/factory/bin/next_phase.py spec/task-card-view/TECH.md`);
the per-phase checklists below are the work. `hs-build` executes the next actionable phase, runs its
`verify:` command, updates state via `set_phase.py`, and makes one atomic code+state commit.

- **Vision / requirements (locked):** [`GOAL.md`](GOAL.md) — R-IDs are the contract.
- **Authoritative design:** [`PLAN.md`](PLAN.md).
- **Backing research:** [`research/00-digest.md`](research/00-digest.md) + briefs `01`–`05`.

## Conventions (apply to every phase)

- Commit conventions, code style, and load-bearing invariants come from [`AGENTS.md`](../../AGENTS.md);
  the curated footgun list is [`.agents/factory/invariants.md`](../../.agents/factory/invariants.md).
- One phase per `hs-build` invocation; one atomic commit containing **both** the code and the
  `TECH.md` state change. Subject: `[feature] Build task-card-view P<n>: …`. **No `Co-Authored-By`
  trailer.**
- A CLI/feature change updates `docs/_include/*.rst` help snippets and `share/` completions **in the
  same commit** (§12). The help-string constant in `task.py` is the source of truth those mirror.
- `card` is purely additive: `normal` stays the default and `normal`/`plain`/`table`/`json`/`csv` must
  remain byte-identical in behavior.

---

## Phase P1 — Derive task status from lifecycle (`Task.status_label`)
**Satisfies:** R4 · **Depends on:** —
**Goal:** A read-only `Task.status_label` property maps a fully-loaded task to exactly one lifecycle
label, honoring the `exit_status` reserved ranges — the foundation the badge renders.

- [x] In `src/hypershell/data/model.py`, add a read-only property `Task.status_label -> str`
      implementing the **order-critical** ladder (see [`research/02`](research/02-status-lifecycle-derivation.md)):
      `schedule_time is None`→`WAITING`; elif `completion_time is None`→`RUNNING`; elif
      `exit_status == 0`→`OK`; elif `exit_status == CANCEL_STATUS`→`CANCELLED`; elif
      `exit_status is None`→`UNKNOWN`; elif `exit_status <= -1000`→`ERROR`; elif `exit_status < 0`→
      `KILLED`; else (`> 0`)→`FAILED`. Comment it as a declarative statement of the invariant it
      reproduces. (The `is None` check moved ahead of the numeric comparisons vs. the digest's
      ordering, so a completed-but-unstamped row can't raise on `None < 0`.)
- [x] Did **not** import `TASK_TEMPLATE_ERROR`/`TASK_RESOURCE_ERROR` (circular + heavy `client.py`
      import) — never-ran classified by the documented `<= -1000` range.
- [x] Added `tests/test_status_label.py` (`@mark.unit`, SPDX header): construct transient `Task`
      objects covering every branch (unscheduled; scheduled+incomplete; exit 0; `CANCEL_STATUS`;
      `-1001`/`-1002`/`-5000`; `-2`/`-9`/`-64`; `1`/`127`; completed+null-exit) and assert
      `status_label`. Module name selects under `-k status_label`.
- **Verify:** `uv run pytest -m unit -k status_label` → 8 passed.
- **Touches:** `src/hypershell/data/model.py` (high-blast-radius, **additive read-only only**),
  `tests/test_status_label.py`.

## Phase P2 — Card renderer
**Satisfies:** R2, R3, R4, R5, R6 · **Depends on:** P1
**Goal:** A pure `render_card(task, width, source_map=None) -> Panel` produces a bordered card —
horizontal id/fingerprint/group/part/(zone) header, labeled regions, lower-right `status:` badge —
that adapts across narrow/normal/wide and stays legible with no color.

- [x] In `src/hypershell/task.py`, added `STATUS_STYLES: Dict[str, str]` beside `select_style`:
      OK→`bold green`, FAILED→`bold red`, CANCELLED→`yellow`, RUNNING→`cyan`, WAITING→`dim`,
      ERROR/KILLED/UNKNOWN→`magenta`.
- [x] Added `render_card(task, width, source_map=None) -> Panel` next to `print_normal`, plus a shared
      `format_task_fields` (extracted from `print_normal`, which now delegates to it — `normal` output
      byte-identical), `card_region`, `card_width`, and `CARD_MIN_WIDTH`/`CARD_MAX_WIDTH`. Composes
      `Panel(Group(header, command, columns[, tags]), title="task", box=box.ROUNDED, width=W,
      subtitle=Text("status: <LABEL>"), subtitle_align="right", border_style=STATUS_STYLES[...])`.
      Header = `Table.grid`; regions = `Table.grid` label:value blocks (command / timing / resources /
      execution / output / retry / result / tags) arranged in a `Table.grid` of 1/2/3 columns. **Id
      never truncated** (own line below 130).
- [x] Responsive: `card_width` clamps to [60,160]; region columns = `<90` →1, `90–149` →2, `>=150` →3
      (three columns only where a full UUID does not fold mid-token); identity header goes horizontal
      at `>=130`. rich strips ANSI on a non-TTY so the literal `status:` text always survives (R6).
- [x] **AMENDMENT (adversarial review, HIGH):** region values are wrapped in `rich.text.Text` so task
      data renders literally — a bare string cell is parsed as `rich` markup, which silently stripped
      brackets (`awk '{a[b]=1}'`) and raised `MarkupError` on `sed 's/x/[/]/'`, aborting the whole
      listing. The header was already immune (`Text.assemble`). Regression test added.
- [x] Unit tests (`tests/test_card_render.py`, `@mark.unit`, `-k card_render`): render real `Task`
      objects through a width-forced `Console(file=StringIO())`; assert full id, fingerprint, region
      titles, `status: <LABEL>`; the [60,160] clamp; distinct 1/2/3-column layouts; horizontal vs
      stacked header; badge lower-right + pinned palette; tags present/omitted; no-color legibility;
      color-on-TTY; and the markup-safety regression. 24 card tests, 200 unit total.
- **Verify:** `uv run pytest -m unit -k card_render` → 24 passed (full unit suite 200 passed).
- **Touches:** `src/hypershell/task.py`, `tests/test_card_render.py`.

## Phase P3 — Wire `card` into `hs list` / `hs search`
**Satisfies:** R1, R8 · **Depends on:** P2
**Goal:** `hs list --format=card` / `hs search … --format=card` render one card per matched task,
responsive to terminal width; `normal` stays default; search help/docs/completions list `card`.

- [ ] In `TaskSearchApp` (`task.py`): add `'card'` to `output_formats` (`:663`). Add
      `print_card(self, results)` mirroring the static `print_normal` (`:783`) — build `Task` objects,
      batch `Source.paths_for_ids`, create one `Console()`, compute `W = max(60, min(160,
      console.size.width))`, loop `console.print(render_card(task, W, source_map))`. Extend the subset
      guard in `check_output_format` (`:829`) from `== 'normal'` to `in ('normal', 'card')`.
- [ ] Update the `SEARCH_HELP` `-f/--format` enumeration (`task.py:591`) to include `card`.
- [ ] Same commit (§12): `docs/_include/task_search_help.rst` (`:75`),
      `share/bash_completion.d/hs` search word list (`:418`), `share/zsh/site-functions/_hs` search
      spec (`:417`, append ` card` inside the `(…)`). (`hsx` bash file is a symlink → covered.)
- [ ] Sanity: `zsh -n share/zsh/site-functions/_hs`; confirm `--format` rejects unknown values and
      `card` requires all fields (`hs list id --format=card` → the subset error).
- **Verify:** `.agents/factory/bin/temp_site.sh sh -c "seq 20 | uv run hsx -t 'echo {}' -N4 && uv run hs list --format=card"`
  (also eyeball under `COLUMNS=70` and `COLUMNS=140`).
- **Touches:** `src/hypershell/task.py`, `docs/_include/task_search_help.rst`,
  `share/bash_completion.d/hs`, `share/zsh/site-functions/_hs`.

## Phase P4 — Wire `card` into `hs info` (+ `hs wait` parity)
**Satisfies:** R7, R1, R8 · **Depends on:** P3
**Goal:** `hs info <id> --format=card` renders one card via the shared `render_card`, identical to how
`hs list` renders that task; `hs wait --info -f card` accepted for parity; info/wait
help/docs/completions list `card`.

- [ ] In `TaskInfoApp` (`task.py`): add `'card'` to `output_formats` (`:172`) and branch `run()`
      (`:201`) so `card` computes the clamped width and prints `render_card(self.task, W)` for the
      single loaded task (source resolved directly, no batch needed).
- [ ] In `TaskWaitApp` (`task.py`): add `'card'` to its forwarded `output_formats` (`:339`) so
      `hs wait --info -f card` delegates cleanly to `TaskInfoApp`.
- [ ] Update `INFO_HELP` (`:141`) and `WAIT_HELP` `-f/--format` enumerations to include `card`.
- [ ] Same commit (§12): `docs/_include/task_info_help.rst` (`:11`), `task_wait_help.rst` (`:19`);
      `share/bash_completion.d/hs` info (`:297`) & wait (`:327`) word lists; `share/zsh/site-functions/_hs`
      info (`:355`) & wait (`:377`) specs.
- [ ] Unit/functional tests (`@mark.unit`, `-k card_info`): submit a task in a `temp_site`, then run
      `hs info <id> --format=card` via the `tests.main(argv)` helper and assert the border + `status:`
      + the id are present; assert `hs info` accepts `card` in its `choices`.
- **Verify:** `uv run pytest -m unit -k card_info`.
- **Touches:** `src/hypershell/task.py`, `docs/_include/task_info_help.rst`,
  `docs/_include/task_wait_help.rst`, `share/bash_completion.d/hs`, `share/zsh/site-functions/_hs`.

## Phase P5 — Regenerate man pages, integration test, docs build
**Satisfies:** R8, R1 · **Depends on:** P4
**Goal:** Generated assets carry `card`, the feature is proven end-to-end through the installed CLI,
and the docs build is clean.

- [ ] Regenerate man pages: `uv run sphinx-build -b man docs docs/_build/man`, copy to
      `share/man/man1/hs.1` and `hyper-shell.1`, and copy `hs.1`→`share/man/man1/hsx.1` (per the
      `/hs-release` process). **Inspect the diff:** if it's only the `card` additions (±a date line),
      keep it; if noisy/unrelated, **defer man to the next `/hs-release`** and note it in the commit
      body (completions + `docs/_include` from P3/P4 remain the hard §12 requirement).
- [ ] Add an integration test (`@mark.integration`, `-k card`): in a throwaway site submit a few
      tasks and drive `hs list --format=card` and `hs info <id> --format=card`; assert a border glyph
      and `status:` appear. (Integration tests shell out — needs `uv sync`.)
- [ ] `uv run sphinx-build docs docs/_build` — no **new** warnings (the pre-existing
      `task_submit.rst`/`manual.rst` toctree warnings are expected).
- [ ] Final sweep: grep that `card` appears in every `-f/--format` enumeration (help strings,
      `docs/_include/*`, bash, zsh, man).
- **Verify:** `uv run pytest -m integration -k card`.
- **Touches:** `share/man/man1/*.1`, `tests/…` (integration).

---

## How `hs-build` drives this

1. `next_phase.py` prints the next actionable phase (statuses authoritative; `current_phase`
   reconciled against them).
2. Pre-flight: clean tree, on `feature/task-card-view`, `develop` reachable.
3. Execute every `[ ]` in the phase (consult `PLAN.md` / `research/` for detail).
4. Run the phase's `verify:` command — never advance on a checkbox alone.
5. Amend this file if reality diverges (regenerate frontmatter with `set_phase.py`; note it in the
   commit body). STOP and escalate only on a **`GOAL.md` contradiction**.
6. Mark the phase `done`, advance `current_phase`, `--touch`; one `[feature]` commit; stop and report.
