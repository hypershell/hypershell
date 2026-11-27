# Research 01 — config values and call paths for sizes and durations

Read-only survey, 2026-11-11. Line numbers are `develop` (branch base) unless marked **2.8.6**.

## 1. Config key behind each option

| Option | Config key (`default=`) | develop | 2.8.6 |
|---|---|---|---|
| `hs submit -m` / `-W` | `task.memory` / `task.timeout` | submit.py:1281-1286 | submit.py:921-926 |
| `hs server -m` / `-W` | `task.memory` / `task.timeout` | server.py:1596-1600 | server.py:1391-1395 |
| `hs client -W` / `-T` | `task.timeout` / `client.timeout` | client.py:1611-1615 | client.py:1541-1545 |
| `hs client -M` | `client.memory` | client.py:1635-1637 | client.py:1565-1567 |
| `hs cluster -m` / `-M` | `task.memory` / `client.memory` | cluster/__init__.py:225-232 | cluster/__init__.py:206-213 |
| `hs cluster -W` / `-T` | `task.timeout` / `client.timeout` | cluster/__init__.py:240-243 | cluster/__init__.py:221-224 |

Defaults are all `0` (core/config.py:117-122, 159-167); each site does `config.X or None`. `hsx` is `hs cluster` (`__init__.py:147-149`).

**Strings go through `type`.** cmdkit 2.7.7 `Interface` (cmdkit/cli.py:50) subclasses `ArgumentParser` and overrides only help, usage, exit and error, not parsing; `Application.from_cmdline` calls `parse_args` (cmdkit/app.py:86). The standard library converts an unused **string** default through `type` (argparse.py 3.14:2329-2338; 3.12:2223). So `[task] memory = "16G"` already reaches `parse_bytes`, and `[task] timeout = "1h30m"` reaches `int` and fails today. After the change, both reach the new parsers with no edit at these sites.

**Ints and floats skip `type`.** A TOML integer is used as is, which is right because it is already in bytes or seconds. A TOML float such as `timeout = 1.5` stays a float. The same holds for environment variables: cmdkit `Environ.expand` (namespace.py:422-448) turns `HYPERSHELL_TASK_TIMEOUT=3600` into int 3600 and `=1.5` into float 1.5, and leaves `=1h` a string that is then parsed. A float is not a regression, since `type=int` never saw it either. It does reach `client_options` as `'-W', '5400.0'`, though (cluster/launch.py:340), and a launched client running the old `type=int` refuses that.

**The error message is lost.** On a `ValueError`, argparse prints `invalid <func.__name__> value: '<v>'` and discards the message (argparse.py 3.12:2554-2558). Only `argparse.ArgumentTypeError` passes its text through (2549-2551). R5 wants an error that lists the accepted forms, so the CLI `type` must raise `ArgumentTypeError`. `data/model.py` and `core/logging.py` call the parser directly and catch `ValueError`. Two ways to serve both: use an exception that subclasses both classes, or wrap the parser in a thin CLI `type` that re-raises. A bad config string fails as `argument -W/--timeout: …` even when the user never typed `-W`, so the message should name the value.

## 2. Direct reads that bypass argparse

- **`config.relay.timeout`** (develop only; relay/__init__.py:918) flows through `site_cluster` → `AutoScalingCluster(client_timeout=…)` (relay/site.py:107, 143). There it is used only in argv (cluster/remote.py:294, 822 → launch.py:338 `str(client_timeout)`). A unit string would arrive as `-T 1h` at the site client, so it would work once `hs client -T` takes units. `[relay] timeout` is not named in the GOAL and is out of scope. No 2.8.6 equivalent.
- **Inline and `--tag` path:** `Task.new` (data/model.py:528-530). Memory is parsed when it is a str. Timeout is not converted (R4, in scope). `smart_coerce` (core/tag.py:35-40) leaves `1h30m` a str, makes `10` an int and `1.5` a float. 2.8.6: model.py:350-352.
- **Blocker for R4:** `split_argline` validates inline tags strictly (model.py:575) before the pop. A value must match `^[A-Za-z0-9_.+-]+$` (model.py:618-620), so `timeout:1:30:00` and `-t timeout:1:30:00` are refused as "Tag value must only contain…" before any duration parser runs. Tag.from_cmdline does split on the first colon only (`split(':', 1)`), so the colon itself survives. 2.8.6 has the same check (model.py:364, regex at 380/400 region). The plan must parse or pop `memory`/`timeout` before validation, or exempt those two keys.
- **`hs update`** (task.py:1083, 1237, 1351) sets `memory=16G` or `timeout=1h` through `smart_coerce` into INTEGER columns with no unit parse. Not in GOAL; note as a follow-up.
- No other `src/` reader of `task.memory`, `task.timeout`, `client.memory` or `client.timeout`. Autoscale, ssh, remote and local cluster modes all take the already-parsed app attributes (cluster/__init__.py:282-288 → local.py:222-254, ssh.py:260-291, remote.py:266-294). `hs run` has no memory or timeout options.

## 3. Forwarding to launched clients

`client_options` (cluster/launch.py:336-340; 2.8.6:334-338) emits `-M str(int)`, `-T str(int)` and `-W str(int)`. A parsed int becomes a bare-digit string, and R6 reads that as bytes or seconds, so it round-trips unchanged. **Requirement:** the parsers must return `int`, never float. `str(5400.0)` is `'5400.0'`, which needs a bare-decimal form. The plan should round, and could also accept a bare decimal (today's `parse_bytes('1.5')` gives 1, so R6 arguably requires it for memory). The launched client re-reads `[task]`/`[client]` config only for options the launch omits. Tests at test_cluster_launch.py:946-955 pin the client option set against 2.9.0a4; the flags are unchanged.

## 4. Consumers assume a number

- Client idle timeout: `timedelta(seconds=timeout)` at client.py:195.
- Task walltime: `min(filter(None, [self.timeout, self.task.timeout]))` against `elapsed.total_seconds()` at client.py:893.
- Backfill: `timedelta(seconds=t.timeout)` and `task.timeout < dt` at client.py:559-568.
- `check_args` `<= 0` at client.py:1713-1716 (2.8.6:1637-1639). Its message says "should be positive integer"; keep or reword.
- Server and submit store the value in the INTEGER `timeout` column (model.py:341, 394).
- `hs info` shows `timedelta(seconds=int(task.timeout))` (task.py:1521).

An int from the new parser is correct everywhere. A string timeout (today's inline `timeout:1h`) would break the `min()` and timedelta calls on the client. R4 fixes that.

## 5. Tests touching the old behaviour

- `tests/test_submit.py:222-239`: inline `memory:2GB`, `'512MB'`, `'2000'`, int 4096, and `timeout:10` (int via smart_coerce). All stay valid under R6. Nothing asserts the leading-match quirks.
- `tests/test_cluster_launch.py:476-494, 516-517, 623-624, 921-922`: argv with `-M 1024 -T 60 -W 30` as bare ints. These stay valid.
- Many integration tests pass `-T 30` and similar (test_dispatch_workload.py:158-245, test_zone_workload.py:79, test_upgrade_cli.py:168, test_cluster_launch.py:1320). Bare ints stay valid.
- No test calls `parse_bytes` or `parse_time` directly, and none asserts a `type=int` error.

## Recommendation

Config-file and environment values already reach the same parsers. argparse runs `type` on a string default, so swapping `type=int` for the duration parser (and `parse_bytes` for the strict one) covers `[task] memory/timeout` and `[client] memory/timeout` with no config-model change, which keeps the cherry-pick clean. Requirements on the parsers:

1. Return `int`.
2. Accept a bare integer, and preferably a bare decimal.
3. Raise `ArgumentTypeError` on the CLI path, so the message listing the accepted forms survives.

TOML and environment numbers pass through unparsed, which is fine. Optionally, document that `[task] timeout = "1h30m"` is accepted. Add one test that sets `HYPERSHELL_TASK_TIMEOUT=1h30m` (or a TOML file) and checks the parsed default.

## 2.8.6 vs develop at these sites

The code is identical in substance and only the line numbers differ. `core/types.py` is unchanged between the two. 2.8.6 has no `relay/`. Its `ensure_valid_tag` has no `strict` parameter (model.py:342, 364), so R4's colon blocker applies there too. `core/logging.py` call sites are 457 and 783 on 2.8.6, against 463 and 1055 on develop. cluster/launch.py differs by 12 lines, none of them in `client_options`. cmdkit pin `>=2.7.7` is the same in both. The 2.8 venv runs Python 3.12, where argparse behaves the same way.
