Run relay for one site.

A *relay* connects a site to an upstream *server*. Upstream it is an ordinary *client*; at
the site it runs its own *server*, scheduling the tasks it receives to *clients* it launches
there, and returns their results upstream.

The relay database is always a private SQLite file, whatever database is configured, and the
configured database is never opened. One relay runs per process, and a second relay refuses to
start on the same relay database.
