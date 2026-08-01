# Research: `rich` building blocks for the `card` output format

## 1. Pinned version

- **Floor:** `rich>=13.7.1` (`pyproject.toml:35`).
- **Resolved:** `rich==13.9.4` (`uv.lock:1333-1334`).

Every API below is verified against 13.9.4 in this repo's venv. All of it also exists in the
13.7.1 floor, so no floor bump is needed. `rich` has no `__version__` attr — use
`importlib.metadata.version("rich")` if you ever need it at runtime (we don't).

## 2. How the repo already uses `rich`

Imports are narrow and consistent — mirror them:

- `src/hypershell/task.py:26-28` — `from rich.console import Console`, `from rich.syntax import
  Syntax`, `from rich.table import Table`.
- `src/hypershell/config.py:25-26` — `Console`, `Syntax`.
- Render idiom throughout is `Console().print(<renderable>)` (fresh `Console()` per call, no
  shared singleton): `task.py:781` (`print_table`), `:806` (JSON via `Syntax`), `:218/:229`
  (`hs info` yaml/json). **Follow this — instantiate `Console()` at the call site.**
- `print_table` (`task.py:772-781`) is the closest existing template: `Table(title=None)`,
  `add_column(name)` per field, `add_row(*row, style=select_style(...))`, then print.
- **Theme:** `config.console.theme` (default `'monokai'`, `core/config.py:183-184`) is only ever
  passed to `Syntax(...)` for code/JSON/YAML highlighting. It is **not** a rich `Theme` object
  and does **not** apply to Table/Panel styling — don't try to feed it to the card.
- **Color gating:** `COLOR_STDOUT` (from `cmdkit.ansi`, `task.py:31`) gates ANSI. The
  table/plain paths colorize via `select_style`/`select_color` keyed on `exit_status`
  (`task.py:1335-1360`): `None`→plain, `0`→green/`'green'`, `CANCEL_STATUS`→faint/`'dim'`,
  other-negative→yellow, positive→red. **Reuse `select_style` for the card's border/status
  style** so the new view stays visually consistent (rich style *names*, not the ANSI callables).
- `core/pretty_print.py` does **not** import rich (custom ANSI dict-printer) — irrelevant here.
- Field set + logical grouping to reproduce: `NORMAL_MODE_TEMPLATE` (`task.py:1294-1320`) and
  `print_normal` (`task.py:1382-1400`), which already formats every value (`format_json`,
  timedeltas, `format_bytes`, `resolve_source`, tags). The card should consume the same
  pre-formatted `task_data` dict, not re-derive it.

Note: there is **no existing status-string derivation** (`unscheduled`/`running`/`done`/
`cancelled`); the virtual `status:` badge value must be computed by the build from the
nullable-column lifecycle contract (see AGENTS.md). That is a build concern, not a rich one.

## 3. Recommended building blocks (verified, buildable)

Import surface to add: `from rich.panel import Panel`, `from rich.columns import Columns`,
`from rich.console import Group`, `from rich import box` (keep existing `Table`/`Console`).

### Outer container → `Panel`

`Panel` is the right choice over a bordered `Table` because it gives title **and** subtitle in
the border for free — the subtitle is exactly the lower-right badge slot.

```python
Panel(body, title="task", title_align="left",
      subtitle=status_text, subtitle_align="right",   # <-- lower-right badge, in bottom border
      box=box.ROUNDED, width=w, padding=(0, 1), border_style=select_style(task.exit_status) or "none")
```

- **Box:** `box.ROUNDED` (clean default). `box.SQUARE`/`box.HEAVY`/`box.MINIMAL` all available if
  a heavier look is wanted. Verified `ROUNDED/SQUARE/HEAVY/MINIMAL/HEAVY_HEAD` all present.
- **Title placement:** `title="task"`, `title_align="left"`.

### Horizontal identity header → `Table.grid(expand=True)`

Recommended over `Columns`/tab-stops: `Table.grid` gives explicit, ratio-sized columns that
fill the width and wrap predictably.

```python
header = Table.grid(expand=True)
for _ in range(5):                       # id, fingerprint, group, part, + reserved zone slot
    header.add_column(justify="left", ratio=1)
header.add_row(f"id: {id}", f"fp: {fp}", f"group: {group}", f"part: {part}", f"zone: {zone}")
```

`Table.grid(*headers, padding=0, expand=False)` is a classmethod (verified sig). Pass
`expand=True` so it fills the panel interior. The reserved `zone` cell can render `-`/empty
until the field lands.

### Two labeled regions (label+value pairs) → `Table.grid` per region, side-by-side via `Columns`

```python
def kv_grid(pairs):
    g = Table.grid(padding=(0, 1))
    g.add_column(justify="right", style="bold cyan", no_wrap=True)   # labels
    g.add_column(justify="left", overflow="fold")                    # values (fold long cmds)
    for k, v in pairs:
        g.add_row(f"{k}:", str(v))
    return g

regions = Columns([kv_grid(left_pairs), kv_grid(right_pairs)], expand=True, equal=True)
```

Group the `NORMAL_MODE_TEMPLATE` fields into ~2-3 logical blocks (identity already in header;
e.g. *execution* = command/args/cores/memory/timeout/exit_status; *timeline* = submit/schedule/
start/complete/waited/duration; *provenance* = hosts/ids/paths/attempt/retried/previous/next/
source/tags).

### Assemble body → `Group`

```python
body = Group(header, "", regions)        # "" = blank spacer line between header and regions
card = Panel(body, ...)
```

`Group` (from `rich.console`) stacks renderables vertically; a bare `""` string is a valid
blank-line member.

### Lower-right `status:` badge

Two idioms — **prefer (a)**:

- **(a) Panel subtitle** (shown above): `subtitle="status: OK"`, `subtitle_align="right"`
  renders it inside the bottom border, flush lower-right. Verified: renders as
  `╰──… status: OK ─╯`. Zero extra layout. Style it inline, e.g.
  `subtitle="[green]status: OK[/]"` or a `Text("status: OK", style=select_style(...))`.
- **(b)** If you want it *inside* the body instead of the border: append a
  `Table.grid(expand=True)` with one right-justified column and one row, or `Align.right(Text(...))`.
  More code, no advantage here.

### Rendering many cards

```python
console = Console()
for task in tasks:
    console.print(make_card(task, width=w))   # Panel already has border margins; no separator needed
```

The panel border provides visual separation, so no explicit `---` rule is required (unlike
`print_normal`, which prints `'---'` between blocks). One `Console()` reused in the loop is fine.

## 4. Responsive width

Read effective width from the console (verified attrs on 13.9.4):

- `Console().size.width` / `Console().width` — terminal width (both `80` in a pipe / no TTY).
- `console.options.max_width` — the render-width budget; same as `width` at top level.

Use `console.width`, then **clamp**:

```python
w = max(60, min(160, Console().width))
```

Then select a layout variant from `w` and pass `width=w` to the `Panel`:

- **narrow (`w < ~80`):** stack regions vertically — put all label/value pairs in **one shared
  `kv_grid`** (so labels align) inside the `Group`, no `Columns`.
- **normal (`~80 ≤ w < ~120`):** two regions side-by-side via `Columns(..., equal=True)`.
- **wide (`w ≥ ~120`):** same side-by-side, optionally 3 regions, wider value columns.

Pick thresholds inside the clamped [60,160] band; tune against the prototype.

### `width=` vs `expand` interaction (gotcha)

- Passing `Panel(..., width=w)` **overrides** `expand` and fixes the outer width to exactly `w`
  — this is how you enforce the [60,160] clamp regardless of the real terminal (prevents a
  1000-col terminal from producing an unreadable card). **Set `width=w` explicitly.**
- Interior grids/`Columns` should use `expand=True` so they fill the clamped panel; but the
  Panel's fixed `width` is the hard bound.
- If instead you `Console(width=w).print(card)` with an `expand=True` panel, the panel fills `w`
  — equivalent, but setting `Panel(width=w)` is more explicit and lets you keep a single
  `Console()`.

### Gotchas summary

1. `rich` has **no `__version__`** — use `importlib.metadata.version`.
2. `config.console.theme` is a *Syntax* theme string, **not** a Table/Panel theme — don't pass
   it to the card.
3. **Narrow-mode label alignment:** two *separate* `kv_grid`s stacked don't share column widths,
   so labels won't line up. In narrow mode use **one** grid for all pairs.
4. `Panel(width=w)` overrides `expand`; that's the lever for the [60,160] clamp.
5. No TTY ⇒ `console.width == 80`; the clamp floor of 60 keeps piped output sane. Consider
   respecting `COLOR_STDOUT` for whether to apply border/status color (mirror table path).
6. `overflow="fold"` on the value column prevents long `command`/paths from truncating with `…`.
