# Research digest — task-card-view

Consolidated decisions from briefs `01`–`05`. Where briefs overlapped, the single recommendation
is stated here.

## Decisions (locked for the design)

1. **rich is sufficient as-is.** Floor `rich>=13.7.1` (`pyproject.toml`), resolved `13.9.4`
   (`uv.lock`). All needed APIs (`Panel`, `Table.grid`, `Columns`, `Group`, `box`,
   `Console.size.width`) exist at the floor — **no dependency bump** ([`01`](01-rich-card-layout.md)).

2. **Outer container = `Panel`.** `Panel(body, title="task", title_align="left",
   box=box.ROUNDED, width=W, padding=(0,1))`. Chosen over a bordered `Table` because the Panel's
   **subtitle slot lands the status badge flush in the lower-right border** —
   `subtitle="status: OK", subtitle_align="right"` renders `╰… status: OK ─╯` (live-verified).
   Zero extra layout to satisfy R4's "lower-right corner" ([`01`](01-rich-card-layout.md)).

3. **Body = header grid + labeled regions.** Header: one `Table.grid(expand=True)` row with columns
   for **id · fingerprint · group · part · (reserved) zone**. Regions: `kv_grid` = `Table.grid`
   with a right-justified `bold`-label column + a folding value column; placed side-by-side via
   `Columns([...], expand=True, equal=True)` in wide/normal, **stacked** in narrow. Assemble with
   `Group(header, "", regions)` ([`01`](01-rich-card-layout.md)).

4. **Status derivation lives on the model.** Add a read-only **`Task.status_label`** property in
   `data/model.py` (per AGENTS.md §1 — state-query logic belongs on `Task`, alongside
   `revert_interrupted`/`select_failed` which use the same predicates). The **order-critical
   ladder** on a fully-loaded task ([`02`](02-status-lifecycle-derivation.md)):
   1. `schedule_time is None` → **WAITING**
   2. `completion_time is None` → **RUNNING**
   3. `exit_status == 0` → **OK**
   4. `exit_status == CANCEL_STATUS` (−1) → **CANCELLED** *(must precede all negatives; −1 also = SIGHUP death, documented collision)*
   5. `exit_status <= -1000` → **ERROR** *(never-ran sentinel range; must precede step 6)*
   6. `exit_status < 0` (−2..−64) → **KILLED** *(signal death)*
   7. `exit_status > 0` → **FAILED**
   8. else (completed but `exit_status is None`) → **UNKNOWN** *(defensive)*

   Do **not** import `TASK_TEMPLATE_ERROR`/`TASK_RESOURCE_ERROR` from `client.py` — it imports
   `data.model` (circular) and is import-heavy. Classify never-ran by the **documented
   `<= -1000` range** instead (forward-compatible with future sentinels).

5. **Label→style map lives in the presentation layer** (`task.py`, beside `select_style`). Map:
   OK→`bold green`, FAILED→`bold red`, CANCELLED→`yellow`, RUNNING→`cyan`, WAITING→`dim`,
   ERROR/KILLED/UNKNOWN→`magenta`. **CANCELLED=yellow is a deliberate card palette** that diverges
   from the existing `select_style` (CANCEL→`dim`, other-negative→`yellow`) — the GOAL fixes it, and
   the card's whole-row derivation can tell CANCELLED from a signal-kill, which `select_style`
   (exit_status-only) cannot ([`02`](02-status-lifecycle-derivation.md)).

6. **Shared renderer = `render_card(task, width, source_map=None) -> Panel` in `task.py`** (NOT
   `core/pretty_print.py` — it needs `Task`/`select_*`/`resolve_source`, which live in `task.py`;
   moving out creates an import cycle). It mirrors the field-prep block of the module-level
   `print_normal` (waited/duration/timeout → `timedelta`, memory → `format_bytes`, source →
   `resolve_source`), all helpers already imported. Callers compute width once and print each
   returned Panel ([`03`](03-output-wiring.md)).

7. **Wiring, both apps** ([`03`](03-output-wiring.md), [`05`](05-docs-completions.md)):
   - `TaskSearchApp` (`hs list`/`hs search`): add `'card'` to `output_formats` (`task.py:663`);
     dispatch is already `getattr(self, f'print_{fmt}')`, so add `print_card(self, results)`
     mirroring the static `print_normal` (build `Task` objects, batch `Source.paths_for_ids`, loop
     `console.print(render_card(...))`). Extend the subset guard in `check_output_format` (`:829`)
     from `== 'normal'` to `in ('normal','card')` — card needs `ALL_FIELDS`.
   - `TaskInfoApp` (`hs info`): add `'card'` to `output_formats` (`:172`) and branch `run()` to call
     `render_card` for the single loaded task.
   - **`TaskWaitApp` parity** (`:339`): it forwards `output_format` into `TaskInfoApp`, so add
     `'card'` there too, or `hs wait --info -f card` is spuriously rejected. Small, adjacent,
     included ([`05`](05-docs-completions.md)).

8. **Width & color** ([`04`](04-tty-width-color.md)): one bare `Console()`; read
   `console.size.width` (handles live TTY, `COLUMNS`, and the 80-col pipe fallback for free); clamp
   `W = max(60, min(160, raw))`. **Layout thresholds:** `<90` narrow (stacked regions), `90–129`
   normal, `>=130` wide (side-by-side). Piped/non-TTY → width 80 → narrow (safest). rich
   auto-strips ANSI when `not is_terminal` and honors `NO_COLOR`; `COLOR_STDOUT` (cmdkit:
   `isatty() and not NO_COLOR`) already gates the rest of `task.py`. **Non-TTY fallback = same code
   path**: borders survive as Unicode, and the status is always emitted as literal
   `status: FAILED` text (never color-only) — satisfies R6. `BrokenPipeError` is already handled;
   let it propagate.

9. **Docs/completions surface** ([`05`](05-docs-completions.md)) — add `card` to:
   help strings `task.py` `SEARCH_HELP` (`:591`) & `INFO_HELP` (`:141`) & `WAIT_HELP`; argparse
   `choices` at `:663`/`:172`/`:339`; `docs/_include/task_search_help.rst:75`,
   `task_info_help.rst:11`, `task_wait_help.rst:19`; `share/bash_completion.d/hs` (`:418` search,
   `:297` info, `:327` wait — `hsx` is a symlink, covered); `share/zsh/site-functions/_hs` (`:417`
   search, `:355` info, `:377` wait — syntax `...:format:(normal json yaml)`, append ` card`).
   **Man pages** (`share/man/man1/{hs,hsx,hyper-shell}.1`) are **generated** (`sphinx-build -b man`,
   then copy `hs.1`→`hsx.1`); regenerate at the end so they carry `card` — with a fallback if the
   diff is noisy (see PLAN risks). CI (`tests.yml`) asserts share/ *paths* only; we add values to
   existing files, so no path list changes.

## Header UX constraint (decided here)

The **id is actionable** (users copy it into `hs info <id>`), so the card **never truncates the
id**. In narrow mode the header stacks (full id on its own line); the **fingerprint** may be
abbreviated (it's a dedup hash, rarely copied). group/part/zone are short ints. Exact narrow-mode
composition is left to build.
