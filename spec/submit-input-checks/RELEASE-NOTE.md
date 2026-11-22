# Release note draft — submit-input-checks

The squash commit body for `hs-publish` (plain text, for GitHub's merge box). It names the four
changes in GOAL.md's *User-visible changes* as behaviour changes, plus the `--template` line split and
the tag-only line fix.

---

Behaviour change: a # is now part of a command. A # anywhere after a line's first non-blank character, including in quotes, a URL fragment or $#, is submitted and run as written by hs submit, hs cluster and hs server; earlier releases cut the command at the first # without a word, so echo 'issue #42' ran as echo 'issue. A line that begins with # is still a comment, # HYPERSHELL: still sets tags wherever it appears on a line (break it with shell quoting, echo '#'' HYPERSHELL:', to run that text), and a trailing # note now stays in the command for the shell to ignore. To audit task files an earlier release may have cut, look for lines with a # after their first character that are not # HYPERSHELL: markers and compare their stored commands with hs list args.

Behaviour change: such a line gets a new task identity, so --restart or --update of a file first submitted by an earlier release submits those lines again, and they run again.

Behaviour change: a task group must be an integer. A group from an inline tag, -t/--tag or a --from-json record that is anything else (x, 1.5, 1e3, true, nan, a JSON string such as "7", or a value outside the 32-bit range) is refused: a task file, JSON source, arguments or hs submit standard input is refused whole with nothing submitted, exit status 5, naming the value and its line or record; -t group:x and -g 3000000000 exit 2; on a stream into hs cluster, hs server or hs submit --queue only the offending line is refused, with an error naming it. Earlier releases stored such values as given on SQLite and ran them in an undocumented order, and group:nan hung the run.

Behaviour change: hs submit refuses a task below the reached group, the highest group the current run of the database has started. The whole submission is refused, exit status 2, naming both groups, and hs submit with a database now reads standard input in full before submitting anything. A submission that names no group takes the reached group when the most recently scheduled group is below it, so it is never refused. hs cluster FILE and hs server FILE refuse such a file the same way on a database with a reached group; tasks the re-submission gate skips as already present, and tasks left in flight by an interrupted run, do not count, so a same-file --restart still runs. Streams into hs cluster and hs server are unchanged: a late lower group runs, with a warning.

hs submit --template now splits each line before applying the template, so comment, blank and tag-only lines are no longer submitted as tasks, and a # HYPERSHELL: inside the template text is no longer read as tags. A tag-only # HYPERSHELL: line in a task file given to hs cluster or hs server no longer fails the run.
