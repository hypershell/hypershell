Run relay for one site.

A *relay* connects a site to an upstream *server*. Upstream it is an ordinary *client*; at
the site it runs its own *server*, scheduling the tasks it receives to *clients* it launches
there, and returns their results upstream.

Upstream, the relay requests work for its zone under an identity of its own, derived from the
relay and the zone name and logged when it starts. It holds at most about two rounds of work for its
site (twice its connected *clients* times its bundle size, ``relay.bundlesize``, which is never
below a *client*'s executor threads), asks for more once half of that is left, and then for the
whole difference at once. It confirms the tasks it receives only once they are stored in the relay
database. The upstream key comes from
``relay.upstream.auth`` (or its ``_env`` and ``_eval`` forms), never from the command line.
The key never reaches the site: before its first *client* is launched, the relay removes from its
own environment, which the site's *clients* and their tasks inherit, every relay setting
(``HYPERSHELL_RELAY_*``) and any variable holding the key. When ``HYPERSHELL_CONFIG_FILE`` names a
file that holds relay settings or the key, the site is given a copy of it instead, without the
``[relay]`` section and without any value holding the key, readable by its owner only, and kept
beside the relay database; the site's *clients* keep the file's other settings, ``[server.tls]``
among them. (If the file cannot be read, the site gets none.) The copy is removed when the relay
exits, however it ends: when it finishes, when it fails, and on SIGINT or SIGTERM. Only a relay
killed outright leaves it behind (by SIGKILL, or by a second SIGTERM while it ends, which ends the
process at once), and the next relay on the same relay database to need a copy writes over it. The relay refuses to start if the *client* command line (launcher, launcher
arguments or template) or a variable exported to tasks (``[export]``) would carry the key.

At the site the relay runs its tasks on the *clients* it launches there, with a key it mints for
each run. Each *client* receives that key in its environment (``HYPERSHELL_SERVER_AUTH``), never on
its command line, and passes it to none of its tasks, so the launcher must pass the environment
through to the *client*. A site *client* that stops sending heartbeats is evicted by the relay
after ``server.evict`` seconds, and the tasks it held are run again at the site. The site never
retries a task: one that fails goes back upstream as it
ended, and the upstream *server* decides whether to run it again. Each result goes back attributed to the *client*
at the site that ran it, matched to the assignment the upstream *server* made. Results are kept in
the relay database until they have been handed over, and for a while after (``relay.retention``).
The relay's heartbeat names its site (``relay.site``, by default the relay's host name) and lists the
*clients* connected at the site, by id and host only. The upstream *server* records them as its own
*clients*, attributed to the site and the relay, and counts them (not the relay) as connected. They
never heartbeat upstream themselves: they disconnect there when they leave the site, and all at once
when the relay finishes or is evicted.
A relay may serve several zones (``--zone`` given more than once, or ``relay.zone`` as a list or as
one ``[relay.zone.NAME]`` table per zone). Upstream it is then one *client* per zone, each under its
own identity, all over the same connection: it takes the zones in turn, and asks for each only as
much as that zone's own *clients* at the site have room for. At the site it keeps one fleet of
*clients* per zone, launched alike but each serving its own zone, with that zone's own launcher,
launcher arguments, size and client bounds if its table gives them; the one relay-local *server*
serves them all. Every launch names its zone to the launcher: ``{zone}`` in the launcher or its
arguments is replaced by the zone's name (``default`` for the default zone), and the launched process
has it in ``HYPERSHELL_ZONE``. Each zone advertises its client bounds upstream; against an upstream
*server* autoscaling with relay sites, each fleet follows the topology it publishes (see
:ref:`Topology <cli_relay_topology>`). A relay that a newer relay at its site supersedes asks for no
more work, launches no *client*, hands back the tasks no *client* has started, lets its *clients*
finish, and exits.
The first zone's identity lists every *client* at the site upstream. Each identity sends its own
heartbeat, so if the relay fails, the upstream *server* evicts each one and puts back what each
held, in every zone.
When the upstream *server* shuts down, the relay finishes the tasks it holds, returns every result,
and ends its upstream registration. It hears of the shutdown in reply to a request for work, in any of
its zones; a zone with no *client* at the site and no task held then ends with the others. A relay
with no *client* at the site in any zone never asks, so it waits until it is drained or stopped.
SIGUSR1 or SIGUSR2, sent to the relay process, drain it the same way: the relay stops asking for work, its site finishes every task the relay holds (those not yet started
too), and the results go back before the registration ends. SIGINT and SIGTERM end the relay at
once, without draining: it ends the *clients* it launched and exits (on SIGTERM, by that signal; a
second SIGTERM ends the process at once). If
the relay fails (a part of its own *server* stops, say, or the upstream *server* refuses it), it ends
the *clients* it launched and exits with a non-zero status. Either way the upstream *server* puts
back the tasks the relay held when it evicts the relay.

A lost upstream connection is not a failure. While the upstream *server* cannot be reached (a call
gets no answer within ``relay.upstream.deadline``), the site keeps running the tasks the relay holds,
their results are kept in the relay database, and the relay connects again with backoff (at most
``relay.upstream.backoff`` seconds apart). It logs the outage when it begins, now and then while it
lasts, and when it ends. Once connected again it sends a heartbeat first (the upstream *server*
registers it again if it had evicted it), then returns the results it holds, and only then asks for
more work. An outage shorter than the upstream eviction period (``relay.upstream.evict_hint``)
costs nothing: the results land. After a longer one, or after the upstream *server* restarted, the
upstream *server* has put those tasks back for other *clients*, so their results are refused as
stale and the tasks run again. A relay that is started while its upstream *server* cannot be reached
waits for it before it starts its site, and a relay restarted on the same relay database returns the
results its earlier run still held. A relay and an upstream *server* that differ on TLS (on at one
end, ``--no-tls`` at the other) would each wait for the other, so the relay checks for this when it
first connects: it exits at once with a non-zero status, before it starts its site, with an error
that names its own TLS setting.

The relay database is always a private SQLite file, whatever database is configured, and the
configured database is never opened. One relay runs per process, and a second relay refuses to
start on the same relay database.
