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
published in a series by a government or a NGO. It is particularly
useful for collections that contains explicit references between
documents, within or across collections.

It is designed to make it easy to get started with basic downloading,
parsing and republishing of documents, and then to improve each step
incrementally.


Prerequisites
-------------

Operating system
    Ferenda is tested and works on Unix, Mac OS and Windows.

Python
    Version 2.6 or newer required, 3.3 recommended (3.2 for Windows, see
    below). The code base is
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
     ``pdftohtml`` (from `poppler <http://poppler.freedesktop.org/>`_, version 0.21 or newer)
     and ``convert`` (from `ImageMagick
     <http://www.imagemagick.org/>`_). The ``convert_to_pdf``
     parameter requires either OpenOffice or LibreOffice. The
     ``ocr_lang`` parameter requires ``tesseract``.
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

.. note::

   If you want to use the Sleepycat/bsddb backend for storing RDF data
   together with python 3, you need to install the ``bsddb3``
   module. Even if you're using python 2 on Mac OS X, you might
   need to install this module, as the built-in ``bsddb`` module often
   has problems on this platform. It's not automatically installed by
   ``easy_install``/``pip`` as it has requirements of its own and is
   not essential.

   On Windows, we recommend using a binary distribution
   of ``lxml``. Unfortunately, at the time of writing, no such
   distribution is available for Python 3.3, so for the time
   being, you'll have to use python 3.2 on this platform.

Installing
----------

Installing using `pip
<http://www.pip-installer.org/en/latest/installing.html>`_ is
preferred (in fact, it's the only method tested)::

    pip install ferenda  

You should definitely consider installing ferenda in a `virtualenv
<http://www.virtualenv.org/en/latest/>`_.
  
Example
-------

This code uses the Ferenda API to create a website containing all(*)
RFCs and W3C recommended standards.

.. literalinclude:: examples/intro-example.py
   :start-after: # begin example		    
   :end-before: # end example		    

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

Features
--------

* Handles downloading, structural parsing and regeneration of large
  document collections.
* Contains libraries to make reading of plain text, PDF and MS Word
  documents as easy as HTML *(Note: these are far from finished)*.
* Uses established information standards as much as possible: If you
  like XHTML, XSLT, XML namespaces, RDF and SPARQL, you'll feel right
  at home.
* Leverages your favourite python libraries: `requests
  <http://docs.python-requests.org/en/latest/>`_, `beautifulsoup
  <http://www.crummy.com/software/BeautifulSoup/>`_, `rdflib
  <https://rdflib.readthedocs.org/en/latest/>`_, `lxml
  <http://lxml.de/>`_, `pyparsing <http://pyparsing.wikispaces.com/>`_
  and `whoosh <https://bitbucket.org/mchaput/whoosh/wiki/Home>`_.
* Possible to patch documents if the source content has errors.
* Easy to write reference/citation parsers and run them on document
  text.
* Documents in the same and other collections are automatically
  cross-referenced.
* Uses caches and dependency management to avoid performing the same
  work over and over.
* Once documents are downloaded and structured, you get a usable web
  site with API, Atom feeds and search for free *(Note: API
  functionality not yet implemented)*.
* Web site generation can create a set of static HTML pages for
  offline use (though you lose search and API functionality).
* Pull in commentary for documents from other sources *(Note: you'll
  have to do most of this work yourself)*.
* Create topic hubs / keyword pages for document from multiple
  collections *(Note: remains undocumented)*.

Next step
---------

See :doc:`firststeps` to set up a project and create your own simple
document repository.
