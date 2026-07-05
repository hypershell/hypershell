*HyperShell* runs arbitrary shell commands on every host that connects to its task queue, so the
queue transport is security-sensitive. Two controls are enabled by default: transport-layer TLS
and a shared authentication key. The full model, threat analysis, and deployment guides are in
the :ref:`security` section.

On a single host or a shared filesystem no action is required. The first time a server starts
with TLS enabled it generates a self-signed certificate and private key under ``<site>/lib/tls``
(private key mode ``0600``) and reuses them thereafter. The managed ``cluster``, the ``ssh``
cluster, and autoscaling launchers each generate a fresh one-time authentication key per
invocation, which the operator never configures.

Standalone ``hs server`` and ``hs client`` instead require an explicit key, supplied with
``-k``/``--auth``, the ``HYPERSHELL_SERVER_AUTH`` environment variable, or ``server.auth`` in the
configuration. The server refuses to start with the built-in placeholder key and requires a key of
at least 16 characters drawn from ``[A-Za-z0-9._+/=-]``. The key is never transmitted over the
queue socket; even so, treat every host that holds it as inside the trust boundary.

Disable TLS per invocation with ``--no-tls``, or permanently with ``server.tls.enabled = false``
(not recommended; a warning is logged). The client verifies the server certificate in one of four
modes, in decreasing order of strength:

* **CA verification** — set ``server.tls.cafile`` to a PEM trust anchor **and**
  ``server.tls.servername`` to the expected hostname (recommended for multi-host deployments).
* **Fingerprint pinning** — set ``server.tls.fingerprint`` to the server's ``SHA256:...`` digest.
* **System CA** — leave ``cafile``, ``fingerprint``, and ``insecure`` unset to use the platform
  trust store with a public-CA-issued certificate.
* **Insecure** — ``server.tls.insecure = true`` encrypts the connection but does not authenticate
  the peer; suitable only for transient local debugging on a trusted host.

Across multiple hosts without a shared filesystem, distribute the server's certificate — or its
pinned fingerprint — to each client out of band, and never the private key.

*HyperShell*'s built-in TLS protects only the queue transport. Secure the task database connection
independently (for example, PostgreSQL ``sslmode``), and rely on SSH host-key verification for
``ssh`` clusters. See the :ref:`security` section for database, internet-facing, and Kubernetes
deployment guides.
