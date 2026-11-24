Set configuration option.

The value is written :ref:`by the key's type <config_value_types>`, the rule an environment variable
for the key is read by. A key that takes text holds the value as written, ``true``, ``null``,
``007`` and an empty value included. A whole number, a number or a boolean is converted, a boolean
from ``true``/``false``, ``yes``/``no``, ``on``/``off`` or ``1``/``0`` in any letter case; a value
that does not convert is refused with exit status 2, and nothing is written. A duration or a size
is written as a number when the value is one and as the text otherwise. A numeric key (a whole
number, a number, a duration or a size, a zone key's included) is held to the key's range, given
with each key in the :ref:`parameter reference <config_params>`: a value outside it, or not a
number of the key's kind (``nan``, ``inf``, a boolean), is refused with exit status 2, and nothing
is written. A value inside the range but past the key's advisory limit is written, with a warning.
A key with no type of its own (an entry of ``database.connect_args`` or ``database.pragmas``, a
``[database]`` provider argument, or a path that names no documented key) holds a TOML integer,
float or boolean when the value is exactly one (``5``, ``2.5``, ``true``), and the text otherwise;
an integer outside 64 bits (below -9,223,372,036,854,775,808 or above 9,223,372,036,854,775,807),
which TOML cannot hold, is refused with exit status 2, and nothing is written. An empty value for a
key that takes text is written as ``""`` and overrides the key in a lower file. For an optional text
key, such as ``database.user`` or ``server.bind``, an empty variable would not: it is read as not
given. The file is written as UTF-8, whatever the locale; a path or value that cannot be is refused
with exit status 2, and nothing is written.
