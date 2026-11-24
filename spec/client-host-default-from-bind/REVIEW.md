# REVIEW — `hs client` dials `server.bind`, not `server.host`

> Adversarial QA by `hs-review`, run in an isolated/clean context. The correctness pass grades the
> branch diff against [`GOAL.md`](GOAL.md) + the AGENTS.md invariants **only**. Every finding cites
> an **executed** command.

- **Reviewed commit:** 7ec82256  ·  **Base:** develop (1dd734d7)  ·  **Date:** 2026-11-23
- **Verdict:** changes-requested
- **Cycle:** 1 of ≤3
- **Contract drift check:** `GOAL.md` changed after shaping only in b88a09ae, the GOAL gate with the
  maintainer's rulings.

## Verification run

Fresh reviewer, xhigh effort, on Python 3.14 and 3.11.

- **Real drives, 44 cases each on 3.14 and 3.11, all passing.** Each case starts its own TLS
  `hs server`, runs a real `hs submit --queue` and `hs client`, and logs every `socket.connect`.
  `QueueConfig.load().host` is read in a child process.
- **Configuration layers covered:** system, user, local, `HYPERSHELL_CONFIG_FILE` and the
  environment.
- **Develop contrast:** on develop the seed case dials `0.0.0.0`; on the branch it dials the
  configured host.
- **Suites:** the full suite on 3.14 gave 4238 passed. On 3.11, the 14 files touching this surface
  gave 1414 passed.
- **Docs and completion:** the docs build passes under `-W`, and `zsh -n` is clean.

## Requirement → evidence matrix

| R-ID | Verified how | Status |
|------|--------------|--------|
| R1 | Seed `bind = "0.0.0.0"` with `host` set, in every layer: all three readers dial the host and the task runs | ✅ |
| R2 | `-H` wins over a configured host, advertise and bind; with `-H`, a broken `host_env` is never read | ✅ |
| R3 | Every branch, in every layer: host over advertise; advertise over a specific bind; a specific bind; a wildcard or empty bind gives localhost; nothing given gives localhost; an explicit `host = "localhost"` wins; `_env`/`_eval` forms; `<none>` falls through | ✅ |
| R4 | `hs submit --queue` and `QueueConfig.load()` match `hs client` in all 44 cases, through the one `client_host` | ✅ |
| R5 | The API defaults stay `localhost` (`DEFAULT_HOST`, `QueueConfig()`, `ClientThread`, `run_client`) | ✅ |
| R6 | `hs server` binds as before. Launched clients are told the address by the unchanged `advertise` rule. A broken `host_env` doesn't affect `hs list`, `hs info`, `hs update`, `hs wait`, `hs config`, `hs server` or `hs cluster` | ✅ |
| R7 | `tests/test_client_host_default.py`, 70 tests, passes on 3.14 and 3.11 | ✅ |
| R8 | Help text, `.rst`, the reference, markers, zsh and the docs build, apart from F1 | ❌ F1 |

Unmapped changes (benign): `WILDCARD_BINDS` moved to `core/config.py`; `server.py`
`DEFAULT_BIND` now comes from `default.server.bind`, with the value unchanged; `QueueInterface.new()`
now uses `client_host()`; `host_eval` and `bind_eval` no longer run at import.

## Findings

### [LOW/CONFIRMED] F1 — The `[server] .bind` reference says a client otherwise dials localhost
- **Where:** `docs/_include/config_param_ref.rst:187-189`.
- **Failure scenario:** read literally, "otherwise *localhost*" covers configurations that give
  `.host` or `.advertise`. Those dial `.host` or `.advertise`, as real drives showed
  (`host-over-advertise-env`, `advertise-over-bind-local`).
- **Touches:** R8.

## Absurd-input and non-goal notes (not findings)
- `bind = "::"` is dialled as `'::'`; IPv6 is a non-goal.
- An empty `host` is dialled as `''`, per the non-goal.
- A `host_eval` with output after a failing step dials that output, which is ordinary `_eval`
  behaviour.

## Human-gate triggers
- None. F1 is a docs sentence.
