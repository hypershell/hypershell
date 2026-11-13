# Laptop evidence: zone-aware autoscaling

What was run: the three modes of [`../../drive/zone_drive.sh`](../../drive/zone_drive.sh), each in a
throwaway site, from the repository root:

```sh
for mode in founding fixed dynamic; do
    ZD_EVIDENCE="$PWD/spec/zone-autoscale/evidence/laptop" \
        .agents/factory/bin/temp_site.sh sh "$PWD/spec/zone-autoscale/drive/zone_drive.sh" "$mode"
done
```

Each mode runs one `hs cluster --restart --autoscaling=MODE -T 2 -P 1 -I 1 -X 0 -Y MAX` on the same
machine, launching its clients through [`../../drive/zone_shim.sh`](../../drive/zone_shim.sh), which
records each launch and starts the client. No scheduler and no cluster are involved.

Host: a laptop (macOS, Apple silicon), Python 3.12, the SQLite database of the throwaway site.

Commit: the tree of `feature/zone-autoscale` at the P4 build commit (on top of P3, `44d28d5`), whose
drive and logs are committed together. The logs have this machine's host name, repository path and
site path replaced by `<host>`, `<repo>` and `<site>`.

Each log holds the launch lines (`pid`, the `{zone}` launcher word, `HYPERSHELL_ZONE`, the client's
`--zone`, and the group-0 tasks left when the launch was made), the task lines (zone, the
`HYPERSHELL_ZONE` the task saw, index), and the autoscaler's log lines.

| Mode | Log | Result |
|------|-----|--------|
| founding | [`founding.txt`](founding.txt) | `zone_drive (founding): PASS`: one launch, default zone, no `--zone`, `{zone}` and `HYPERSHELL_ZONE` both `default`; 3 tasks; the client exited on idle. |
| fixed | [`fixed.txt`](fixed.txt) | `zone_drive (fixed): PASS`: one client each for default (no `--zone`), `cpu` and `gpu`; the `gpu` launch made at `g0=0`, after group 0's work was done; 3 tasks per zone, each seeing its own zone; every client exited on idle. |
| dynamic | [`dynamic.txt`](dynamic.txt) | `zone_drive (dynamic): PASS`: default and `gpu` (twelve one-second tasks each) grew to the max-size of 3 on their own pressure, `cpu` (three quick tasks) stayed at one; every `gpu` launch made at `g0=0`; every client exited on idle. |
