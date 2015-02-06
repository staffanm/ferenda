.. -*- coding: utf-8 -*-

Ferenda is a python library and framework for transforming
unstructured document collections into structured Linked Data. It
helps with downloading documents, parsing them to add explicit
semantic structure and RDF-based metadata, finding relationships
between documents, and publishing the results, including through a
REST-based HTTP API.

.. image:: https://badge.fury.io/py/ferenda.png
   :target: http://badge.fury.io/py/ferenda

.. image:: https://travis-ci.org/staffanm/ferenda.png?branch=master
    :target: http://travis-ci.org/staffanm/ferenda/

.. image:: https://ci.appveyor.com/api/projects/status/aqdo3c39cdof8opa/branch/master
    :target: https://ci.appveyor.com/project/staffanm/ferenda/branch/master

.. image:: https://coveralls.io/repos/staffanm/ferenda/badge.png?branch=master
    :target: https://coveralls.io/r/staffanm/ferenda

.. image:: https://landscape.io/github/staffanm/ferenda/master/landscape.png
   :target: https://landscape.io/github/staffanm/ferenda/master
   :alt: Code Health

.. image:: https://pypip.in/d/ferenda/badge.png
   :target: https://pypi.python.org/pypi/ferenda

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

Some bundled code and other creative works are written by other
authors, included in accordance with their respective licenses:

* `rdflib-sqlite <https://github.com/RDFLib/rdflib-sqlite>`_ by Graham
  Higgins, BSD
* `patch <https://code.google.com/p/python-patch/>`_ by Anatoly
  Techtonik, MIT
* `Grit XSLT stylesheets <http://code.google.com/p/oort/wiki/Grit>`_
  and `RDL service UI
  <https://github.com/rinfo/rdl/tree/master/packages/java/rinfo-service/src/main/webapp/ui>`_
  by Niklas Lindstrom, BSD
* `httpheader <http://deron.meranda.us/python/httpheader/>`_ by Deron
  Meranda, LGPL
* `smc.mw <https://pypi.python.org/pypi/smc.mw>`_ by Marcus Brinkmann, BSD
* `normalize.css <http://git.io/normalize>`_, MIT
* `responsive template <http://verekia.com/initializr/responsive-template>`_, PD
* `jquery <http://jquery.com>`_ , MIT
* `modernizr <http://modernizr.com>`_, MIT
* `respond.js <http://github.com/scottjehl/Respond>`_, MIT/GPL
* `Gentleface wireframe toolbar icons
  <http://gentleface.com/free_icon_set.html>`_, CC-BY-NC
  
 
