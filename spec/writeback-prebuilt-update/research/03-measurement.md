# 03 — The R1 measurement and the R6 hand-back

## 1. R1: what is measured

Seeded from the shaping microbenchmark (scratch `micro.py`: `Task.update_all([task.to_dict()])`
against `Task.apply_results([task])`, 5,000 results). The cycle's script,
[`../cpu_bench.py`](../cpu_bench.py), changes three things so a builder and a reviewer can rerun it:

- **Extracted trees, one environment.** `git archive <sha> src` for `b819de6` (the 2.8 stand-in the
  GOAL allows), `bc6547a`, and the fix. `pyproject.toml` and `uv.lock` differ between `b819de6` and
  `bc6547a` only in the version, and the fix changes no dependency, so one interpreter and one
  environment serve all three: the child puts the tree's `src/` first on `sys.path` (run with `-I`)
  and refuses to run if `hypershell` imports from anywhere else. Nothing is installed or fetched,
  which also satisfies "one machine, one Python version".
- **The server's own call.** It times `Receiver.update_tasks` on one-result bundles (bundle size 1,
  as in the cluster runs), so each build is measured through its own writeback path: the full-row
  `Task.update_all` on 2.8, `Task.apply_results` on develop and on the fix, "or whatever replaces
  it". Logging is at ERROR, which is the same for every build. One result in a hundred has a
  non-zero exit status. The child checks afterwards that all N rows completed and N/100 failed.
- **Interleaving and process isolation.** Each measurement is a fresh child in its own session
  under a kill timer (default 300 s), with a fresh database. A warm-up round is dropped, then
  rounds rotate the build order. The bar is the fix's mean CPU per result at or below 2.8's.

Run on the base (fix = `HEAD` = `bc6547a`'s code, three rounds, Python 3.14.6, this machine): 2.8
0.377, develop 0.502, "fix" 0.519 ms/result, **bar missed, exit 1**, so the bar can fail. With the
prototype from 01 the fix ran about 20% under 2.8. A first measurement right after start-up read high
(0.476 against 0.396 later), which is why the warm-up round is dropped.

## 2. R6: what the foreman runs

The cluster configuration is fixed by the GOAL: 256 single-executor clients, bundle size 1, TLS,
SQLite, a 16-CPU server node, builds **2.8.3** (tag `2.8.3`, commit `4d5771b`), **develop**
(`bc6547a`) and **the fix** (its final sha), at least three interleaved runs per build. The foreman
owns the harness and the cluster. Build agents never run it and never call a cluster tool.

The hand-back is a section of `EVIDENCE.md` that names those three commits and that configuration.
The foreman returns one JSON block (`config`, `runs` with `label`/`sha`/`order`/`tasks_per_s`,
optional `ruling`), which [`../check_evidence.py`](../check_evidence.py) `r6` checks. It needs the
configuration, at least three interleaved rounds, and the fix's mean at or above 2.8.3's. A
shortfall within the spread passes only with the maintainer's ruling recorded. The script defines
"the runs' spread" as the wider of the two builds' max-minus-min ranges, which the GOAL leaves
unstated; that reading is for the foreman to confirm.
