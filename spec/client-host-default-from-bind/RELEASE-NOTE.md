# Release note draft — client-host-default-from-bind

The squash commit body for `hs-publish` (plain text, for GitHub's merge box). The fix and the rule in
one paragraph, then who it changes things for (GOAL R8, PLAN §2.5).

---

hs client started without -H now dials server.host when a configuration file or HYPERSHELL_SERVER_HOST gives it, so one shared configuration serves a server bound to 0.0.0.0 and its clients on other nodes. Earlier releases dialled server.bind, so such a client dialled its own node and was refused. When server.host is not given, a client dials server.advertise if given, else server.bind when it is one specific address, else localhost. hs submit --queue and QueueConfig.load() follow the same rule. A host given as localhost counts as given.

Behaviour change: a configuration that gives server.host now has hs client dial it, not server.bind. One that gives server.advertise but not server.host now has hs client, hs submit --queue and QueueConfig.load() dial server.advertise. One that gives only a specific server.bind now has hs submit --queue and QueueConfig.load() dial it, where they dialled localhost; hs client is unchanged there. With 0.0.0.0 bound and neither key given, hs client dials localhost, not 0.0.0.0 (the same host).

Unchanged: -H still wins, and the Python API defaults stay localhost. server.advertise keeps its meaning for the clients hs cluster launches, and a relay's site clients still use relay.advertise.

The address is now settled when the command that dials starts, so a server.host_eval command no longer runs at the start of every hs command, only of hs client and hs submit --queue. A server.host_env or server.advertise_env that names a variable that is not set is refused when that command starts, naming the key.
