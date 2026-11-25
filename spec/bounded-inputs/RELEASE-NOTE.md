Every numeric setting has one range, one parser and one error

Every numeric option, configuration key and `HYPERSHELL_*` variable now has a declared range,
listed with each option on its command's page and with each key in the parameter reference. One
parser per kind (whole number, number, duration, size) reads a value from the command line, a file
and a variable alike. A value outside its range, or not a number of its kind, stops the command
before it starts work, with one line naming the setting, the range and the value, and where it
came from:

    -c/--cores: expected a whole number from 0 (unconstrained) to 32,767, given '100000'
    server.evict: expected a duration from 10 s to 2,147,483,647 s, given 5 (from: /path/config.toml)

A value inside its range behaves exactly as before, in every form taken before (units, the Slurm
time forms, a period under one second read as one, a negative start delay read as random).

Behaviour changes:

- Out-of-range numbers are refused. An option exits 2; a configuration value, from a file or a
  variable, exits 3. Values accepted before and now refused include `-b 0`, `-l` below 1, `-c`
  above 32,767, any duration over 2,147,483,647 seconds (about 68 years), `-m` of 2^63 bytes or
  more, a negative rate, and a factor of 0, below 0, `nan` or `inf`.
- The whole configuration is checked when it loads, for every command, so a bad value anywhere in
  a file stops `hs list` as well as `hs server`. A numeric key's `_env` or `_eval` form that cannot
  resolve (a variable that is not set, a command that fails) stops every command the same way.
  `hs config` still runs on a file holding a bad value or such a form, so it can be shown and set
  right; a bad variable still stops it.
- `server.evict` has a lower cap, 2,147,483,647 seconds instead of 86,399,999,913,600, and a
  refused evict period exits 3 instead of 5. The same cap now applies to `evict_after` given to
  the Python API's heartbeat monitor.
- Configuration refusals exit 3. `hs cluster`'s refusal of a file's `autoscale.period` or
  `client.ratelimit` exited 2. `hs client` now refuses a `client.ratelimit` under the floor.
- A file's numeric key may be text: `attempts = "3"` reads as 3, as the variable does, instead of
  failing every command at import.
- `hs config set` refuses a value outside its key's range, and any integer TOML cannot hold
  (outside 64 bits), exit 2, writing nothing.
- The resource tags `cores`, `memory` and `timeout` take the ranges of `-c`, `-m` and `-W`, from an
  inline tag, `-t` or a `--from-json` record. A file, standard input read in full or a JSON source
  holding a bad value is refused whole (exit 5, nothing submitted, naming the value and its line or
  record); on a stream only that line is refused, with an error; `-t` exits 2, on `hs submit` and
  `hs run` alike, before anything is written. A tag is read as its option reads it, except that a
  `cores` tag with a zero fraction (`cores:4.0`) still reads as that whole number (`cores:4.5` is
  refused), and a `timeout` tag's fraction of a second rounds up to whole seconds
  (`timeout:90.5` is 91), as `-W` rounds it; before, a bare decimal was stored as written. A
  `--from-json` record's `cores`, `memory` and `timeout`, a JSON number or a string, are stored as
  whole numbers read the same way: `"timeout": 90.5` stores 91 and `"memory": 1.5` stores 1, where
  before the decimal was stored as given; `"cores": "4.0"` stores 4 on PostgreSQL too, where before
  it failed the submission. A template still expands a record's field as written.
- Some settings warn past an advisory limit and run anyway: more than 1,024 threads, more than 100
  attempts (`-r` above 99), more than 1,024 cores, a scaling factor above 100, a scaling period
  above an hour (3,600 seconds), and a rate below 0.001 tasks per second other than 0. One warning
  per setting per run; one summary per submission for resource tags, a single task given as
  arguments included. The exit status is unchanged.
- A numeric key given by `_eval` (or `_EVAL`) runs its command once per process, when the
  configuration loads, instead of at every read.

Range changes beside the table:

- `-N/--num-threads` keeps 0, which runs one thread per core, as documented.
- `-n/--interval` of `hs wait` and `hs run` takes at least 1 second; 0 queried the database
  without rest.
- `server.tls.handshake` keeps its range of 1 second to one day.
- `hs client -T/--timeout` and `-W/--task-timeout` keep a floor of 1 second, as before; the keys
  `client.timeout` and `task.timeout`, and `hs cluster`'s options, still take 0 for none.
