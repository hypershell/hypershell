.. _security:

Security
========

|

Overview
--------

*HyperShell* runs arbitrary shell commands on every machine that connects to its task
queue. A *HyperShell* deployment is therefore only ever as secure as the network it operates
on and the authentication material that gates it. This document describes the security
architecture, the cryptographic protections built into the queue transport, and the recommended
deployment patterns for trusted networks, public-internet exposure, and Kubernetes.

The two security primitives users need to understand are:

* a shared **authentication key** that both server and clients must hold to participate in
  the queue, and
* an **end-to-end TLS layer** that protects every byte of queue traffic on the wire.

Both are enabled by default and configured so that the most common deployment scenario — a
single-host ``LocalCluster`` — works out of the box with no operator action required.

-------------------

Architecture
------------

|

Components
^^^^^^^^^^

*HyperShell* is built on Python's :class:`multiprocessing.managers.BaseManager` and
:class:`multiprocessing.JoinableQueue` infrastructure. The runtime decomposes into three
roles:

* **Submit** ingests shell commands from ``stdin`` or files and writes them to the database
  (SQLite or PostgreSQL) with stable UUIDs.

* **Server** pulls pending tasks from the database, bundles them, and exposes four
  ``JoinableQueue`` instances to clients via RPC over TCP:

  * ``scheduled`` — task bundles awaiting execution
  * ``completed`` — results returned from clients
  * ``heartbeat`` — periodic liveness reports from clients
  * ``confirmed`` — explicit acknowledgement of received bundles

* **Client** connects to the server, pulls bundles from the ``scheduled`` queue, spawns
  shell subprocesses, and returns results on the ``completed`` queue.

The ``cluster`` command (``hsx``) composes the three roles in a single process group for
local-only or SSH-distributed workflows.

|

Threat Model
^^^^^^^^^^^^

Because tasks are arbitrary shell commands, the queue must be treated as a privileged
channel:

1. **Arbitrary code execution.** Any party able to enqueue a task can run it on every
   connected client. Connection authentication is therefore the primary security control,
   not an optional convenience.

2. **Data exposure.** Task definitions, exit status, timing, and heartbeat data travel
   through the queue. Task ``stdout`` and ``stderr`` do *not* — they are written to disk
   on the client (locally, or on a shared filesystem if one is configured) and retrieved
   on demand over SFTP via :class:`~hypershell.cluster.ssh.SSHConnection` when the
   operator runs ``hs task info --stdout`` or similar. Without transport encryption, the
   metadata that *does* flow over the queue (including the authentication handshake) is
   readable on the wire.

3. **Active network attacks.** Unencrypted traffic is vulnerable to man-in-the-middle
   modification, command injection, and replay. With encryption but no peer authentication,
   it is vulnerable to active impersonation.

4. **Rogue clients.** A client that holds the auth key can join the queue and pull tasks;
   a stolen key effectively grants full task-execution privileges on every node the client
   can reach.

5. **Denial of service.** Neither connection flooding nor application-level resource
   exhaustion is mitigated by cryptography. The queue transport imposes no handshake timeout,
   frame-size cap, or connection limit of its own (see the Built-in TLS *Limitations* below),
   so DoS resistance must come from firewall and rate-limit controls at the network layer.

The defenses below address (1)–(3) directly and partially address (4). DoS mitigation is
provided at the network perimeter, not by the queue transport.

-------------------

.. _builtin-tls:

Built-in TLS
------------

|

Overview
^^^^^^^^

*HyperShell* speaks TLS natively for its queue transport. There is no external middleware
(``stunnel``, ``nginx``, sidecar) required — the listener, the connect path, and every
per-proxy RPC opened by :class:`multiprocessing.managers.BaseProxy` are wrapped with TLS
automatically when enabled. The implementation lives in :mod:`hypershell.core.tls` and
:mod:`hypershell.core.queue`.

TLS is **enabled by default**. The default configuration is designed so that a fresh install
on a single host can run ``hsx`` without any operator intervention: certificates are
generated on first start, the client trusts the same certificate file via a shared
filesystem path, and the queue proceeds over an encrypted, authenticated channel.

|

How It Works
^^^^^^^^^^^^

A dedicated serializer key, ``'hypershell-tls'``, is registered in
:data:`multiprocessing.managers.listener_client` and points to a TLS-aware listener/client
pair (:class:`hypershell.core.queue.SecureListener` and
:func:`hypershell.core.queue.secure_client`). Whenever a manager is constructed with this
serializer, every byte on the wire — the initial handshake, the BaseManager challenge/
response, every per-proxy RPC, and every server-side accept — transparently routes through
TLS.

The :class:`hypershell.core.queue.SecureManager` class selects this serializer when an active
:class:`hypershell.core.tls.TLSConfig` is supplied. If TLS is disabled (or no configuration
is found), ``SecureManager`` is byte-for-byte equivalent to a stock ``BaseManager`` with
the default ``pickle`` serializer, so cleartext deployments remain supported for
constrained environments.

|

Authentication Key
^^^^^^^^^^^^^^^^^^

Independent of TLS, the queue gates connections with a shared authentication key. The key
is never sent over the queue socket. It is used directly (ASCII-encoded) as the key for the
standard multiprocessing handshake — an HMAC challenge/response with a fresh per-connection
nonce — so the secret itself never crosses the queue transport:

.. code-block:: python

    authkey = config.server.auth.encode('ascii')

An eavesdropper on the queue transport cannot recover the auth string from the handshake and
cannot forge a valid response without it, and the per-connection nonce precludes replay of a
captured exchange. This holds against a passive observer, and against an active attacker only
once the TLS peer has been verified (via ``cafile`` + ``servername`` or a pinned
``fingerprint``). The handshake is not cryptographically bound to the TLS channel, so in
``insecure`` mode — where the peer is not authenticated — an active man-in-the-middle can
relay the challenge/response. Verify the peer, and do not use ``insecure`` mode on untrusted
networks, so the key is exchanged only with the intended server.

For ``LocalCluster``, ``RemoteCluster``, ``SSHCluster``, and autoscaling invocations,
*HyperShell* generates a fresh random key for every invocation via ``secrets.token_hex(64)``,
scoped to that single cluster lifetime; the operator never sees or configures it. For
standalone ``hs server`` / ``hs client`` deployments, the operator supplies the key via
configuration, environment, or the ``--auth`` CLI option.

To keep the shared secret meaningful, ``hs server`` **refuses to start** with the built-in
placeholder key and enforces a minimum key policy: the key must be at least 16 characters and
drawn from ``[A-Za-z0-9._+/=-]`` (which admits hex, URL-safe tokens, and standard Base64). The
key generators used in the deployment guides below satisfy this policy.

.. admonition:: The key is visible on the client command line in cluster launches
    :class: note

    For ``SSHCluster`` and the MPI/SLURM launchers, the per-invocation key is passed to each
    client process as a ``-k`` command-line argument. On multi-user client hosts it is
    therefore visible in the process table (``ps`` / ``/proc``) and scheduler accounting for
    the lifetime of the client, even though it is redacted from *HyperShell*'s own launch
    logs. Treat every host that holds the key as inside the trust boundary. A directly
    configured ``server.auth`` is likewise stored in plaintext in the config file and printed
    verbatim by ``hs config get``; prefer the ``_eval`` / ``_env`` suffixes (see below) to
    keep the secret out of the file.

|

Auto-generated Certificates
^^^^^^^^^^^^^^^^^^^^^^^^^^^

The first time a server starts with TLS enabled and ``cert``/``key`` set to ``'<auto>'``,
*HyperShell* generates a self-signed RSA-3072 certificate covering the local FQDN, the short
hostname, ``localhost``, ``127.0.0.1``, and ``::1`` and writes the materials under the
site library directory:

* **Linux / POSIX:** ``$HOME/.hypershell/lib/tls/server.crt`` and ``server.key``
* **macOS:** ``$HOME/Library/HyperShell/tls/server.crt`` and ``server.key``
* **Windows:** ``%APPDATA%\HyperShell\Library\tls\server.crt`` and ``server.key``

The private key is written with mode ``0600`` (owner-only) and the certificate with mode
``0644``. The SHA-256 fingerprint is logged at ``INFO`` level when the certificate is
generated so the operator can paste it into the client configuration of a remote node or
pin it via ``server.tls.fingerprint``.

Subsequent server starts find the existing materials and reuse them — the operation is
idempotent. Delete the two files to force regeneration (for example, after the hostname
changes).

|

Configuration
^^^^^^^^^^^^^

TLS is configured under the ``[server.tls]`` namespace in the *HyperShell* TOML config:

.. code-block:: toml

    [server.tls]
    enabled     = true              # default
    cert        = "<auto>"          # path to server cert, or '<auto>'
    key         = "<auto>"          # path to server key, or '<auto>'
    cafile      = "<auto>"          # trust anchor; '<auto>' = server's own cert (single-host / shared-FS)
    fingerprint = "<none>"          # 'SHA256:AB:CD:...' pin (overrides cafile verification)
    insecure    = false             # disable peer verification entirely (logs a warning)
    min_version = "TLSv1.2"         # or 'TLSv1.3'
    ciphers     = "<none>"          # OpenSSL cipher string
    servername  = "<none>"          # SNI / hostname check override

Every key is also exposed via environment variables with the standard ``HYPERSHELL_`` prefix
and the same precedence as other settings (CLI > environment > local config > user config >
system config > defaults):

.. code-block:: shell

    HYPERSHELL_SERVER_TLS_ENABLED=true
    HYPERSHELL_SERVER_TLS_CERT=/etc/hypershell/tls/server.crt
    HYPERSHELL_SERVER_TLS_KEY=/etc/hypershell/tls/server.key
    HYPERSHELL_SERVER_TLS_CAFILE=/etc/hypershell/tls/ca.crt
    HYPERSHELL_SERVER_TLS_FINGERPRINT=SHA256:AB:CD:EF:...

|

Peer Verification Modes
^^^^^^^^^^^^^^^^^^^^^^^

The client side of every connection decides how to validate the server certificate. Four
modes are supported, in decreasing strength:

* **CA verification** — set ``cafile`` to a trust anchor file (PEM bundle) **and**
  ``servername`` to the expected hostname. The client validates the chain against that
  anchor (:class:`ssl.CERT_REQUIRED`) and checks that the certificate matches the expected
  name (SNI / SAN check). This is the recommended mode for multi-host deployments with
  operator-managed PKI. ``servername`` is required for server-identity verification, not an
  optional extra: with ``cafile`` set but ``servername`` unset, the client verifies only that
  the certificate chains to the anchor and does **not** check which host presented it, so any
  certificate issued by that anchor — from any host — is accepted. Always set ``servername``
  on untrusted networks.

* **Fingerprint pinning** — set ``fingerprint`` to the server's
  ``SHA256:AB:CD:...`` digest. The client completes the TLS handshake without CA
  validation and rejects the connection if the post-handshake fingerprint does not match.
  Use this when the operator already trusts the server out-of-band and wants minimal
  configuration — typical for self-signed certificates and small clusters.

* **System CA bundle** — leave ``cafile``, ``fingerprint``, and ``insecure`` all unset.
  The client loads the platform's default CA store and requires hostname verification.
  Use this with a real public-CA-issued certificate (e.g., Let's Encrypt).

* **Insecure mode** — set ``insecure = true``. The handshake completes but the peer
  identity is not authenticated. The transport is still encrypted, but with no peer
  verification an active man-in-the-middle can impersonate the server and relay the auth-key
  handshake (which is not bound to the TLS channel). Suitable only for transient local
  debugging on a trusted host; logs a warning on every connection. Do not use it on untrusted
  networks.

When both ``cafile`` and ``fingerprint`` are set, ``fingerprint`` takes precedence and
``cafile`` is ignored.

|

Limitations
^^^^^^^^^^^

Understanding these properties matters most for deployments on untrusted networks.

1. **No mutual TLS.** Clients are not required to present certificates. The shared
   authentication key authenticates the *client* to the server; TLS authenticates the
   *server* to the client. There is no certificate-based client identity.

2. **Pickle-serialized RPC.** The queue is built on Python's multiprocessing managers, whose
   RPC serializes objects with ``pickle`` in both directions. The authentication key and
   (non-``insecure``) TLS peer verification are the controls that gate this channel: a party
   that holds the key and reaches the server — or, in ``insecure`` mode, an active
   man-in-the-middle — can do more than enqueue tasks. Keep the key secret, keep peer
   verification enabled, and treat every host that holds the key as inside the trust boundary.

3. **Self-signed by default; single-leaf trust.** The auto-generated certificate is
   self-signed (not chained to a public CA), valid for ten years, and has no revocation path;
   the private key is written to the site directory unencrypted and owner-only (``0600``), as
   is normal for an unattended service. With ``cafile = '<auto>'`` the client trusts that exact
   certificate as its own anchor, resolved per host — so this works out of the box only on a
   single host or a shared filesystem. A standalone remote client must be given the server's
   certificate or its pinned ``fingerprint`` out of band; otherwise it trusts its own
   auto-generated certificate and the handshake to the real server fails (do not work around
   this with ``insecure`` mode). Rotating the auto-generated certificate requires
   redistributing the new certificate or fingerprint to clients.

4. **No transport-level DoS controls.** The queue sets no per-connection handshake timeout, no
   inbound frame-size cap, and no limit on concurrent connections or worker threads. A peer
   that reaches the listener — including a slow peer that stalls before authenticating — can
   consume server resources, and failed authentication/handshake attempts are handled by the
   underlying multiprocessing layer without surfacing in *HyperShell*'s logs. Denial-of-service
   protection must come from the network layer (see the deployment guides).

5. **IPv4 only.** The built-in TLS queue listener binds ``AF_INET`` (IPv4). On IPv6-only or
   IPv6-preferred networks, clients must reach the server over an IPv4 address.

6. **No pre-shared-key TLS.** PSK would be a natural fit for the shared-secret model but
   is only available in Python 3.13+; *HyperShell* supports 3.9+.

-------------------

Adjacent Trust Surfaces
-----------------------

|

*HyperShell*'s built-in TLS protects the **queue transport** and nothing else. A production
deployment touches at least two additional security boundaries that the *HyperShell*
process is not in a position to enforce: the **task database** and (when used) the
**SSH transport** for distributing clients or fetching task output. Both must be hardened
independently of the queue.

|

The Task Database
^^^^^^^^^^^^^^^^^

The database (SQLite or PostgreSQL) is the canonical source of truth for what tasks exist
and what their command strings are. The server reads pending tasks from the database and
hands them to clients for execution. **Anyone who can write rows into the ``task`` table
can execute arbitrary shell commands on every connected client** — equivalent to root
access on the entire compute fleet if clients run as root, or to user-level access to every
client host otherwise.

.. admonition:: Securing the database is the operator's responsibility
    :class: warning

    *HyperShell* does not authenticate or authorize callers at the database layer. It
    trusts the database connection. If you use PostgreSQL, the security of that PostgreSQL
    instance — network exposure, role/permission model, TLS configuration, backup access
    — is **part of your HyperShell threat surface** and must be treated with the same care
    as the queue itself. A SQL-injection bug in any unrelated application that shares the
    same database, or a leaked PostgreSQL password, is functionally equivalent to a stolen
    *HyperShell* authentication key.

**SQLite.** When ``database.provider = 'sqlite'`` (the default), the database is a local
file under the site library directory. Security reduces to filesystem permissions: the
file should be readable and writable only by the user that runs the *HyperShell* server.
Avoid placing the SQLite file on a network filesystem unless every host that mounts it is
inside the same trust boundary as the server itself.

**PostgreSQL.** When ``database.provider = 'postgres'``, the *HyperShell* server connects
via `psycopg (v3) <https://www.psycopg.org>`_ (the ``postgresql+psycopg`` dialect).
*HyperShell* constructs a standard SQLAlchemy URL and passes it to
:func:`sqlalchemy.create_engine`; everything related to transport security is configured
in that URL (or in ``connect_args``) and is delegated to ``psycopg`` and the underlying
``libpq``. *HyperShell* itself adds nothing.

For production deployments, prefer the ``postgres-system`` or ``postgres-c`` install extra
over ``postgres`` (see :ref:`install`): both link the operating-system ``libpq``/OpenSSL, so
the TLS stack receives OS security updates, whereas the default ``postgres`` extra bundles a
frozen copy inside the wheel.

Concretely, the database does **not** speak TLS unless your URL says so. A bare
``postgresql://user:pass@host/db`` connects in cleartext and accepts the server's
certificate (or lack thereof) without verification. To require TLS and verify the server,
append the standard ``libpq`` query parameters:

.. code-block:: toml

    [database]
    provider      = "postgres"
    database      = "hypershell"
    host          = "db.example.com"
    user          = "hypershell"
    password_eval = "cat /etc/hypershell/pg-pass.key"
    sslmode       = "verify-full"
    sslrootcert   = "/etc/hypershell/pg-ca.crt"


``sslmode`` accepts (in order of strength) ``disable``, ``allow``, ``prefer``, ``require``,
``verify-ca``, and ``verify-full``. Anything below ``verify-full`` is vulnerable to
man-in-the-middle attack against the database connection. The standard ``libpq``
documentation covers the full parameter set:
https://www.postgresql.org/docs/current/libpq-ssl.html.

In addition to transport security, harden the PostgreSQL instance itself:

* Bind PostgreSQL to a non-public interface and restrict access in ``pg_hba.conf`` to the
  hosts that actually run the *HyperShell* server.
* Create a dedicated PostgreSQL role for *HyperShell* with the minimum privileges needed
  to operate on the *HyperShell* schema. Do not reuse the ``postgres`` superuser.
* Rotate the database password on the same cadence as the *HyperShell* authentication
  key.
* Treat database backups with the same sensitivity as the live database — they contain
  the same command strings.

|

SSH for Cluster Distribution and Output Retrieval
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Two *HyperShell* features rely on SSH:

* :class:`~hypershell.cluster.ssh.SSHCluster` distributes client processes to remote hosts
  via ``paramiko``. The host list, key material, and remote shell are all configured
  through the user's standard ``~/.ssh/config``.
* ``hs info <task> --stdout`` / ``--stderr`` fetches the captured task output from the
  client host via SFTP (also ``paramiko``), since output is *not* sent through the queue.

Security for these paths is provided entirely by SSH itself — host key verification, key
rotation, ``authorized_keys`` policy, ``StrictHostKeyChecking``, and so on. *HyperShell*
does not bypass or weaken any of these controls. Treat the SSH configuration that
*HyperShell* uses with the same care you would treat any other SSH-based automation:
prefer key-based authentication, disable password auth, keep ``known_hosts`` populated,
and audit ``authorized_keys`` for unexpected entries.

-------------------

Deployment Guides
-----------------

|

Local Single-Host (Default)
^^^^^^^^^^^^^^^^^^^^^^^^^^^

For ``LocalCluster`` and SSH-distributed workflows on a shared filesystem (the typical
HPC scenario), no operator action is required. The default configuration:

* Generates a self-signed certificate on first server start
* Writes it to the site library directory readable by every process on the host
* Configures the client to trust the same file (``cafile = '<auto>'`` mirrors ``cert``)
* Generates a fresh random authentication key per cluster invocation

The most common command therefore needs no security flags:

.. code-block:: shell

    seq 1000 | hsx -t 'echo {}' -N16

Cross-host workflows over SSH (``hsx --ssh 'a[00-32].cluster'``) inherit the same setup
provided that every host shares the site library directory via NFS, Lustre, GPFS, or
similar. The server writes the certificate once; every SSH-launched client reads it from
the same path.

.. admonition:: When the shared filesystem assumption breaks
    :class: note

    If your cluster does not share ``$HOME/.hypershell/lib`` across nodes, either pin the
    server fingerprint on the clients (see below) or copy the certificate file to each
    node's site library directory before launching clients.

|

Linux Cluster Exposed to the Internet
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

For deployments that accept client connections from outside a trusted network — for
example, a head-node *HyperShell* server reachable over the public internet by remote
workers — the recommended configuration uses operator-managed certificates and a strong,
operator-managed authentication key.

**Step 1 — Generate a strong authentication key.**

.. code-block:: shell

    openssl rand -base64 48 > /etc/hypershell/auth.key
    chmod 600 /etc/hypershell/auth.key
    chown hypershell:hypershell /etc/hypershell/auth.key

Distribute this file securely to every client (e.g., via configuration management, a
secrets store, or out-of-band channel). Never check it into source control.

**Step 2 — Provision a real TLS certificate.**

The cleanest path for a server with a stable hostname is `Let's Encrypt
<https://letsencrypt.org/>`_ via ``certbot``:

.. code-block:: shell

    certbot certonly --standalone -d hypershell.example.com

This produces ``/etc/letsencrypt/live/hypershell.example.com/fullchain.pem`` (cert) and
``privkey.pem`` (key). Configure *HyperShell* to use them:

.. code-block:: toml

    # /etc/hypershell.toml
    [server]
    bind     = "0.0.0.0"
    auth_eval = "cat /etc/hypershell/auth.key"

    [server.tls]
    enabled     = true
    cert        = "/etc/letsencrypt/live/hypershell.example.com/fullchain.pem"
    key         = "/etc/letsencrypt/live/hypershell.example.com/privkey.pem"
    min_version = "TLSv1.3"

.. admonition:: ``_eval`` and ``_env`` suffixes
    :class: note

    Any setting with an ``_eval`` suffix is resolved by shelling out to the named command
    and capturing ``stdout``; ``_env`` reads the named environment variable. These are
    *cmdkit* features available on every configuration key. They let secrets stay outside
    the TOML file. For example, ``auth_eval = "vault read -field=value secret/hypershell"``
    or ``auth_env = "HYPERSHELL_AUTH_VALUE"``.

For environments without internet-reachable HTTP for ACME validation, use an internal CA
(e.g., HashiCorp Vault PKI, smallstep, FreeIPA). Configure the same ``cert``/``key`` paths
to point at the issued material.

**Step 3 — Configure the client to verify the server.**

If clients can resolve and connect to a public hostname covered by the server's cert, leave
``cafile`` unset and let the platform's default CA store handle verification:

.. code-block:: toml

    # client-side config
    [server]
    host      = "hypershell.example.com"
    auth_eval = "cat /etc/hypershell/auth.key"

If you used an internal CA, distribute the CA's root certificate to each client and pin it
explicitly:

.. code-block:: toml

    [server.tls]
    enabled    = true
    cafile     = "/etc/hypershell/internal-ca.crt"
    servername = "hypershell.example.com"   # require SAN match
    min_version = "TLSv1.3"

If you accept a self-signed certificate (acceptable for small operator-managed deployments),
pin the fingerprint instead — the server logs it at ``INFO`` on first start:

.. code-block:: toml

    [server.tls]
    enabled     = true
    fingerprint = "SHA256:AB:CD:EF:01:23:..."

**Step 4 — Restrict network exposure.**

Cryptography is not a substitute for network controls. Combine TLS with:

* A host firewall (``ufw``, ``firewalld``, ``iptables``) restricting the server port to
  known client IP ranges
* A perimeter firewall or VPN tunnel for cross-site connectivity
* `fail2ban <https://www.fail2ban.org/>`_ or equivalent to throttle abusive connection
  attempts
* OS-level rate limits (``iptables -m connlimit``) to bound the connection rate

**Step 5 — Run with least privilege.**

.. code-block:: shell

    useradd --system --shell /usr/sbin/nologin hypershell
    chown -R hypershell:hypershell /var/lib/hypershell /etc/hypershell
    # systemd unit running as User=hypershell, Group=hypershell

Never run the *HyperShell* server as ``root``. Tasks executed by clients run as whichever
user the client process runs as; constrain that user to the minimum privileges required
for the workload.

|

Kubernetes Deployment
^^^^^^^^^^^^^^^^^^^^^

Kubernetes is a natural fit for *HyperShell*: the server runs as a long-lived workload
(``StatefulSet`` if backed by SQLite on a ``PersistentVolume``, ``Deployment`` if backed
by PostgreSQL), and clients run as elastically-scaled ``Deployment`` or ``Job`` workloads.

**Certificate management with cert-manager.**

Use `cert-manager <https://cert-manager.io/>`_ to provision and rotate the server
certificate. A ``Certificate`` resource against an internal ``Issuer`` (or ACME-backed
``ClusterIssuer``) produces a ``Secret`` containing ``tls.crt``, ``tls.key``, and
``ca.crt``:

.. code-block:: yaml

    apiVersion: cert-manager.io/v1
    kind: Certificate
    metadata:
      name: hypershell-server
      namespace: hypershell
    spec:
      secretName: hypershell-server-tls
      issuerRef:
        name: internal-ca
        kind: ClusterIssuer
      commonName: hypershell-server.hypershell.svc.cluster.local
      dnsNames:
        - hypershell-server
        - hypershell-server.hypershell
        - hypershell-server.hypershell.svc.cluster.local
      duration: 2160h    # 90 days
      renewBefore: 360h  # 15 days

**Secret for the authentication key.**

Store the shared auth key as a ``Secret`` rather than embedding it in a manifest:

.. code-block:: shell

    kubectl create secret generic hypershell-auth \
        --namespace hypershell \
        --from-literal=auth="$(openssl rand -base64 48)"

**Server StatefulSet.**

Mount both Secrets into the server pod and expose the TLS configuration via environment
variables:

.. code-block:: yaml

    apiVersion: apps/v1
    kind: StatefulSet
    metadata:
      name: hypershell-server
      namespace: hypershell
    spec:
      serviceName: hypershell-server
      replicas: 1
      selector:
        matchLabels: { app: hypershell-server }
      template:
        metadata:
          labels: { app: hypershell-server }
        spec:
          containers:
            - name: server
              image: hypershell/hypershell:latest
              args: ["hs", "server", "--bind", "0.0.0.0"]
              env:
                - name: HYPERSHELL_SERVER_TLS_ENABLED
                  value: "true"
                - name: HYPERSHELL_SERVER_TLS_CERT
                  value: /tls/tls.crt
                - name: HYPERSHELL_SERVER_TLS_KEY
                  value: /tls/tls.key
                - name: HYPERSHELL_SERVER_TLS_MIN_VERSION
                  value: TLSv1.3
                - name: HYPERSHELL_SERVER_AUTH
                  valueFrom:
                    secretKeyRef: { name: hypershell-auth, key: auth }
              ports:
                - containerPort: 50001
                  name: queue
              volumeMounts:
                - { name: tls, mountPath: /tls, readOnly: true }
                - { name: data, mountPath: /var/lib/hypershell }
          volumes:
            - name: tls
              secret: { secretName: hypershell-server-tls }
      volumeClaimTemplates:
        - metadata: { name: data }
          spec:
            accessModes: [ReadWriteOnce]
            resources: { requests: { storage: 10Gi } }

**Service and NetworkPolicy.**

Expose the server as a ``ClusterIP`` Service and lock down access with a
``NetworkPolicy``:

.. code-block:: yaml

    apiVersion: v1
    kind: Service
    metadata:
      name: hypershell-server
      namespace: hypershell
    spec:
      selector: { app: hypershell-server }
      ports:
        - port: 50001
          targetPort: 50001
          name: queue
    ---
    apiVersion: networking.k8s.io/v1
    kind: NetworkPolicy
    metadata:
      name: hypershell-server-allow-clients
      namespace: hypershell
    spec:
      podSelector:
        matchLabels: { app: hypershell-server }
      policyTypes: [Ingress]
      ingress:
        - from:
            - podSelector:
                matchLabels: { app: hypershell-client }
          ports:
            - { protocol: TCP, port: 50001 }

**Client Deployment.**

Mount the same auth ``Secret`` and the CA from the cert-manager-issued ``Secret``:

.. code-block:: yaml

    apiVersion: apps/v1
    kind: Deployment
    metadata:
      name: hypershell-client
      namespace: hypershell
    spec:
      replicas: 8
      selector:
        matchLabels: { app: hypershell-client }
      template:
        metadata:
          labels: { app: hypershell-client }
        spec:
          containers:
            - name: client
              image: hypershell/hypershell:latest
              args:
                - "hs"
                - "client"
                - "--host"
                - "hypershell-server.hypershell.svc.cluster.local"
              env:
                - name: HYPERSHELL_SERVER_TLS_ENABLED
                  value: "true"
                - name: HYPERSHELL_SERVER_TLS_CAFILE
                  value: /tls/ca.crt
                - name: HYPERSHELL_SERVER_TLS_SERVERNAME
                  value: hypershell-server.hypershell.svc.cluster.local
                - name: HYPERSHELL_SERVER_AUTH
                  valueFrom:
                    secretKeyRef: { name: hypershell-auth, key: auth }
              volumeMounts:
                - { name: tls, mountPath: /tls, readOnly: true }
          volumes:
            - name: tls
              secret: { secretName: hypershell-server-tls }

The client's ``HYPERSHELL_SERVER_TLS_SERVERNAME`` matches the cert's SAN, which makes the
TLS layer reject any pod that happens to attach to the same Service but presents a
different certificate.

.. admonition:: Service mesh as an alternative
    :class: note

    If your cluster already runs a service mesh (Istio, Linkerd) with mTLS for all
    in-cluster traffic, you can rely on the mesh for transport security and disable
    *HyperShell*'s own TLS layer (set ``HYPERSHELL_SERVER_TLS_ENABLED=false``). The shared
    authentication key still gates queue access. This is appropriate when the mesh-provided
    identity model is authoritative and you want to avoid double encryption.

|

External Tunneling (Legacy)
^^^^^^^^^^^^^^^^^^^^^^^^^^^

For sites that pre-date built-in TLS or that already operate a sidecar tunneling layer for
other reasons, *HyperShell* can run over an external tunnel with its own TLS disabled.
This is documented for completeness; new deployments should prefer the built-in transport.

**VPN.** `WireGuard <https://www.wireguard.com/>`_,
`Tailscale <https://tailscale.com/>`_, or
`OpenVPN <https://openvpn.net/>`_ provide a transparent encrypted tunnel between client
and server hosts. *HyperShell* runs as if on a private network; no TLS configuration is
required on the *HyperShell* side, though enabling it adds defense-in-depth at negligible
cost.

**stunnel.** A ``stunnel`` sidecar can wrap a cleartext *HyperShell* connection in TLS:

.. code-block:: ini

    # server-side stunnel.conf
    [hypershell]
    accept  = 0.0.0.0:50001
    connect = 127.0.0.1:50000
    cert    = /path/to/server-cert.pem
    key     = /path/to/server-key.pem

    # client-side stunnel.conf
    [hypershell]
    client  = yes
    accept  = 127.0.0.1:50000
    connect = server.example.com:50001
    CAfile  = /path/to/ca-cert.pem
    verify  = 2

With this layout the *HyperShell* server binds ``localhost:50000`` and ``stunnel`` handles
TLS on port ``50001``. Disable *HyperShell*'s built-in TLS
(``HYPERSHELL_SERVER_TLS_ENABLED=false``) to avoid double-wrapping.

-------------------

Operational Recommendations
---------------------------

|

Independent of the deployment style above:

* **Rotate keys and certificates regularly.** A 90-day rotation cadence is a sensible
  default for both the auth key and the TLS certificate. cert-manager and Let's Encrypt
  handle the certificate side automatically; the auth key requires an operator-driven
  rolling restart of server and clients.

* **Run as an unprivileged user.** The *HyperShell* server itself does not require
  elevated privileges. Tasks run as the user that owns the client process; constrain that
  user accordingly.

* **Enable audit logging.** Set ``logging.style = "detailed"`` (or ``"system"``) and ship
  logs to a central collector. The server logs every client connection, every task
  dispatched, and every completion. See :ref:`logging <logging>` for details. The
  per-invocation auth key is redacted from launch logs, but avoid shipping full configuration
  dumps (which contain a directly-set ``server.auth``) to shared log infrastructure.

* **Sanitize task input.** When generating tasks programmatically, treat the command
  string as a shell-injection vector — escape or template carefully, and never interpolate
  untrusted input into the command. ``hsx`` accepts arbitrary input from ``stdin`` by
  design; the trust boundary is whoever can write to that pipe.

* **Monitor connection patterns.** *HyperShell* does not itself log failed authentication or
  failed TLS handshakes — they are handled by the underlying multiprocessing / TLS layer and
  not surfaced in the application log — so watch for probes and connection surges at the
  network layer (firewall counters, ``fail2ban``, flow logs) rather than expecting them in
  *HyperShell*'s output.

-------------------

References
----------

|

* `Python multiprocessing.managers <https://docs.python.org/3/library/multiprocessing.html#managers>`_
* `Python ssl module <https://docs.python.org/3/library/ssl.html>`_
* `cryptography library <https://cryptography.io/>`_
* `cert-manager <https://cert-manager.io/>`_
* `Let's Encrypt <https://letsencrypt.org/>`_
* `WireGuard <https://www.wireguard.com/>`_
* `Tailscale <https://tailscale.com/>`_
* `stunnel <https://www.stunnel.org/>`_

-------------------

Summary
-------

|

*HyperShell* enables powerful parallel computing but requires careful attention to security
because it executes arbitrary shell commands by design. The two essential controls
*HyperShell* itself provides are the shared **authentication key** that gates the queue
and the **TLS layer** that protects queue traffic on the wire. Both are enabled by default
and self-provisioning on a single host; both are configurable end-to-end for multi-host
deployments through TOML, environment variables, and CLI flags. Beyond the
queue, the **task database** (especially PostgreSQL) and any **SSH transports** used for
client distribution or output retrieval are adjacent trust surfaces that must be hardened
independently.

Recommended practice:

1. **Local single-host workflows** — accept the defaults. Auto-generated self-signed
   certificates and per-invocation auth keys are sufficient.
2. **Multi-host on trusted networks** — accept the defaults if the site library directory
   is shared; otherwise pin the server fingerprint on each client.
3. **Multi-host across untrusted networks** — provision a real certificate (Let's Encrypt
   or an internal CA), distribute a strong shared auth key out of band, restrict the
   server port at the firewall, run as an unprivileged user, and require
   ``sslmode=verify-full`` for any PostgreSQL connection.
4. **Kubernetes** — use cert-manager for certificates, ``Secret`` for the auth key,
   ``NetworkPolicy`` for connectivity, and consider an existing service mesh as an
   alternative transport-security layer.
5. **Database and SSH** — treat the PostgreSQL instance and your SSH key/host-key policy
   as part of the *HyperShell* threat surface. A leaked database password or a permissive
   ``authorized_keys`` defeats every protection in the queue.

For questions or to report security vulnerabilities, please contact the maintainers via
the `GitHub repository <https://github.com/hypershell/hypershell>`_ or join the
`Discord <https://discord.gg/wmv5gyUfkN>`_ community.
