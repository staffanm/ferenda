Introduction to Ferenda
=======================

Ferenda is a python library and framework for transforming
unstructured document collections into structured Linked Data. It
helps with downloading documents, parsing them to add explicit
semantic structure and RDF-based metadata, finding relationships
between documents, and publishing the results.

It uses the XHTML and RDFa standards for representing semantic
structure, and republishes content using Linked Data principles.

It works best for large document collections that have some degree of
internal standardization, such as the laws of a particular country,
technical standards such as RFCs or ISO standards, or reports
published by a government or a NGO. It is particularly useful for a
set of document collections that contains explicit references between
documents. It is, to a lesser degree, useful for data that is not
document-based, such as product databases, TV listings and time tables
(but you might prefer `Scrapy <http://scrapy.org>`_ for those).

It is designed to make it easy to get started with basic downloading,
parsing and republishing of documents, and then to improve each step
incrementally.


Prerequisites
-------------

Operating system
    Ferenda works on Unix, Mac OS and Windows.

Python
    Version 2.6 or newer required, 3.3 recommended. The code base is
    primarily developed with python 3, and is heavily dependent on all
    forward compatibility features introduced in Python 2.6. Python
    3.0 and 3.1 is not supported.

Third-party libraries
    ``beautifulsoup4``, ``rdflib``, ``rdflib-sqlite``, ``html5lib``,
    ``lxml``, ``requests``, ``whoosh``, ``pyparsing``, ``jsmin``,
    ``six`` and their respective requirements. If you've installed
    ferenda using ``easy_install`` or ``pip`` they should have been
    installed automatically. If you're working with a clone of the
    source repository you can install them with a simple ``pip
    install -r requirements.py3.txt`` (substitute with
    ``requirements.py2.txt`` if you're not yet using python 3).

Command-line tools
   For some functionality, certain binaries must be present and in your ``$PATH``:

   * :py:class:`~ferenda.PDFReader` requires ``pdftotext`` and ``pdftohtml`` (from `poppler <http://poppler.freedesktop.org/>`_) and ``convert`` (from `ImageMagic <http://www.imagemagick.org/>`_)
   * :py:class:`~ferenda.Wordreader` requires `antiword <http://www.winfield.demon.nl/>`_ to handle old .doc files. 
   * :py:class:`~ferenda.TripleStore` can perform some operations (bulk up- and download) much faster if `curl <http://curl.haxx.se/>`_ is installed.

Once you start to collect a non-trivial number of documents and
metadata about those documents, you'll need a RDF triple store, either
`Sesame <http://www.openrdf.org/>`_ or `Fuseki
<http://jena.apache.org/documentation/serving_data/index.html>`_.  For
document collections small enough to keep all metadata in memory you
can get by with only rdflib, using either a Sqlite or a Berkely DB
(aka Sleepycat/bsddb) backend. For further information, see
:doc:`external-dbs`.

.. note::

   If you want to use the Sleepycat/bsddb backend for storing RDF data
   together with python 3, you need to install the ``bsddb3``
   module. Even if you're using python 2 on Mac OS X, you might
   need to install this module, as the built-in ``bsddb`` module often
   has problems on this platform. It's not automatically installed by
   ``easy_install``/``pip`` as it has requirements of its own and is
   not essential.

   On Mac OS X, there is a known incompatibility between the system
   libraries, python 3.3, BeautifulSoup 4.2 and lower and lxml, which manifests
   itself in the error message "Unicode parsing is not supported on
   this platform". A workaround (until BeautifulSoup 4.3 is released) 
   is to make sure lxml is statically
   compiled with libxml2/libxslt by setting the environment
   variable ``STATIC_DEPS`` to ``true``, eg::

       STATIC_DEPS=true pip install lxml

   On Windows, we recommend using a binary distribution
   of lxml. Unfortunately, at the time of writing, no such
   distribution is available for Python 3.3, so for the time
   being, you'll have to use python 3.2 on this platform.

Example
-------

This code creates a website containing all* RFCs and W3C recommended
standards.

.. literalinclude:: intro-example.py

* actually, it only downloads the 50 most recent of each. Downloading
and handling close to 7000 RFC documents takes a very long time.
		    
Alternately, using the command line tools:

.. literalinclude:: intro-example.sh

.. note::

   *Actually, in order to finish before a potential user gives up, the above examples just processes the 50 most recent documents of each type. In order to process all documents, remove the ``downloadmax`` configuration parameter/command line option, and be prepared to wait several hours.

Features
--------

* Handles downloading, structural parsing and regeneration of large document collections
* Contains libraries to make reading of text, PDF and MS Word documents as easy as HTML *(Note: these are far from finished)*
* Leverages your favourite python libraries: `requests <http://docs.python-requests.org/en/latest/>`_, `beautifulsoup <http://www.crummy.com/software/BeautifulSoup/>`_, `rdflib <https://rdflib.readthedocs.org/en/latest/>`_, `lxml <http://lxml.de/>`_, `pyparsing <http://pyparsing.wikispaces.com/>`_ and `whoosh <https://bitbucket.org/mchaput/whoosh/wiki/Home>`_.
* Uses established standards as much as possible: If you like XHTML, XSLT, XML namespaces, RDF and SPARQL, you'll feel right at home
* Possible to patch documents if the source content has errors
* Easy to write reference/citation parsers and run them on document text
* Documents in the same and other collections are automatically cross-referenced
* Uses caches and dependency management to avoid performing the same work over and over
* Once documents are downloaded and structured, you get a usable web
  site with API, Atom feeds and search for free *(Note: API and search functionality not yet implemented)*
* Pull in commentary for documents from other sources *(Note: you'll have to do most of this work yourself)*
* Create topic hubs / keyword pages for document from multiple collections *(Note: remains undocumented)*
