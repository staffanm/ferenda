Key concepts
============

Document
--------

A :py:class:`~ferenda.Document` is the main unit of information in
Ferenda. A document is primarily represented in serialized form as a
XHTML 1.1 file with embedded metadata in RDFa format, and in code by
the :py:class:`~ferenda.sources.Document` class. The class has five
properties:

* ``meta`` (a RDFLib :py:class:`~rdflib.graph.Graph`)
* ``body`` (a tree of building blocks, normally instances of
  :py:mod:`ferenda.elements` classes, representing the structure and
  content of the document)
* ``lang`` (an `IETF language
  <http://en.wikipedia.org/wiki/IETF_language_tag>`_ tag, eg ``sv`` or
  ``en-GB``)
* ``uri`` (a string representing the canonical URI for this document)
* ``basefile`` (see below)

The method
:py:meth:`~ferenda.sources.DocumentRepository.render_xhtml`
renders a :py:class:`~ferenda.Document` object into a XHTML 1.1+RDFa document.

A document has a couple of different identifiers, and it's useful to
understand the difference and relation between them.

* ``basefile``: This is a short id, internal to the document repository,
  and is used as the base for stored files on disk. For a docrepo of
  RFCs, a good basefile for RFC 1147 is "1147", which corresponds to
  the downloaded file rfc/downloads/1147.txt, the parsed file
  rfc/parsed/1147.xhtml and the generated file rfc/generated/1147.html
* ``uri``: The *canonical URI* for this resource. In case you're dealing
  with documents that have no well-defined canonical URIs (which is
  the common case), feel free to invent a URI scheme. Even if there is
  a established canonical URI for your document, you might want to use
  a URI that resolves to a server under your control, so that you can
  provide good Linked data for that URI. You can point out the
  established canonical URI using a RDF owl:sameAs statement. The
  method
  :py:meth:`~ferenda.sources.DocumentRepository.canonical_uri`
  transforms a basefile to a canonical uri.
* ``dct:identifier`` (optional): If the document has an established
  human-readable identifier, such as "RFC 1147" or "2003/98/EC" (The
  EU directive on the re-use of public sector information), the
  dct:identifier is used for this. See
  `DCMI Terms <http://dublincore.org/documents/2012/06/14/dcmi-terms/#terms-identifier>`_
  and :doc:`linkeddata`.

DocumentEntry
-------------

Information about how a document has been handled within the ferenda
framework is not a part of the Document object as described
above. Such information include when a document was first downloaded
or updated, the URL from where it came, and when it was made available
through the ferenda-based website, is encapsulated in the
:py:class:`~ferenda.DocumentEntry` class. Such objects are created and
updated by the download methods, stored alongside the documents
themselves (in :py:mod:`pickle` format), and are read by the feeds
methods in order to create valid Atom feeds.

.. _keyconcept-documentrepository:

DocumentRepository
------------------

A document repository (docrepo for short) is a class that handles all
aspects of a document collection: Downloading the documents (or
aquiring them in some other way), parsing them into structured
documents, and then re-generating HTML documents with added niceties,
for example references from documents from other docrepos.

You add support for a new collection of documents by subclassing
:py:class:`~ferenda.sources.DocumentRepository`. For more
details, see :doc:`createdocrepos`


Project
-------

A collection of docrepos and configuration that is used to make a
useful web site. The first step in creating a project is running
`ferenda-setup.py <projectname>`.

.. _configuration:

Configuration
-------------

A ferenda docrepo object can be configured in two ways - either when
instantiating the object, eg:

.. code-block:: py

  d = DocumentSource(datadir="mydata", loglevel="DEBUG",force=True)

.. note::

   Parameters that is not provided when instantiating the object are
   defaulted from the built-in configuration values (see below)
  
Or it can be configured using the :py:class:`~ferenda.LayeredConfig` class, which takes
configuration data from three places:

* built-in configuration values (provided by
  :py:meth:`~ferenda.DocumentRepository.get_default_options`)
* values from a configuration file (normally ``ferenda.ini``", placed
  alongside ``ferenda-build.py``)
* command-line parameters, eg ``--force --datadir=mydata``

.. code-block:: py
  
  d = DocumentSource()
  d.config = LayeredConfig(defaults=d.get_default_options(), 
                           inifile="ferenda.ini", 
                           commandline=sys.argv)
  
(This is what ``ferenda-build.py`` does behind the scenes)

Configuration values from the configuration file overrides built-in
configuration values, and command line parameters override
configuration file values.

By setting the ``config`` property on you override any parameters provided when
instantiating the object.

.. note::

   Because of reasons, after re-setting the ``config`` property, you
   also need to re-set the ``store`` property. For now, look at the
   source code for ``_instantiate_class`` in ``ferenda/manager.py`` to
   learn how it's done. 

These are the normal configuration options:

================= ========================================== =========
option            description                                default
================= ========================================== =========
datadir           Directory for all downloaded/parsed etc    'data'
                  files
patchdir          Directory containing patch files used by   'patches'
                  patch_if_needed
parseforce        Whether to re-parse downloaded files,      False
                  even if resulting XHTML1.1 files exist
		  and are newer than downloaded files
generateforce     Whether to re-generate browser-ready       False
                  HTML5 files, even if they exist and are
		  newer than all dependencies
force             If True, overrides both parseforce and     False
                  generateforce.
fsmdebug          Whether to display debugging information   False
                  from FSMParser 
refresh           Whether to re-download all files even if   False
                  previously downloaded.
lastdownload      The datetime when this repo was last       None
                  downloaded (stored in conf file)
conditionalget    Whether to use Conditional GET (through    True
                  the If-modified-since and/or
		  If-none-match headers)
url               The basic URL for the created site, used   'http://localhost:8000/'
                  as template for all managed resources in
		  a docrepo (see ``canonical_uri()``).
fulltextindex     Whether to create a Whoosh fulltext index. True
                  Note: This can take a lot of time.
useragent         The user-agent used with any external      'ferenda-bot'
                  HTTP Requests. Please change this into
		  something containing your contact info.
storetype         Any of the suppored types: 'SQLITE',       'SQLITE'
                  'SLEEPYCAT', 'SESAME' or 'FUSEKI'
storelocation     The file path or URL to the triple store,  'data/ferenda.sqlite'
                  dependent on the storetype
storerepository   The repository/database to use within the  'ferenda'
                  given triple store (if applicable)
indexlocation     The location of the whoosh index           'data/whooshindex'
combineresources  Whether to combine and minify all css and  False
                  js files into a single file each
cssfiles          A list of all required css files           ['http://fonts.googleapis.com/css?family=Raleway:200,100',
                                                             'res/css/normalize.css',
                                                             'res/css/main.css',
						             'res/css/ferenda.css']
jsfiles           A list of all required js files            ['res/js/jquery-1.9.0.js',
                                                             'res/js/modernizr-2.6.2-respond-1.1.0.min.js',
                                                             'res/js/ferenda.js']
staticsite        Whether to generate static HTML files      False
                  suitable for offline usage (removes
		  search and uses relative file paths
		  instead of canonical URIs)
================= ========================================== =========

File storage
------------

  
Intermediate files
^^^^^^^^^^^^^^^^^^

In many cases, the data that you want parse to work on differs
slightly from what download actually downloaded. For example, if
you're downloading PDF files or Word documents, you will probably
massage them into a form that is easier to parse (eg. by using
`pdftohtml` or `antiword`). This initial transformation often takes
time and is not likely to need changing once in place. Furthermore,
PDF and Word files are unsuitable as a base for patching (see below),
but the transformed HTML/XML/Text files usually are better for this.

Therefore, many docrepos will be using intermediate files (However,
our examples used in the netstandards site, do not need them).

- main and auxillary intermediate files

Annotation files
^^^^^^^^^^^^^^^^

(More properly called "pertinent RDF statements for a particular file")

