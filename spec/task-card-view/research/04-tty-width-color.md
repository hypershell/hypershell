# Research 04 — Terminal width detection & color/TTY gating

Scope: how the repo gates color/TTY today and how the responsive `card` format should
obtain width and degrade on non-TTY / NO_COLOR. Facts verified against the installed
`rich 13.9.4` and `cmdkit.ansi`.

## 1. `COLOR_STDOUT` — where it comes from

It is **not** defined in HyperShell. It is imported from cmdkit:
`task.py:31` → `from cmdkit.ansi import red, green, yellow, faint, COLOR_STDOUT`.

Definition (`.venv/.../cmdkit/ansi.py:26-37`), computed **once at import time**:

```python
NO_COLOR    = False if not os.getenv('NO_COLOR') else True
FORCE_COLOR = False if not os.getenv('FORCE_COLOR') else True
COLOR_STDOUT = True
COLOR_STDERR = True
if not sys.stdout.isatty() or NO_COLOR:
    COLOR_STDOUT = False          # piped OR NO_COLOR -> off
if not sys.stderr.isatty() or NO_COLOR:
    COLOR_STDERR = False
if FORCE_COLOR:                   # FORCE_COLOR wins over both
    COLOR_STDOUT = True
    COLOR_STDERR = True
```

So `COLOR_STDOUT` = `sys.stdout.isatty() and not NO_COLOR`, overridden `True` by `FORCE_COLOR`.
It honors the `NO_COLOR` / `FORCE_COLOR` env conventions. **Caveat: import-time** — a test/
harness that swaps `sys.stdout` after import won't change it.

Only use in `task.py`: `colorize_rows` (`task.py:747-750`):
```python
return not self.show_count and self.output_format in ('plain', 'table') and COLOR_STDOUT
```
It gates whether `print_plain` wraps lines with `select_color(...)` and whether `print_table`
passes a per-row `style=`. When off, the ANSI shorthands (`red`, etc.) are already no-ops too
(`format_ansi` returns text unchanged when `not COLOR_STDOUT`).

## 2. How the repo builds a rich `Console` today

Every call is a bare, throwaway `Console()` with **no args** — no theme, no `force_terminal`,
no width: `config.py:226`, `task.py:218`, `:229`, `:781`, `:806`. There is **no shared/module
console**. Width and color are thus auto-detected from the real terminal each time.

`config.console.theme` (`core/config.py:183-185`) defaults to `'monokai'` and is used **only**
as the Pygments theme for `Syntax(...)` JSON highlighting — it is a syntax-highlight theme, not
a rich `Theme` object, and is unrelated to width/color gating.

## 3. Existing non-TTY behavior

- **JSON** (`print_json`, `task.py:801-810`) and `print_field`/`print_formatted`
  (`:215-231`) branch on `sys.stdout.isatty()`: TTY → `Console().print(Syntax(...))` (pretty,
  themed); non-TTY → plain `print(json.dumps(...))`. This is the established pattern: **only
  prettify on a TTY**.
- **Broken pipe**: `exceptions = {BrokenPipeError: handle_broken_pipe}` on the search/list app
  (`task.py:684`) and info app (`:183`). `handle_broken_pipe` (`core/exceptions.py:99-114`)
  dup2's stdout to `/dev/null` and returns `exit_status.success`, so `hs list --all --csv | head`
  exits cleanly instead of dumping a traceback. **Any card renderer must not defeat this** — let
  `BrokenPipeError` propagate to the app's exception mapping (don't catch/swallow it).

## 4. rich width & color behavior (verified, rich 13.9.4)

- **Non-TTY width fallback = 80.** `Console(file=StringIO()).size.width == 80`,
  `.is_terminal == False`, `.options.max_width == 80`. On a real TTY, `.size.width` is the live
  terminal width. `.width == .size.width` for a default console.
- **`COLUMNS` env overrides width** even when redirected: `COLUMNS=200` → `.width == 200`
  (verified). rich honors `COLUMNS`/`LINES`.
- **Color auto-drops on non-TTY.** Printing styled text / a `Panel` to a StringIO emitted **zero
  ESC bytes** — but the box-drawing border chars (`╭─╮│╰╯`) and the text still render. So a
  bordered card piped to a file is legible, just uncolored.
- **`NO_COLOR`**: rich reads it independently of cmdkit. With `force_terminal=True` +
  `NO_COLOR=1`, `console.no_color` is `True` and **color** escapes are stripped, though
  non-color attributes (e.g. bold `\x1b[1m`) may remain. Net: status text stays readable.
- **`console.options.max_width`** is the width used to measure/constrain a renderable; construct
  child options via `console.options.update_width(n)` to render a card at a chosen width, or set
  `Console(width=n)` / `Panel(..., width=n)` to pin it.

## 5. Recommendations

**Obtaining effective width.** In the card renderer, construct one `Console()` (bare, like the
rest of the module) and read `console.size.width`. This transparently gives the live TTY width,
the `COLUMNS` override, and the 80-col non-TTY fallback for free — no manual `os.get_terminal_size`
/ `shutil` needed. Reuse that same console object to `print` the card so width used for layout ==
width used for render.

**Clamp + thresholds (starting point):**
```
raw   = console.size.width            # 80 when piped
width = max(60, min(160, raw))        # clamp to [60, 160]
if   width < 90:   layout = 'narrow'  # stacked / single column, labels above values
elif width < 130:  layout = 'normal'  # 2-column label:value
else:              layout = 'wide'    # multi-column / room for output preview
```
Rationale: piped output lands at 80 → `narrow`, which is the safest stacked layout for redirected
consumers. Render the card body at the clamped `width` (e.g. `Panel(..., width=width)` or via
`console.options.update_width(width)`) so a very wide terminal caps at 160 and a tiny one floors
at 60 rather than producing an unusable card.

**Non-TTY / no-color fallback.** rich already strips ANSI when `not console.is_terminal` (matches
`COLOR_STDOUT` being off) and keeps the Unicode border + text, so a single code path can serve
both. Recommended: **still emit the bordered card** (borders survive as legible box chars) but
guarantee the status is plain-text readable — render it as literal `status: FAILED` /
`status: SUCCESS (0)` text, never color-only. Pick the layout from the (clamped) width, which is
`narrow` at the 80-col pipe default. Optionally, if `not COLOR_STDOUT` (or `not console.is_terminal`),
consider ASCII/`box.ASCII` borders for maximal portability — but plain-text legibility is already
satisfied without it, so this is optional polish, not required. Do **not** special-case away the
border for pipes unless a `--plain`-style flag is requested; the JSON precedent only swaps *pretty
highlighting* for plain text, and a card's border carries no color dependency.

**Don't break the pipe.** Keep `BrokenPipeError: handle_broken_pipe` in the app's `exceptions`
and let it propagate (as `hs list` does) so `hs ... --format card | head` exits 0.
