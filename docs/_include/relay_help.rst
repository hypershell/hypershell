Options
^^^^^^^

``-H``, ``--upstream-host`` *ADDR*
    Hostname or IP address of the upstream *server* (default: localhost).

    See also ``relay.upstream.host``.

``-p``, ``--upstream-port`` *NUM*
    Port number of the upstream *server* (default: 50001).

    See also ``relay.upstream.port``.

``--no-tls``
    Disable TLS on the connection to the upstream *server* (not recommended).

    This affects only the upstream connection; the relay-local *server* keeps ``[server.tls]``.
    See also ``relay.upstream.tls.enabled``.

``--zone`` *NAME*
    Zone this relay serves (default: the default zone).

    The relay asks upstream only for tasks of this zone, and its *clients* at the site serve it.
    See also ``relay.zone``.

``-N``, ``--size`` *NUM*
    Number of *clients* the relay keeps launched at its site (default: 1).

    A *client* that exits is launched again. The size must be at least 1: with no *client* at the
    site the relay would never ask upstream for work. See also ``relay.size``.

``--launcher`` *CMD*
    Command prefix used to launch each *client* at the site (default: none).

    With none, *clients* run directly on the relay's host. Otherwise each is launched as
    ``CMD [ARGS...] hyper-shell client ...`` (e.g., ``srun``). The *clients* connect back to the
    relay by its host name. The launcher must pass the environment through: each *client*
    receives the site key there (``HYPERSHELL_SERVER_AUTH``), never on its command line.
    See also ``relay.launcher``.

``--launcher-args`` *ARGS*
    Extra arguments for the launcher, placed after it (quoted as one argument).

    See also ``relay.launcher_args``.

``--bind`` *ADDR*
    Bind address of the relay-local *server* (default: 0.0.0.0).

    The *clients* at the site connect to the relay here, so it must be reachable from the
    nodes they run on. See also ``relay.bind``.

``--port`` *NUM*
    Port of the relay-local *server* (default: 50002).

    The default differs from the *server* default (50001), so a relay can run beside a
    *server* on the same host. See also ``relay.port``.

``--database`` *PATH*
    Path to the relay database (default: <lib>/relay/<host>.db).

    The default is named for the host, since a home directory is often shared between hosts.
    A node-local disk is a good place for it. See also ``relay.database``.
