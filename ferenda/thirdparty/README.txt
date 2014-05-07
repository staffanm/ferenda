This directory contains some smaller python modules that, for one
reason or another, couldn't be directly fetched by setup.py from pypi.

* cssmin by Zachary Voase (https://pypi.python.org/pypi/cssmin/)
  Included since it's setup.py wasn't python3 compatible, otherwise
  the code is identical to cssmin-0.1.4. BSD License.

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
  package isn't on pypi or is python3 compatible. LGPL License.

* rdflib_jsonld by Niklas Lindström
  (https://github.com/RDFLib/rdflib-jsonld/). Included since the
  package isn't installable from pypi. Patched to use a single source
  tree for py2/3 compatibility (no 2to3 translation step needed). BSD
  License.
