Introduction to Ferenda
=======================

Ferenda is a python library and framework for transforming
unstructured document collections into structured
`Linked Data <http://en.wikipedia.org/wiki/Linked_data>`_. It helps
with downloading documents, parsing them to add explicit semantic
structure and RDF-based metadata, finding relationships between
documents, and republishing the results.

It uses the XHTML and RDFa standards for representing semantic
structure, and republishes content using Linked Data principles and a
REST-based API.

Ferenda works best for large document collections that have some
degree of internal standardization, such as the laws of a particular
country, technical standards, or reports published in a series. It is
particularly useful for collections that contains explicit references
between documents, within or across collections.

It is designed to make it easy to get started with basic downloading,
parsing and republishing of documents, and then to improve each step
incrementally.

Example
-------

Ferenda can be used either as a library or as a command-line
tool. This code uses the Ferenda API to create a website containing
all(*) RFCs and W3C recommended standards.

.. literalinclude:: examples/intro-example.py
   :lines: 8-19,21-65

Alternately, using the command line tools and the project framework:
 
.. literalinclude:: examples/intro-example.sh

.. note::

   (*) actually, it only downloads the 50 most recent of
   each. Downloading, parsing, indexing and re-generating close to
   7000 RFC documents takes several hours. In order to process all
   documents, remove the ``downloadmax`` configuration
   parameter/command line option, and be prepared to wait. You should
   also set up an external triple store (see :ref:`external-triplestore`) and
   an external fulltext search engine (see :ref:`external-fulltext`).

Prerequisites
-------------

Operating system
    Ferenda is tested and works on Unix, Mac OS and Windows.

Python
    Version 2.6 or newer required, 3.4 recommended. The code base is
    primarily developed with python 3, and is heavily dependent on all
    forward compatibility features introduced in Python 2.6. Python
    3.0 and 3.1 is not supported.

Third-party libraries
    ``beautifulsoup4``, ``rdflib``, ``html5lib``,
    ``lxml``, ``requests``, ``whoosh``, ``pyparsing``, ``jsmin``,
    ``six`` and their respective requirements. If you install
    ferenda using ``easy_install`` or ``pip`` they should be 
    installed automatically. If you're working with a clone of the
    source repository you can install them with a simple ``pip
    install -r requirements.py3.txt`` (substitute with
    ``requirements.py2.txt`` if you're not yet using python 3).

Command-line tools
   For some functionality, certain executables must be present and in
   your ``$PATH``:

   * :py:class:`~ferenda.PDFReader` requires ``pdftotext`` and
     ``pdftohtml`` (from `poppler <http://poppler.freedesktop.org/>`_, version 0.21 or newer).

     * The :py:meth:`~ferenda.pdfreader.Page.crop` method requires
       ``convert`` (from `ImageMagick <http://www.imagemagick.org/>`_).
     * The ``convert_to_pdf`` parameter to
       :py:meth:`~ferenda.PDFReader.read` requires the ``soffice``
       binary from either OpenOffice or LibreOffice
     * The ``ocr_lang`` parameter to
       :py:meth:`~ferenda.PDFReader.read` requires ``tesseract`` (from
       `tesseract-ocr <https://code.google.com/p/tesseract-ocr/>`_),
       ``convert`` (see above) and ``tiffcp`` (from `libtiff
       <http://www.libtiff.org/>`_)

   * :py:class:`~ferenda.WordReader` requires `antiword
     <http://www.winfield.demon.nl/>`_ to handle old ``.doc`` files.
   * :py:class:`~ferenda.TripleStore` can perform some operations
     (bulk up- and download) much faster if `curl
     <http://curl.haxx.se/>`_ is installed.

Once you have a large number of documents and metadata about those
documents, you'll need a RDF triple store, either `Sesame
<http://www.openrdf.org/>`_ (at least version 2.7) or `Fuseki
<http://jena.apache.org/documentation/serving_data/index.html>`_ (at
least version 1.0).  For document collections small enough to keep all
metadata in memory you can get by with only rdflib, using either a
Sqlite or a Berkely DB (aka Sleepycat/bsddb) backend. For further
information, see :ref:`external-triplestore`.

Similarly, once you have a large collection of text (either many short
documents, or fewer long documents), you'll need an fulltext search
engine to use the search feature (enabled by default). For small
document collections the embedded `whoosh
<https://bitbucket.org/mchaput/whoosh/wiki/Home>`_ library is
used. Right now, `ElasticSearch <http://www.elasticsearch.org/>`_ is
the only supported external fulltext search engine.

As a rule of thumb, if your document collection contains over 100 000
RDF triples or 100 000 words, you should start thinking about setting
up an external triple store or a fulltext search engine. See
:ref:`external-fulltext`.

Installing
----------

Ferenda should preferably be installed with `pip
<http://www.pip-installer.org/en/latest/installing.html>`_ (in fact,
it's the only method tested)::

    pip install ferenda  

You should definitely consider installing ferenda in a `virtualenv
<http://www.virtualenv.org/en/latest/>`_.

.. note::

   If you want to use the Sleepycat/bsddb backend for storing RDF data
   together with python 3, you need to install the ``bsddb3``
   module. Even if you're using python 2 on Mac OS X, you might
   need to install this module, as the built-in ``bsddb`` module often
   has problems on this platform. It's not automatically installed by
   ``easy_install``/``pip`` as it has requirements of its own and is
   not essential.

   On Windows, we recommend using a binary distribution of
   ``lxml``. Unfortunately, at the time of writing, no such official
   distribution is for Python 3.3 or later. However, the inofficial
   distributions available at
   http://www.lfd.uci.edu/~gohlke/pythonlibs/#lxml has been tested
   with ferenda on python 3.3 and later, and seems to work great.

   The binary distributions installs lxml into the system python
   library path. To make lxml available for your virtualenv, use the
   ``--system-site-packages`` command line switch when creating the
   virtualenv.
  
Features
--------

* Handles downloading, structural parsing and regeneration of large
  document collections.
* Contains libraries to make reading of plain text, MS Word and PDF
  documents (including scanned text) as easy as HTML.
* Uses established information standards like XHTML, XSLT, XML
  namespaces, RDF and SPARQL as much as possible.
* Leverages your favourite python libraries: `requests
  <http://docs.python-requests.org/en/latest/>`_, `beautifulsoup
  <http://www.crummy.com/software/BeautifulSoup/>`_, `rdflib
  <https://rdflib.readthedocs.org/en/latest/>`_, `lxml
  <http://lxml.de/>`_, `pyparsing <http://pyparsing.wikispaces.com/>`_
  and `whoosh <https://bitbucket.org/mchaput/whoosh/wiki/Home>`_.
* Handle errors in upstream sources by creating one-off patch files
  for individiual documents.
* Easy to write reference/citation parsers and run them on document
  text.
* Documents in the same and other collections are automatically
  cross-referenced.
* Uses caches and dependency management to avoid performing the same
  work over and over.
* Once documents are downloaded and structured, you get a usable web
  site with REST API, Atom feeds and search for free.
* Web site generation can create a set of static HTML pages for
  offline use.

Next step
---------

See :doc:`firststeps` to set up a project and create your own simple
document repository.
