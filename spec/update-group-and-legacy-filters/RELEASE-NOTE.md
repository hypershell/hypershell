# Release note draft — update-group-and-legacy-filters

The squash commit body for `hs-publish` (plain text). Drafted by the P1–P3 build, 2026-11-24.

---

hs update writes only an integer group, and the single-task form refuses the filters it would ignore.

Behaviour change: hs update group=VALUE, and hs update ID group VALUE in the single-task form, now take a group as hs submit -g does: an integer from -2147483648 to 2147483647, stored as that integer (07 and +5 as 7 and 5). Any other value exits with status 2, naming it, before anything is counted, asked or written, and no other change given with it is written, with or without -f. Earlier releases stored text, fractional and out-of-range groups on SQLite, took 1e3 as 1000 and true as 1, and failed on none, null and nan with exit status 5 and a traceback; scripts that set a group with one of those forms now fail.

The single-task form hs update ID FIELD VALUE now refuses an option that selects tasks (-w, -t, -g, -l, -s, --desc, -F, -S, -C, -R, -X, --retries, --signal): it exits with status 2, naming each one given, and writes nothing. Earlier releases ignored those options and wrote the task unconditionally. To write a task only when it matches, use the filtered form: hs update FIELD=VALUE -w "id == ID" -F. The -f option is still accepted.
