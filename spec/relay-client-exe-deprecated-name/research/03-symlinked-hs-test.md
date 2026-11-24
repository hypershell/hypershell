# 03 — A test that runs a client from a symlinked `bin/hs` with no `hyper-shell` on `PATH`

Scope: R5. What the suite already has, and what the new test needs. Read on `develop` `11faef5e`.

## What exists

- `tests/test_relay_db.py::relays` starts relays as `[sys.executable, tmp/hs, 'relay', …]`, where
  `tmp/hs` is a non-executable `RECORDER` script calling `main(sys.argv[1:])`, with
  `HYPERSHELL_RELAY_LAUNCHER` set to `true` (no site client) and the upstream a stand-in
  `QueueServer`. It never runs a site client; it cannot complete a task.
- `tests/test_relay_launch.py::recording_launcher` records each launch's argv and environment and
  exits. Used by `test_relay_advertise.py` for option/key precedence; good for R3's precedence test,
  not for R5's "a client starts and completes a task".
- `spec/relay-advertise/drive/advertise_drive.sh healthy` is the only end-to-end relay run with real
  site clients: `hs submit t.in`, an upstream `hs server --forever --no-tls -H 127.0.0.1`, a relay
  whose launcher execs the client, and `hs list --remaining --count` polled to 0. It puts the venv's
  `bin` on `PATH` precisely because the relay launches `hyper-shell client` (its own comment).
- The relay logs every launch once per zone at DEBUG:
  `Autoscale launcher (zone NAME): <shell-quoted argv>` (`cluster/launch.py::LaunchActuator.describe`).

## The new test (pytest, `@mark.integration`)

An installation stand-in: `tmp_path/inst/bin/hs` and `…/hsx`, symlinks to the test interpreter's
own scripts (`os.path.join(sysconfig.get_path('scripts'), 'hs')`, whose shebang is the venv's absolute
`python`). No `hyper-shell` in that directory. Every process runs with
`PATH=<inst/bin>:/usr/bin:/bin`; the test asserts `shutil.which('hyper-shell', path=PATH) is None`
first, so a stray system install cannot mask the defect. `HYPERSHELL_SITE` is a fresh site under
`tmp_path`, `HYPERSHELL_CONFIG_FILE` empty, `HYPERSHELL_DATABASE_FILE` dropped.

1. `inst/bin/hs submit t.in` (three `echo` tasks) into the site's SQLite database.
2. Upstream: `inst/bin/hs server --forever --no-tls -H 127.0.0.1 -p PORT`, key in
   `HYPERSHELL_SERVER_AUTH` (never on the command line).
3. Relay: `inst/bin/hs relay -H 127.0.0.1 -p PORT --no-tls --bind 127.0.0.1 --port FREE -N 1`
   (`[--database tmp/relay.db]` for the second case), key in `HYPERSHELL_RELAY_UPSTREAM_AUTH`,
   `HYPERSHELL_LOGGING_LEVEL=DEBUG`, its own process group. No launcher: the client runs on this
   host, as the program the relay names.
4. Poll `inst/bin/hs list --remaining --count` to `0` within a bound, failing early if the relay
   exits.
5. Assert the relay's log holds `Autoscale launcher (zone default): <inst/bin/hs> client ` (the
   symlink path, not its target), never `hyper-shell`; for the `--database` case also
   `Re-executing to bind relay database` (the re-execution really happened).
6. SIGUSR1 drains the relay (exit 0); the server and every process group are ended in `finally`.

On `develop` both cases fail at step 4 by assertion (`relay.poll() is None`): the relay logs
`hyper-shell client …`, the `SiteScaler` dies on `FileNotFoundError`, and the relay exits
(research/01). With the scratch prototype both pass, 4.6 s and 4.7 s (plan-time dry-run).

A `relay.remote_exe` and `--remote-exe` test (R5's second sentence) uses the `relays` fixture with
`recording_launcher`: the recorded argv's word after the launcher is the key's value, then the
option's over the key's, and with `--database` (re-executed) the recorded environment holds no
`_HYPERSHELL_RELAY_PROGRAM`.

## Hazards

- Bind only `127.0.0.1`. Run serially (`pytest -n auto` is broken on this suite).
- macOS `tmp_path` lives under `$TMPDIR`; the logged path is `shlex.quote`d, so compare against
  `shlex.quote(str(path))`.
- The sandbox hides other processes from `ps`; end what the test starts by its own process groups.
