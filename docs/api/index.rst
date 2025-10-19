.. _library:

Library
=======

`HyperShell` allows for embedding within an existing Python project.
All components are built as concurrent threads that are created and waited upon.
We provide high-level functions with the same signature to make it easy to construct
and wait on these threads.

-------------------

Server
------

|

Embed the :class:`~hypershell.server.ServerThread` within an application to host the
scheduler and serve task bundles to clients. This thread implicitly creates the
:class:`~hypershell.submit.SubmitThread` for you, though you can run the server without
it and run submit flows elsewhere.

.. admonition:: Run server forever
    :class: note

    .. code-block:: python

        from hypershell.server import ServerThread
        ...

        server = ServerThread.new(forever_mode=True, address=('0.0.0.0', 54321),
                                  auth='my-secret-key', max_retries=2, eager=True,
                                  evict_after=600)
        ...
        server.join()

If all we need is to run the server without managing other activity, we can invoke
the :meth:`~hypershell.server.serve_forever` function and the thread creation and
join will be managed together.


.. admonition:: Run server forever
    :class: note

    .. code-block:: python

        from hypershell.server import serve_forever
        ...

        serve_forever(address=('0.0.0.0', 54321), auth='my-secret-key',
                      max_retries=2, eager=True, evict_after=600)

The :meth:`~hypershell.server.serve_forever` function is similar to the
:meth:`~hypershell.server.serve_from` function but ``source`` is set to ``None``
and ``forever_mode`` is ``True``. There is a third :meth:`~hypershell.server.serve_file` function
which simply wraps :meth:`~hypershell.server.serve_from` function by opening the named file
within a *context manager* and feeding the iterable file descriptor into it.

-------------------

Client
------

|

Embed the :class:`~hypershell.client.ClientThread` within an application to connect to
the running server and process tasks locally. The client will stay connected and continue
processing tasks until either the server sends the disconnect signal or the client timeout
limit has been reached.

.. admonition:: Run client as dedicated thread
    :class: note

    .. code-block:: python

        from hypershell.client import ClientThread
        ...

        client = ClientThread.new(num_threads=16, address=('my.server.univ.edu', 54321),
                                  auth='my-secret-key', client_timeout=600)
        ...
        client.join()


If all we need is to run the client without managing other activity, we can invoke
the :meth:`~hypershell.client.run_client` function and the thread creation and
join will be managed together.

.. admonition:: Run client as a function
    :class: note

    .. code-block:: python

        from hypershell.client import run_client
        run_client(num_threads=16, address=('my.server.univ.edu', 54321),
                   auth='my-secret-key', client_timeout=600)

-------------------

API Reference
-------------

|

.. toctree::
    :maxdepth: 1

    submit
    server
    client
    cluster
    data
