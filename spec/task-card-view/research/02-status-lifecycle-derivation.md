# Status label + color derivation for the `card` view

Research topic: how to derive a virtual `status:` badge (OK / FAILED / CANCELLED /
RUNNING / WAITING / abnormal) from a fully-loaded `Task`, and where that logic belongs.

## 1. Lifecycle predicates (ground truth from `data/model.py`)

Task state is a function of nullable columns, not a stored enum. Confirmed against the
`Task` classmethods:

- **unscheduled / WAITING** — `schedule_time IS NULL`
  (`select_new`: `.filter(cls.schedule_time.is_(None))`, `model.py:559`).
- **in-flight / interrupted / RUNNING** — `schedule_time` set AND `completion_time IS NULL`
  (`select_interrupted` / `count_interrupted` / `select_orphaned`, `model.py:647-666,728-738`).
  Note: an *interrupted* task (server died mid-flight) is indistinguishable here from a live
  running one; both correctly read as RUNNING until `revert_interrupted()` resets them on the
  next server start.
- **done (terminal)** — `completion_time` set (`count_remaining`: `.filter(cls.completion_time.is_(None))`
  for the *not*-done set, `model.py:641`).
- **cancelled (terminal)** — `exit_status == CANCEL_STATUS` AND `completion_time` set.
  `cancel_all` (`model.py:704-720`) stamps `schedule_time`, `completion_time` (both = now) and
  `exit_status = CANCEL_STATUS`. The comment there is load-bearing: completion_time is set so
  the task is terminal, else it reads as interrupted and gets reverted/re-run.

`CANCEL_STATUS: Final[int] = -1` (`model.py:49`). Its docstring: cancelled is terminal, never
scheduled/retried/counted-as-failure; distinguished from genuine failure *solely* by this value.
Every failure/retry query filters `exit_status IS NOT NULL AND != 0 AND != CANCEL_STATUS`
(`select_failed` `model.py:564-576`, `increment_group` `model.py:530-538`). **-1 collides with a
SIGHUP death** (`SIGHUP = 1` → subprocess exit_status -1); this collision is documented and
accepted — a SIGHUP-killed task reads as CANCELLED.

## 2. "Never ran" sentinels

Defined in `client.py:616-617` (NOT in model.py):

```
TASK_TEMPLATE_ERROR: Final[int] = -1001  # Template expansion failed (task never ran)
TASK_RESOURCE_ERROR: Final[int] = -1002  # Insufficient local resources (task never ran)
```

Documented rule (client.py:611-615 and AGENTS.md): signal deaths occupy `-1..-64`
(exit_status = -N); **all HyperShell-internal "never ran" sentinels live below -1000** to stay
clear of that range. So `exit_status <= -1000` is a stable, forward-compatible predicate for the
never-ran class *without importing the exact constants*.

**Do NOT import these from `client.py` for the derivation.** `client.py` imports
`from hypershell.data.model import Task` (client.py:59), so model.py importing client.py is a
circular import. `client.py` is also import-heavy (multiprocessing, subprocess, `core.resource`
which computes `CPU_COUNT`/`MEMORY_TOTAL` at import, config singleton, template). Pulling that
into `task.py` or `model.py` just to read two ints is wrong. Classify by the `<= -1000` range
instead. (Optional future refactor: promote both sentinels into `model.py` next to
`CANCEL_STATUS` to centralize the whole `exit_status` vocabulary, and have client.py import them
from there — clean, but out of scope for this feature.)

## 3. Existing classification in `task.py`

- `no_color(_text)` — identity passthrough (task.py:1323).
- `SPECIAL_TASK_COLORS = {None: no_color, 0: green, CANCEL_STATUS: faint}` (task.py:1328).
- `select_color(status)` (task.py:1335-1345): `SPECIAL_TASK_COLORS.get(status, yellow if status
  is not None and status < 0 else red)`. So: **None → uncolored, 0 → green, -1 → faint,
  other-negative → yellow, positive → red.** The `None` guard is deliberate — `dict.get`
  evaluates the default eagerly and `None < 0` would raise.
- `SPECIAL_TASK_STYLES = {None: None, 0: 'green', CANCEL_STATUS: 'dim'}` + `select_style`
  (task.py:1351-1360): rich-style analog for table rows (`dim` == same SGR as `faint`);
  other-negative → 'yellow', positive → 'red'.
- `print_normal` (task.py:1382-1401) applies ONE flat `select_color(task.exit_status)` over the
  whole rendered block. The card view replaces this flat coloring with a per-badge status.

Key gap: `select_color`/`select_style` key on `exit_status` ALONE, so they cannot tell WAITING
(unscheduled) from RUNNING (in-flight) — both have `exit_status IS NULL`. The card badge needs
the nullable-column predicates, i.e. the whole row, which is exactly why derivation must see the
`Task`, not just its `exit_status`.

Color rendering primitives available: `cmdkit.ansi` exports `bold, faint, italic, underline,
black, red, green, yellow, blue, magenta, cyan, white`. `format_ansi` composes safely
(`bold(green(t))` yields `BOLD GREEN t RESET` — the inner wrap ends in RESET so the outer only
prepends). If the card renders via `rich` (like `print_table`) use style strings; if via
`cmdkit.ansi` (like `print_normal`) use composed callables. `print_normal` uses cmdkit.ansi, so
the card most likely does too.

## 4. RECOMMENDATION

### 4a. Predicate ladder (on a fully-loaded Task; order matters)

```
1. schedule_time is None                         -> WAITING     (unscheduled)
2. completion_time is None                        -> RUNNING     (scheduled, in-flight/interrupted)
3. exit_status == 0                               -> OK
4. exit_status == CANCEL_STATUS (-1)              -> CANCELLED    (also SIGHUP death, documented)
5. exit_status is not None and exit_status <= -1000 -> abnormal:ERROR   (never-ran sentinel)
6. exit_status is not None and exit_status < 0    -> abnormal:KILLED  (signal death, -2..-64)
7. exit_status is not None and exit_status > 0    -> FAILED
8. else (completion_time set, exit_status None)   -> abnormal:UNKNOWN (defensive; anomalous row)
```

Ordering rationale: steps 1-2 use the lifecycle nullable-column predicates first (only these
distinguish WAITING vs RUNNING). Once terminal (completion_time set), classify by exit_status.
CANCELLED (step 4) MUST precede the generic negative branches or it degrades to KILLED. Never-ran
(`<= -1000`, step 5) MUST precede the signal-kill branch (step 6) since both are `< 0`. Step 8
handles the inconsistent "completed but no status" row honestly as abnormal rather than
mislabeling. This reproduces the model's predicates exactly and stays consistent with
`select_color`'s exit_status semantics (0=OK/green, -1=cancel, other-neg=abnormal, pos=FAILED).

The GOAL lists a single "abnormal" state; I recommend the property return the *granular*
sub-labels (ERROR / KILLED, plus UNKNOWN defensively) and let the style map collapse them to one
"abnormal" style. This costs nothing and gives a strictly more useful badge; if the GOAL insists
on a single literal, return `ABNORMAL` for steps 5/6/8.

### 4b. Where the logic lives

**Put the predicate ladder on `Task` as a read-only property in `data/model.py`** — e.g.
`Task.status_label -> str` (or a small `TaskStatus` Enum). This is the AGENTS.md invariant:
"task state/query logic → `Task` classmethods; never hand-roll state predicates in FSM [or,
here, presentation] code." The ladder *is* lifecycle-state logic (it re-derives the same
nullable-column predicates as `select_new`/`select_interrupted`/`cancel_all`); scattering it into
`task.py` would create a second, drift-prone copy of the lifecycle contract. It needs no DB/query
imports (pure attribute reads), so it adds no import weight to model.py and cannot import
client.py. Use the `<= -1000` range check for never-ran (see §2).

**Keep the label→style mapping in `task.py`** next to `select_color`/`SPECIAL_TASK_STYLES` — that
is presentation, the layer that already owns cmdkit.ansi/rich rendering. So: model.py owns
*state → label*; task.py owns *label → color*. This split respects both the invariant and the
existing precedent (select_color stays put). `select_color`/`select_style` remain for the
plain/table row coloring (they key on exit_status and are fine there); the card badge uses the
new `status_label` + a new label→style map.

### 4c. Label -> style map (task.py, parallel to SPECIAL_TASK_STYLES)

GOAL-fixed: OK = bold green, FAILED = bold red, CANCELLED = yellow. Recommended for the rest
(chosen to stay distinct from CANCELLED's yellow and readable in light/dark):

| Label     | rich style     | cmdkit.ansi callable   |
|-----------|----------------|------------------------|
| OK        | `bold green`   | `lambda t: bold(green(t))` |
| FAILED    | `bold red`     | `lambda t: bold(red(t))`   |
| CANCELLED | `yellow`       | `yellow`               |
| RUNNING   | `cyan`         | `cyan`                 |
| WAITING   | `dim`          | `faint`                |
| abnormal (ERROR/KILLED/UNKNOWN) | `magenta` | `magenta`   |

Notes: CANCELLED = yellow per the GOAL *diverges* from `select_style` (which maps CANCEL→'dim'
and other-negative→'yellow'); this is an intentional card-specific choice — flag it so the two
schemes are consciously different. `dim`/`faint` for WAITING matches "not started, low emphasis"
and echoes select_color's uncolored/None handling. `magenta` for abnormal keeps it visually
distinct from CANCELLED's yellow while reading as "something weird happened"; `bold yellow` is a
reasonable alternative if a warm caution tone is preferred. If a single "abnormal" label is used,
map it to `magenta`.
