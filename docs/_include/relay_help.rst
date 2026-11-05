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

``--bind`` *ADDR*
    Bind address of the relay-local *server* (default: 0.0.0.0).

    The *clients* at the site connect to the relay here, so it must be reachable from the
    nodes they run on.

``--port`` *NUM*
    Port of the relay-local *server* (default: 50002).

    The default differs from the *server* default (50001), so a relay can run beside a
    *server* on the same host.

``--database`` *PATH*
    Path to the relay database (default: <lib>/relay/<host>.db).

    The default is named for the host, since a home directory is often shared between hosts.
    A node-local disk is a good place for it. See also ``relay.database``.
