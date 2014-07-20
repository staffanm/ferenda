This directory contains some smaller python modules that, for one
reason or another, couldn't be directly fetched by setup.py from pypi.

* rdflib-sqlite by Graham Higgins
  (https://pypi.python.org/pypi/rdflib-sqlite) Included since this
  package has been mothballed by it's author. Code based on latest
  revision in https://github.com/RDFLib/rdflib-sqlite with some bug
  fixes. BSD License.

* patch by Anatoly Techtonik
  (https://code.google.com/p/python-patch/). Included since the
  package isn't on pypi or is python3 compatible. Code using patches
  for python3 compatiblity by Johannes Berg, taken from
  https://raw.github.com/mcgrof/backports/master/lib/patch.py. MIT License.

* httpheader by Deron Meranda
  (http://deron.meranda.us/python/httpheader/). Included since the
  package isn't on pypi or is python3 compatible (NOTE: rdflib bundles
  a copy of this module as
  rdflib.plugins.parsers.pyRdfa.extras.httpheader, but that version
  has a bug in the one function that we (but not rdflib) uses, so we
  can't use that). LGPL License.
