# R8 outcome

outcome: b
seed: issues/relay-shutdown-tail.md

Ten laptop runs (`evidence/laptop/`, budget 10 of 10), five diagnostic on `475334c` and five post-fix.
The tail is the sum of the chain's designed waits; two fixes landed (the relay's returner sends at once
when the site holds no unfinished task; the upstream shutdown signal no longer waits out the heart
monitor's 2 s read). Two causes remain and are seeded: the site clients' result bundle wait
(`relay.bundlewait` doubling as theirs), and the upstream scheduler not woken by a landed result. No
cluster job ran (plan-gate ruling 3); `evidence/cluster/` was not produced. See TECH.md P8.
