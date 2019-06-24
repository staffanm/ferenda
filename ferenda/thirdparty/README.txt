
This directory contains some smaller python modules that, for one
reason or another, couldn't be directly fetched by setup.py from pypi.

* rdflib-sqlite by Graham Higgins
  (https://pypi.python.org/pypi/rdflib-sqlite) Included since this
  package has been mothballed by it's author. Code based on latest
  revision in https://github.com/RDFLib/rdflib-sqlite with some bug
  fixes. BSD License.

* patch by Anatoly Techtonik
  (https://code.google.com/p/python-patch/). Included since inclusion
  is the recommended way of using the lib, and the package isn't
  python3 compatible. Code using patches for python3 compatiblity by
  Johannes Berg, taken from
  https://raw.github.com/mcgrof/backports/master/lib/patch.py. MIT
  License. (NOTE: This module will be removed once patchit has been
  determined to be usable)

* patchit by Arthur Skowronek
  (https://github.com/eisensheng/patchit). Included since the released
  version on pypi isn't python3 compatible. MIT License.

* httpheader by Deron Meranda
  (http://deron.meranda.us/python/httpheader/). Included since the
  package isn't on pypi or is python3 compatible (NOTE: rdflib bundles
  a copy of this module as
  rdflib.plugins.parsers.pyRdfa.extras.httpheader, but that version
  has a bug in the one function that we (but not rdflib) uses, so we
  can't use that). LGPL License.

* smc.mw by Marcus Brinkmann
  (https://pypi.python.org/pypi/smc.mw). Included since the released
  0.3 version isn't compatible with recent grako releases, instead
  using a GIT snapshot (4e339f0b82). BSD License.

* coin, part of court, by Niklas Lindström
  (https://code.google.com/p/court/source/checkout?repo=python). Included
  since the package isn't on pypi. BSD License.

* lxml.html.diff, part of lxml (https://lxml.de/). Included with
  modifications to allow better control over exactly how diffing is
  performed. BSD License.
