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
    Zone this relay serves (default: the default zone). Give it more than once to serve several.

    The relay asks upstream only for tasks of the zones it serves, and keeps one fleet of
    *clients* at the site for each zone (``-N`` *clients* each, unless the zone gives its own
    ``size``). Zones named here take the place of ``relay.zone``; each still takes its own
    settings from ``[relay.zone.NAME]``. See also ``relay.zone``.

``-N``, ``--size`` *NUM*
    Number of *clients* the relay keeps launched at its site (default: 1).

    A *client* that exits is launched again. The size must be at least 1: with no *client* at the
    site the relay would never ask upstream for work. Against an upstream *server* autoscaling with
    relay sites, the size is instead the default ``max`` each zone advertises (see ``relay.max``),
    and *clients* are launched as the published topology asks. See also ``relay.size``.

``--launcher`` *CMD*
    Command prefix used to launch each *client* at the site (default: none).

    With none, *clients* run directly on the relay's host. Otherwise each is launched as
    ``CMD [ARGS...] HS client ...`` (e.g., ``srun``), where *HS* is the relay's own program (see
    ``--remote-exe``). A ``{zone}`` in the launcher or its arguments is replaced by the zone the
    *client* serves, and the launched process has it in ``HYPERSHELL_ZONE``. The *clients* connect back to the relay at its bind address (``--bind``),
    or by its host name when it binds every interface (*0.0.0.0*, the default), unless
    ``--advertise`` names another address for them to dial. The launcher must
    pass the environment through: each *client* receives the site key there
    (``HYPERSHELL_SERVER_AUTH``), never on its command line.
    See also ``relay.launcher``.

``--launcher-args`` *ARGS*
    Extra arguments for the launcher, placed after it (quoted as one argument).

    See also ``relay.launcher_args``.

``--remote-exe`` *PATH*
    Program the *clients* at the site are launched as (default: the ``hs`` that runs the relay).

    By default the relay launches its *clients* as the ``hs`` it was started as, by its full path
    and as the user ran it (a symlinked ``bin/hs`` stays the link), and keeps that program when it
    starts again to bind its database (``--database``). The path assumes the site's nodes see this
    installation where this host does; where they see it at another path, or run another
    installation, name the program here. It is one program, not a command line: a wrapper belongs
    in ``--launcher``. The value is passed on as given, never resolved or checked. It takes
    precedence over ``relay.remote_exe``. See also ``relay.remote_exe``.

``--bind`` *ADDR*
    Bind address of the relay-local *server* (default: 0.0.0.0).

    The *clients* at the site connect to the relay here, so it must be reachable from the
    nodes they run on. ``--advertise`` changes only the address they are told to dial.
    See also ``relay.bind``.

``--advertise`` *ADDR*
    Address the *clients* at the site are told to dial (default: by ``--bind``).

    By default the *clients* of every zone dial the relay at its bind address, or by this host's
    name when it binds every interface (*0.0.0.0*). Give another address when they must use a
    different name, such as one the site's compute nodes resolve to this host while the relay
    listens on every interface. It is passed on as given, never resolved or checked, and must be
    reachable from the nodes the *clients* run on. With TLS verified against the system CA bundle
    (``[server.tls] cafile`` empty) and no ``servername``, it must be a name the relay-local
    *server*'s certificate carries. This is the address the site's *clients* dial, not the client
    bounds a zone advertises upstream. It takes precedence over ``relay.advertise``;
    ``server.advertise`` does not reach a relay. See also ``relay.advertise``.

``--port`` *NUM*
    Port of the relay-local *server* (default: 50002).

    The default differs from the *server* default (50001), so a relay can run beside a
    *server* on the same host. See also ``relay.port``.

``--database`` *PATH*
    Path to the relay database (default: <lib>/relay/<host>.db).

    The default is named for the host, since a home directory is often shared between hosts.
    A node-local disk is a good place for it. See also ``relay.database``.
