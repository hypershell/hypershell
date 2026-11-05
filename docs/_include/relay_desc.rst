Run relay for one site.

A *relay* connects a site to an upstream *server*. Upstream it is an ordinary *client*; at
the site it runs its own *server*, scheduling the tasks it receives to *clients* it launches
there, and returns their results upstream.

Upstream, the relay requests work for its zone under an identity of its own, derived from the
relay and the zone name and logged when it starts. It asks only for as many tasks as its site has
room for (its connected *clients* times its bundle size, ``relay.bundlesize``), and confirms the
tasks it receives only once they are stored in the relay database. The upstream key comes from
``relay.upstream.auth`` (or its ``_env`` and ``_eval`` forms), never from the command line.

At the site the relay runs its tasks on the *clients* it launches there, with a key it mints for
each run. The site never retries a task: one that fails goes back upstream as it ended, and the
upstream *server* decides whether to run it again. Each result goes back attributed to the *client*
at the site that ran it, matched to the assignment the upstream *server* made. Results are kept in
the relay database until they have been handed over, and for a while after (``relay.retention``).
When the upstream *server* shuts down, the relay finishes the tasks it holds, returns every result,
and ends its upstream registration; SIGUSR1 or SIGUSR2 stop it asking for more work in the same way.

The relay database is always a private SQLite file, whatever database is configured, and the
configured database is never opened. One relay runs per process, and a second relay refuses to
start on the same relay database.
