# Release note draft — relay-client-exe-deprecated-name

The squash commit body for `hs-publish` (plain text, for GitHub's merge box). The relay fix in one
paragraph, and the Python API default as a behaviour change (GOAL R6, PLAN §2.5).

---

hs relay now launches its site clients as the hs that runs the relay, by its full path and as the user ran it, and keeps that program when it starts again to bind its database (--database). Earlier releases launched every site client as hyper-shell client, so a relay whose site had no hyper-shell on PATH lost its site clients at once and exited. Give another program with hs relay --remote-exe PATH or relay.remote_exe (HYPERSHELL_RELAY_REMOTE_EXE), option over key: the default path assumes the site's nodes see the installation where the relay's host does, and this is the way out when they do not.

Behaviour change: the Python API launches remote clients as hs, not hyper-shell. SSHCluster, RemoteCluster and AutoScalingCluster built without remote_exe, and hypershell.cluster.DEFAULT_REMOTE_EXE, now name hs client. Pass remote_exe='hyper-shell' for the old behaviour. hs cluster is unchanged: it always names its own program.
