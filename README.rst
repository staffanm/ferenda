.. -*- coding: utf-8 -*-

Ferenda is a python library and framework for transforming
unstructured document collections into structured Linked Data. It
helps with downloading documents, parsing them to add explicit
semantic structure and RDF-based metadata, finding relationships
between documents, and publishing the results, including through a
REST-based HTTP API.


Quick start
-----------

This example uses ferenda's project framework to download the 50
latest RFCs and W3C standards, parse documents into structured,
RDF-enabled XHTML documents, loads all RDF metadata into a triplestore
and generates a web site of static HTML5 files that are usable
offline::

    pip install ferenda
    ferenda-setup myproject
    cd myproject
    ./ferenda-build.py ferenda.sources.tech.RFC enable
    ./ferenda-build.py ferenda.sources.tech.W3Standards enable
    ./ferenda-build.py all all --downloadmax=50 --staticsite --fulltextindex=False
    open data/index.html

The same functionality can also be accessed through a python API, if
you want to use ferenda as part of a larger system. It's also possible
to just use the parts of ferenda that you need (eg. only the
downloading and parsing features).

More information
----------------

See http://ferenda.readthedocs.org/ for in-depth documentation.

Copyright and license
---------------------

Most of the code written by Staffan Malmgren, licensed under the main
2-clause BSD license.

Some bundled code are written by other authors, included in accordance
with their respective licenses:

* `rdflib-sqlite <https://github.com/RDFLib/rdflib-sqlite>`_ by Graham
  Higgins, BSD
* `patch <https://code.google.com/p/python-patch/>`_ by Anatoly
  Techtonik, MIT
* `Grit XSLT stylesheets <http://code.google.com/p/oort/wiki/Grit>`_,
  `RDL service UI
  <https://github.com/rinfo/rdl/tree/master/packages/java/rinfo-service/src/main/webapp/ui>`_
  and `coin.py
  <https://code.google.com/p/court/source/checkout?repo=python>`_ by
  Niklas Lindstrom, BSD
* `httpheader <http://deron.meranda.us/python/httpheader/>`_ by Deron
  Meranda, LGPL
* `smc.mw <https://pypi.python.org/pypi/smc.mw>`_ by Marcus Brinkmann, BSD


