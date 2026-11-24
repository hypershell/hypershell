# 04 — Tests to restate, docs to rewrite, the benchmark

Brief for `hs-plan` (2026-11-23). What the build must touch outside `core/queue.py`, and what the
existing harness already gives R5 and R10.

## Tests that meet a pickle-framed peer today

The GOAL (R7) names three restatements. A sweep of `tests/` for stock peers (`BaseManager`,
`Listener`, `Client`, `'pickle'`) finds two more, outside the pinned surfaces:

| Test | Stock end | After the codec | Restatement |
|---|---|---|---|
| `test_dispatch_version.py::test_old_client_is_refused_while_new_client_works` (`:118`, `OldClient(BaseManager)` `:48`) | client | refused at `connect()` by R5 before it reaches `_get_scheduled()` | `OldClient` becomes a codec peer (`SecureManager`) that omits the protocol argument, so the `ClientTooOld` path is still checked; a new test asserts R5 with the stock peer |
| `test_queue.py::TestPrivateInternals::test_create_passes_args_and_refusal_is_a_traceback` (`:213`, `Caller(BaseManager)`) | client | same | `Caller` becomes a `SecureManager` subclass; the pins (`*args` reach the registry callable, a refusal is a `#TRACEBACK`) are unchanged |
| `test_cluster_launch.py` `CONTRACT_TAG = '2.9.0a4'` (`:970`), `test_old_client_runs_under_new_launcher` (`:1099`), `assert_old_client_ran` | a 2.9.0a4 client, run from the tag's tree | cannot connect | see below |
| `test_queue.py::test_plain_listener_under_default_timeout` (`:376`) | stock `Client` dialling `_PlainListener` | the stock end unpickles the codec's `'ready'` | the dialling end is `_plain_client`, or the exchange uses `send_bytes`/`recv_bytes`; what it checks (blocking mode under a default timeout, handshake and first exchange) is unchanged |
| `test_queue.py::test_plain_client_under_default_timeout` (`:398`) | stock `Listener` echoing | the stock end unpickles the codec's `'ping'` | the listening end is `_PlainListener`, or raw bytes; same check |

Not affected (both ends HyperShell's, or both stock): `test_queue_tls.py` (`SecureListener` +
`secure_client`, a `dict` payload, inside the vocabulary), `test_queue_address_family.py` (the stock
pair as reference, or the secure pair), `test_queue_gc.py:695` (`dispatch(conn, None, 'shutdown')`
through the manager's own `_Client`), `test_queue_tls.py:520-560` (the forked manager, Q1: keeps the
codec through the shared pair; its callable returns a `dict`).

### The 2.9.0a4 contract (R7)

`CONTRACT_CLIENT_OPTIONS` is the option set of a 2.9.0a4 client, checked against the tag's
`client.py` (`test_launch_contract_matches_tagged_client`), and every launch may emit only those
options (`test_launch_contract_uses_only_known_client_options`). `test_old_client_runs_under_new_launcher`
runs the tag's own client tree under the new launcher and expects the run to complete. After the
codec no release before 2.9.0a10 can connect, and 2.9.0a10 has no tag yet. Restatement:

- The option floor stays 2.9.0a4's set, and `CONTRACT_TAG`'s docstring says why: the launch keeps
  the option floor it had; the oldest client that can *connect* is 2.9.0a10 (`AGENTS.md`,
  `invariants.md` §11 restated to match). Every option in that set is one this tree's client accepts
  except `-k`/`--auth` (checked 2026-11-23 against `client.py`'s `add_argument` calls), which the
  launch never emits (the key travels in the environment or on ssh's stdin, §11); the `-R` bend stays.
- `test_old_client_runs_under_new_launcher` becomes an R5 check at the launch level: the tag's client
  under the new launcher runs no task, the server logs the version-mismatch WARNING, and the cluster
  ends within its bound instead of hanging (how depends on what a 2.9.0a4 client does with the fixed
  reply: brief 02).

## Docs that describe the old envelope (R11)

- `docs/security.rst` known limitation 2, "Pickle-serialized RPC" (`:402-407`): rewritten, with
  `.. versionchanged:: 2.9.0`.
- `docs/security.rst` "How It Works" (`:135-151`): "The framing, the handshake, and every byte on the
  wire are those of a stock `BaseManager`" (`:150`) is no longer true for the envelope; the framing
  and handshake still are.
- `docs/alternatives.rst:826-828`: "the manager's RPC *framing* uses Python `pickle`" — also an
  "everywhere they describe the old one" line, not named in R11; it must change too.
- `AGENTS.md`: the wire-format bullet (`:422-425`), the pinned-surface paragraph's "pickle-framed RPC
  surface" (`:189`), "The server process hosts the manager" bullet's "The wire, auth and TLS
  behaviour are those of the stock manager, unchanged" (`:388-392`), the `core/queue.py` hotspot line
  (`:683`), and the 2.9.0a4 contract sentence (`:471-474`).
- `.agents/factory/invariants.md` §9 ("Multiprocessing RPC framing is **pickle**", in lockstep with
  `AGENTS.md`) and §11 (the 2.9.0a4 sentence).
- The change log is the PR's squash commit body (`AGENTS.md` § Docs, `:51`, `:661`), drafted by
  `hs-publish`; the line that a 2.9.0a10 peer cannot connect to 2.9.0a9 or earlier, in either
  direction, goes there (and into the "What's new" admonition at `hs-release`). There is no
  changelog file to edit.

## The benchmark (R10) and the skew check (R5)

`tests/bench/bench_queue.py` already has what R10 needs:

- `gate --base REF --feature REF` builds each ref into its own locked venv (`git archive` + `uv sync
  --frozen`), runs `hs server` with loopback clients over a fresh SQLite site, interleaved, `--repeats
  5` by default, and reports the median and spread per config. Its default pass rule is
  `feature median >= base median - max(base spread, feature spread, 5% of base median)` (`:533-536`),
  which contains the Q4 threshold (5%, or 2.9.0a9's own spread if larger) but also admits the
  feature's own spread. The plan reads the Q4 rule off the printed medians and min-max (base spread
  only), not the tool's verdict.
- Bundle size 1 is `GATE_CONFIGS` rows `b=1` (TLS and cleartext); the default bundle size: the
  server's and client's `bundlesize` default to 1 (`core/config.py:148`, `:173`), so "default" is the
  `client-default` rows (the client advertises no size; `-b 1` and `-b 50` on the server); `b=50`
  rows cover a larger bundle. Base ref `2.9.0a9` (a tag).
- `skew --new REF --old REF` already drives an old client against a new server and a new client
  against an old server, TLS on and off, with a bound (30 s). Its pass strings are this cycle's to
  restate: it counts `Client too old` in the server log (`:410`) and wants one CRITICAL line naming
  `work-request protocol` (`:440`). Against `--old 2.9.0a9` they become the R5 WARNING and the R5
  CRITICAL. That is the end-to-end proof of R5 with a real release, both directions, both transports.
- `parity --base 2.9.0a9 --feature HEAD` checks identical final task tables and clean exits, TLS on
  and off: the R3 "byte for byte" property at the CLI level.

The bench is never collected by pytest and needs `psutil` (in the dev group) and network for `uv
sync` of each ref (pypi.org, files.pythonhosted.org).
