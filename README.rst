Ferenda is a python library and framework to scrape and convert
unstructured content into semantically-marked-up, Linked Data-enabled
content. It is focused on documents, not individual pieces of data,
but is useful for any kind of information that can be described in
terms of resources (documents, people, places, events, etc...). It
uses RDF for all metadata.

Quick start
-----------

This uses ferenda's project framework to download the 50 latest RFCs 
and W3C standards, parse documents into structured, RDF-enabled XHTML 
documents, loads all RDF metadata into a triplestore and generates a 
web site of static HTML5 files that are usable offline::

    export STATIC_DEPS=true     # if using python 3.3 on Mac OS
    pip install --extra-index-url https://testpypi.python.org/pypi ferenda
    ferenda-setup myproject
    cd myproject
    ./ferenda-build.py ferenda.sources.tech.RFC enable
    ./ferenda-build.py ferenda.sources.tech.W3Standards enable
    ./ferenda-build.py all all --downloadmax=50 --staticsite --fulltextindex=False
    open data/index.html

More information
----------------

See http://ferenda.readthedocs.org/ for in-depth documentation.

Copyright and license
---------------------

Most of the code written by Staffan Malmgren, licensed under the main
2-clause BSD.

Some bundled code is written by other authors, included in accordance
with their respective licenses:

* cssmin by Zachary Voase (https://pypi.python.org/pypi/cssmin/), BSD
* rdflib-sqlite by Graham Higgins
  (https://github.com/RDFLib/rdflib-sqlite), BSD
* patch by Anatoly Techtonik
  (https://code.google.com/p/python-patch/), MIT
* Grit XSLT by Niklas Lindström
  (http://code.google.com/p/oort/wiki/Grit), BSD


