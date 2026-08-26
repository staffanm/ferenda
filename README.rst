Ferenda
========

Ferenda downloads, parses, connects, and publishes large document
repositories. It powers the Swedish legal information service lagen.nu.

Each source owns its download, parse, typed model, and JSON artifact pipeline.
Shared libraries provide citation parsing, storage layout, rendering, search,
and build orchestration. The JSON artifacts are authoritative. SQLite and the
search index are derived and can be rebuilt.

Requirements
------------

Ferenda requires Python 3.14 or later and `uv <https://docs.astral.sh/uv/>`_.
Some Word inputs also require Java, Apache POI, and ``antiword``.

Quick start
-----------

::

    uv sync
    ./tools/fetch_poi.sh
    uv run pytest

Run ``uv run lagen --help`` for the pipeline command line.

Documentation
-------------

* `Development guide <docs/developing/README.md>`_
* `Documentation index <docs/README.md>`_
* `Development guide <docs/developing/README.md>`_
* `Operations guide <docs/operating/README.md>`_
* `API and data guide <docs/api/README.md>`_

License
-------

Ferenda uses the 2-clause BSD license. See `LICENSE.txt <LICENSE.txt>`_.
