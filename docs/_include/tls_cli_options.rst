``--no-tls``
    Disable TLS on the queue interface (not recommended).

    Transport-layer security is enabled by default. When disabled, task bundles and
    results travel the queue connection unencrypted; only do this on a trusted, isolated
    network or when transport security is already provided by an external tunnel.

    See the `security` section.

``--tls-cert`` *PATH*
    Path to the TLS certificate file (default: <auto>).

    With the default ``<auto>`` a self-signed certificate is generated once on first
    server start and stored under the site ``lib`` directory. Provide a *PATH* to use
    your own certificate instead.

``--tls-key`` *PATH*
    Path to the TLS private key file (default: <auto>).

    Paired with ``--tls-cert``. With the default ``<auto>`` the auto-generated key is
    used; a user-provided key file should be readable only by its owner.

``--tls-ca`` *PATH*
    Path to the TLS CA certificate file used to verify the peer (default: <auto>).

    With the default ``<auto>`` the server certificate is trusted directly, which suits
    a single host or shared filesystem. Provide a *PATH* to a certificate authority to
    verify peers across multiple hosts.

    See the `security` section for peer verification modes.
