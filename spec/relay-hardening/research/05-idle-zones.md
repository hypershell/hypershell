# Research 05 — R5 idle zones and R6 docs

Read-only brief for `hs-plan`. Lines are `develop` at `2ffb5cc` (2.9.0a8).

## The mechanism today

- **Gate** (`src/hypershell/relay/upstream.py:365-398`): a zone is asked only if `bound(zone) > 0`
  and `backlog <= bound // 2` (`:391`). `bound` is site clients × `local_bundlesize` × 2
  (`:348-357`), so a zone with no site client is **never asked**.
- **`finish_idle`** (`:400-411`, called at `:378` once `self.finished` is non-empty): it ends every
  zone with `bound == 0 and backlog == 0`, without a request. The stated premise is at `:247-249` and
  in the test docstring at `tests/test_relay_connector.py:559-561`.
- **Wire**: the request is the stock client JSON (`request_text`, `:198`). The reply is
  `{"protocol":1,"tasks":[...],"shutdown":bool}` (`core/dispatch.py:146-149`). No sentinel is
  involved. The relay reads `reply.get('shutdown')` (`upstream.py:428`), and so does the plain
  client (`client.py:236`).
- **Dispatcher** (`dispatch.py:313-360`): `_done` = `finishing and not pool.rows and (inbound is
  None or inbound.empty())`. A request served at the head of the line cuts the staged rows first,
  and gets `shutdown` only once its own zone's pool is empty. A request for a zone whose pool still
  holds rows after `finish()` **drains them** (always at least one row: `size = max(1, …)`, `:331`).
  `finish()` (`:531`) is called only from `signal_clients` (`server.py:933-939`, via
  `wait_heartbeat`). That happens after the scheduler is done, or, in memory, as soon as submission
  ends. So "a zone heard shutdown" implies the server stages nothing more.
- **With a database**, rows staged for a zone stay staged after `finish()`. `revert_staged`
  (`server.py:1218`) puts back the stamped ones only after the heartbeat monitor ends. That happens
  when `not self.beats and scheduler_done` (`server.py:902`), so a relay whose identities still beat
  holds a non-forever upstream open. **Without a database**, default-zone rows sit in the pool or
  the inbound queue unstamped, and `take_staged` returns `[]`, so they are lost for that run with
  nothing logged.

## Probes (`$TMPDIR/rh-05/probe.py`: the real `UpstreamPuller` and `Dispatcher`, in process)

| case | develop | candidate (b) |
|---|---|---|
| DB: 2 rows staged for `gpu`, clients `{cpu:1,gpu:0}`, `finish()` | **FINAL**, gpu held 0, **2 still staged** | GATE, gpu holds 1, 1 staged, gpu not finished |
| no DB: 4-task inbound bundle, one earlier default request (size 1), zones `(gpu, default)`, clients `{gpu:1}` | **FINAL**, **3 left in the default pool** | GATE, default holds 1, 2 staged |
| DB, nothing staged | FINAL 2/2 | FINAL 2/2 |
| no DB, nothing staged | FINAL 2/2 | FINAL 2/2 |
| DB, 3 staged → gpu client arrives → tasks done | — | takes the other 2 (staged 0), then FINAL 2/2 |

Under the monkeypatch, `-m unit tests/test_relay_*.py` gives 371 passed and 3 failed. All 3
failures are the same assertion, `[request['zone'] …] == ['cpu']`, at
`test_relay_connector.py:566` (both params) and `:607`; the run now also sends one `gpu` request,
which `ShuttingDown` answers with shutdown. The FINAL state and the `finished` set are unchanged.
On develop all 29 pass. The integration multizone tests (`test_relay_workload.py:1052,1111`) were
not run.

## Options

- **(a) End an idle zone only after a site period with no client.** This does not meet R5: the rows
  run only if the fleet connects within the period, and batch launch waits can be far longer. It
  adds a full period to every multi-zone finish that has an idle zone (R5: "still end with the
  others"), raises a config question, and makes timing tests flaky. Even with it, the relay still
  cannot tell a zone with nothing staged without asking. **Reject.**
- **(b) One more request per idle zone at the finish.** Once `self.finished` is non-empty, a zone
  with `bound == 0`, `backlog == 0` and not finished is picked at the gate and sends a request of
  size 1. Its own reply decides: `shutdown` ends it with the others, a task is held (backlog > 0, so
  it is not probed again) until a site client arrives and the zone asks as usual, and an empty reply
  (it was not at the head of the line) means it is probed again on the next pass, each wait being at
  most `ZONE_REQUEST_WAIT`. **No wire change, relay only.** A 2.9.0a8 server runs the same
  `Dispatcher`, so the fix works against it too. A 2.9.0a8 relay against this server is unaffected
  because the server does not change. A zone holding a task with no client is exactly how a zone
  that held work behaved before (the `backlog` check in `finish_idle`): the relay keeps beating and
  the non-forever upstream waits for it. The relay's local server stores a held task with no client
  (`insert_received`, as for any bundle).
- **(c) A shutdown reply that says whether anything is staged.** This is a server change in
  `dispatch.py` (every server, the invariant §6 path). "Staged anywhere" cannot tell this relay's
  zones from other relays' zones, so it needs a per-zone list, and the zone still has to ask again
  to take the rows, which is (b) anyway. Old relays ignore an extra key (`.get`). This relay against
  a 2.9.0a8 server has to fall back to `finish_idle`, which leaves C5-1 open there. **Reject.**

## Recommendation: (b), probe size 1

It is the only option that meets R5 exactly with no new state: the upstream answers the exact
question ("anything staged for *this* zone?") with the existing protocol. It touches no server and
no wire, so R10 holds in both directions, and the non-goal holds: a relay with no client in any zone
never hears a shutdown, so it never probes. Size 1 keeps the excess over the site bound (0) to one
task per idle zone, and only at a finish. Sketch: in `gate`, replace the `finish_idle` call and the
zone loop's `bound == 0` skip with `if bound == 0: if not self.finished or backlog: continue;
size = 1`. Drop `finish_idle` (its info log becomes an info line when an idle zone takes tasks at
the finish, e.g. "holds N tasks for zone X with no site client - waiting for one"). `request()`
already ends the zone and reaches FINAL.

## Corrected texts (R5)

- **`upstream.py:247-249`**: "The upstream server finishes for every zone at once, but serves each
  zone what is still staged for it before it answers that zone's requests with a shutdown. So once
  one zone has had a shutdown reply, a zone with no site client and nothing held, which otherwise
  never asks, asks once for itself, for one task. A shutdown ends it with the others; a task it takes
  is held until a site client in the zone runs it, and the zone then asks as usual. A relay with no
  site client in any zone never asks at all, so it never hears a shutdown, and pulling ends only on a
  drain or halt."
- **Test `:557-561`** (rename to `…_asks_once_and_ends_on_its_own_shutdown`): "A zone with no site
  client never asks for work, so once another zone has heard the shutdown it asks once for itself
  and ends on its own reply. The upstream server finishes for every zone at once, but serves each
  zone what is still staged for it first. A zone with nothing staged, no site client and nothing
  held is told at once and ends; pulling ends, so the relay sends its final heartbeats and a
  non-forever upstream server exits." Amend the request lists at `:566`/`:607` to `['cpu', 'gpu']`.

## R9 test shape (`tests/test_relay_connector.py`, beside `:556-617`, `@mark.unit`, no threads)

Parametrize `upstream_db` over True and False, using the existing `zoned_puller`, `drive` and
`complete_all`. `connected` is a lambda over a dict, so setting `clients['gpu'] = 1` mid-test works.
- With DB: `Dispatcher(bundlesize=1, queuesize=1)`, `stage(zone_id('gpu'), 3 dispatched rows)`,
  `finish()`, clients `{cpu:1, gpu:0}`. Assert `drive` returns GATE, gpu is not in `finished`,
  `count_backlog(gpu) == 1` and `staged(gpu) == 2`. Then add a gpu client, `complete_all`, `drive`,
  and assert `staged(gpu) == 0`. Then `complete_all`, `drive`, and assert FINAL with every staged id
  in `relay_task`.
- Without DB: `Dispatcher(inbound=Queue())`, put one 4-record default bundle, one prior request
  `{'client':'gone','size':1,'wait':0}`, `finish()`, zones `(gpu, None)`, clients `{gpu:1}`. Run the
  same assertions on `DEFAULT_ZONE_ID` and finish with the pool and inbound empty.
- Nothing staged (both modes): FINAL at once, both finished, requests `['cpu', 'gpu']`.

The develop probe rows show both staged cases reaching FINAL with rows left, so each test fails on
`2ffb5cc`.

## R6 docs (`docs/_include/relay_desc.rst:56-59`; the man page shares it via `docs/manual.rst:101`)

Current text: "It hears of the shutdown in reply to a request for work, in any of its zones; a zone
with no *client* at the site and no task held then ends with the others. A relay with no *client*
at the site in any zone never asks, so it waits until it is drained or stopped."

Proposed: "It hears of the shutdown in reply to a request for work, in each of its zones. Once one
zone has heard it, a zone with no *client* at the site and no task held asks once more for itself:
it ends with the others if nothing is left for it upstream, and otherwise holds what it is sent
until its *clients* run it. A relay with no *client* at the site in any zone never asks for work, so
it never hears an upstream shutdown: it waits until it is drained or stopped, and a non-forever
upstream *server* waits for it too, since its identities still beat, until the relay is drained or
stopped."

Marker: none inside `relay_desc.rst`. No `_include` prose carries one, and the man page shows none
(0 "Changed in version" lines in `hs.1`). Following the `:pr:`101`` precedent (`relay.rst:92`), put
`.. versionchanged:: 2.9.0` with one clause ("a zone with no *client* asks once more at an upstream
shutdown, and takes what is still staged for it") after the `.. include::` at `docs/cli/relay.rst:17`,
with no `:pr:`; `hs-release` adds it. Optionally qualify "holds at most about two rounds of work per
site *client*" (`relay.rst:~110`) with "(one task more per zone with none, at an upstream shutdown)".
`share/` completions: no change (no CLI change). `hs.1` picks up the new text at the next
`hs-release` man rebuild; no man work in this cycle.

## Risks and decisions for the planner

1. **R10 "suites pass unchanged"** against the 3 failing assertions above. They encode the C5-1
   behaviour, and R5 explicitly mandates amending that test's docstring. Record this in PLAN's
   deviation table; no option other than (c)-with-fallback avoids it.
2. **The relay-cycle bound principle** ("never asks with no site client", `spec/relay/TECH.md`
   C4-1 residual) is bent by one task per idle zone at a finish. Accept this and say so in the docs.
3. **A fleet that never connects** keeps the relay and a non-forever upstream open, holding 1 task,
   until a drain or stop. That is today's behaviour for a held zone, but before this change such a
   relay, with a DB, ended and its rows were reverted.
4. **An upstream restarted after a zone heard shutdown** (`finished` persists, pre-existing): the
   probe meets a server that is not finishing, registers demand `(zone, 1)` and may hold 1 row.
   This is bounded and minor.
5. **Another client in the same zone** can lose at most 1 row per idle relay zone to the probe.
6. **Evidence plan**: the laptop two-zone drive against a draining upstream, and the multizone
   integration tests re-run on the head.
