Run relay for one site.

A *relay* connects a site to an upstream *server*. Upstream it is an ordinary *client*; at
the site it runs its own *server*, scheduling the tasks it receives to *clients* it launches
there, and returns their results upstream.

Upstream, the relay requests work for its zone under an identity of its own, derived from the
relay and the zone name and logged when it starts. It asks only for as many tasks as its site has
room for (its connected *clients* times its bundle size, ``relay.bundlesize``), and confirms the
tasks it receives only once they are stored in the relay database. The upstream key comes from
``relay.upstream.auth`` (or its ``_env`` and ``_eval`` forms), never from the command line.

The relay database is always a private SQLite file, whatever database is configured, and the
configured database is never opened. One relay runs per process, and a second relay refuses to
start on the same relay database.
