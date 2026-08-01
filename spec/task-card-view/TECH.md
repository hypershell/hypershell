---
slug: task-card-view
title: Rich "card" view for tasks
kind: feature
appetite: big
status: blocked
branch: feature/task-card-view
base: develop
current_phase: done
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
  status: done
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
  status: done
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
  status: done
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
  last_reviewed_commit: dbfe83b93ab04c59cdabda8bec987eb1d22bef75
  verdict: changes-requested
  blocked_reason: R5 min-60 width clamp inert below 60 cols (card call sites use bare
    Console); id truncated
  cycle: 2
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

- [x] In `TaskSearchApp` (`task.py`): added `'card'` to `output_formats` (after `normal`). Added the
      static `print_card(results)` mirroring `print_normal` — build `Task` objects, batch
      `Source.paths_for_ids`, one `Console()`, `card_width(console.size.width)`, loop
      `console.print(render_card(task, W, source_map))` with a blank line between cards. Extended the
      subset guard in `check_output_format` from `== 'normal'` to `in ('normal', 'card')` (message now
      interpolates the format).
- [x] Updated the `SEARCH_HELP` `-f/--format` enumeration to `(normal, card, plain, table, csv, json)`.
- [x] Same commit (§12): `docs/_include/task_search_help.rst` (added `card` to the list + a prose
      sentence), `share/bash_completion.d/hs` search word list (`normal card plain table csv json`),
      `share/zsh/site-functions/_hs` search spec (`:format:(normal card plain table csv json)`).
      (`hsx` bash file is a symlink → covered.)
- [x] Sanity: `zsh -n share/zsh/site-functions/_hs` OK; module import OK; `hs list id --format=card`
      → `Cannot use --format=card with subset of field names`.
- **Verify:** drove `hs list --format=card` in a `temp_site` (20 echo tasks, `-N4`): **20 cards, all
      `status: OK`**; subset rejected; `normal` still the default (0 cards without `--format`);
      responsive confirmed live at `COLUMNS=70`(1-col) / `100`(2-col) / `155`(3-col + horizontal
      header). Full unit suite 200 passed.
- **Touches:** `src/hypershell/task.py`, `docs/_include/task_search_help.rst`,
  `share/bash_completion.d/hs`, `share/zsh/site-functions/_hs`.

## Phase P4 — Wire `card` into `hs info` (+ `hs wait` parity)
**Satisfies:** R7, R1, R8 · **Depends on:** P3
**Goal:** `hs info <id> --format=card` renders one card via the shared `render_card`, identical to how
`hs list` renders that task; `hs wait --info -f card` accepted for parity; info/wait
help/docs/completions list `card`.

- [x] In `TaskInfoApp` (`task.py`): added `'card'` to `output_formats` and a `run()` branch that
      creates one `Console()` and prints `render_card(self.task, card_width(console.size.width))` for
      the single loaded task (source resolved directly via the `source_map=None` path — no batch).
- [x] In `TaskWaitApp` (`task.py`): added `'card'` to its forwarded `output_formats` so
      `hs wait --info -f card` delegates cleanly to `TaskInfoApp` (which now handles it).
- [x] Updated `INFO_HELP` and `WAIT_HELP` `-f/--format` enumerations to `([normal], card, json, yaml)`.
- [x] Same commit (§12): `docs/_include/task_info_help.rst` (+ prose), `task_wait_help.rst`;
      `share/bash_completion.d/hs` info & wait word lists (`normal card json yaml`);
      `share/zsh/site-functions/_hs` info & wait specs (`:format:(normal card json yaml)`).
- [x] Functional tests (`tests/test_card_info.py`, `@mark.unit`, `-k card_info`): `card` is a choice
      for both info and wait; `hs info <id> --format=card` on a submitted task renders exactly one
      card with the full id and `status: WAITING` (the honest unscheduled state, via `tests.main`);
      `normal` remains the info default. The **completed-task `OK` card and `hs wait --info -f card`**
      (which blocks until completion) are pinned by the **P5 integration** test — verified manually
      here: a run-to-completion task renders `status: OK` via both `hs info` and `hs wait --info`.
- **Verify:** `uv run pytest -m unit -k card_info` → 3 passed (full unit suite 203 passed).
- **Touches:** `src/hypershell/task.py`, `docs/_include/task_info_help.rst`,
  `docs/_include/task_wait_help.rst`, `share/bash_completion.d/hs`, `share/zsh/site-functions/_hs`,
  `tests/test_card_info.py`.

## Phase P5 — Regenerate man pages, integration test, docs build
**Satisfies:** R8, R1 · **Depends on:** P4
**Goal:** Generated assets carry `card`, the feature is proven end-to-end through the installed CLI,
and the docs build is clean.

- [x] Man regeneration **DEFERRED to the next `/hs-release`** (per the plan's noisy-diff fallback). A
      trial `uv run sphinx-build -b man docs docs/_build/man` produced a diff carrying **unrelated**
      content — the already-merged `--part`/`--rotate` (part-tag-to-column) doc changes the committed
      man pages predate, plus a today `.TH` date bump — not just `card`. Bundling that into this
      feature commit would muddy the diff; man is a release-time generated aggregate, so the man pages
      were reverted to their committed state. R8's hard requirement (bash/zsh completions +
      `docs/_include`) is fully met by P3/P4; the man `card` lines land at the next release alongside
      the pending `--part` ones.
- [x] Added `tests/test_card_integration.py` (`@mark.integration`, `-k card`): runs a file-mode
      cluster to completion, then drives `hs list --format=card` (asserts **2 cards, 2× `status: OK`**),
      `hs info <id> --format=card` (one card, full id, `status: OK`), and `hs wait <id> --info -f card`
      (`status: OK`).
- [x] `uv run sphinx-build docs docs/_build` — no **new** warnings (only the pre-existing
      `task_submit.rst`/`manual.rst` toctree + `pkg_resources` deprecation warnings).
- [x] Final sweep: `card` present in `task.py` help strings, all three `docs/_include/*`, bash (search
      + info + wait), and zsh (search + info + wait). (Man deferred as above.)
- **Verify:** `uv run pytest -m integration -k card` → 1 passed (full unit suite 203; all `-k card`
      tests 27 passed).
- **Touches:** `tests/test_card_integration.py`. (Man pages intentionally **not** touched — deferred.)

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
