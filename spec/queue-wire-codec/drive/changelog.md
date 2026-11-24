# queue-wire-codec — change-log note for the squash commit body

For `hs-publish` to carry into the PR's squash commit body (PLAN § 2.6). Public text: it describes the
property and the fix, never a way to exploit pickle.

> The queue's messages are now encoded as a closed vocabulary of plain values (JSON), no longer
> pickled: each end of a queue connection decodes what its peer sends only into plain data, refuses
> anything else, and never unpickles a peer's bytes. A message outside the vocabulary closes its
> connection; a server logs one warning naming the peer and keeps serving its other clients, and a
> client stops with a version error (exit status 5). A server refuses in the same way a connection
> whose first request is not one of the manager's public requests, under any interpreter flags. The framing, the authentication handshake and
> TLS are unchanged.
>
> **Incompatible with 2.9.0a9 and earlier, in either direction.** A 2.9.0a10 server, client, relay or
> `hs submit` cannot connect to a peer of 2.9.0a9 or earlier. A newer server logs one warning naming
> the older peer, and that older client, relay or `hs submit` exits at once (status 5) with the
> server's one-line message: run matching server and client versions. A newer client, relay or
> `hs submit` meeting an older server exits at once (status 5) with one error naming the older
> server's queue framing. Run the same version on every host.
>
> A client builds the proxies it calls its server through from its own lists of the queue's
> methods, never from what the server sends: a server that offers a method outside those lists, or
> sends a proxy the client did not ask for, is refused with a version error (exit status 5), and the
> client does not reconnect.
>
> On the Python API, `SecureManager` now accepts only its own two serializers (`hypershell-tls` and
> `hypershell-plain`) and raises `ValueError` for any other, and an error other than `queue.Empty`,
> `queue.Full` or `RemoteError` raised by a remote queue method reaches the caller as `RemoteError`
> naming the error's type and text. The client-side refusals above belong to HyperShell's own queue
> client: code that uses `SecureManager` directly as a client should register each type with an
> explicit proxy type and list of methods.
