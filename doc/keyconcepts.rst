Key concepts
============


Project
-------

A collection of docrepos and configuration that is used to make a
useful web site. The first step in creating a project is running
``ferenda-setup <projectname>``.

A project is primarily defined by its configuration file at
``<projectname>/ferenda.ini``, which specifies which docrepos are
used, and settings for them as well as settings for the entire
project.

A project is managed using the ``ferenda-build.py`` tool.

If using the API instead of these command line tools, there is no
concept of a project except for what your code provides. Your client
code is responsible for creating the docrepo classes and providing
them with proper settings. These can be loaded from a
``ferenda.ini``-style file, be hard-coded, or handled in any other way
you see fit.

.. note::

   Ferenda uses the ``layeredconfig`` module internally to handle all
   settings. 

.. _configuration:

Configuration
-------------

A ferenda docrepo object can be configured in two ways - either when
creating the object, eg:

.. code-block:: py

  d = DocumentSource(datadir="mydata", loglevel="DEBUG",force=True)

.. note::

   Parameters that is not provided when creating the object are
   defaulted from the built-in configuration values (see below)
  
Or it can be configured using the :py:class:`~ferenda.LayeredConfig`
class, which takes configuration data from three places:

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

By setting the ``config`` property, you override any parameters provided when
creating the object.

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
compress          Whether to compress intermediate files.     ''
                  Can be either a empty string (don't
		  compress) or 'bz2' (compress using bz2).
serializejson     Whether to serialize document data as a    False
                  JSON document in the parse step.
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
downloadmax       Maximum number of documents to download    None
                  (None means download all of them).
conditionalget    Whether to use Conditional GET (through    True
                  the If-modified-since and/or
		  If-none-match headers)
url               The basic URL for the created site, used   'http://localhost:8000/'
                  as template for all managed resources in
		  a docrepo (see ``canonical_uri()``).
fulltextindex     Whether to index all text in a fulltext     True
                  search engine. Note: This can take a lot
		  of time.
useragent         The user-agent used with any external      'ferenda-bot'
                  HTTP Requests. Please change this into
		  something containing your contact info.
storetype         Any of the suppored types: 'SQLITE',       'SQLITE'
                  'SLEEPYCAT', 'SESAME' or 'FUSEKI'.
		  See :ref:`external-triplestore`.

storelocation     The file path or URL to the triple store,  'data/ferenda.sqlite'
                  dependent on the storetype
storerepository   The repository/database to use within the  'ferenda'
                  given triple store (if applicable)
indextype         Any of the supported types: 'WHOOSH' or    'WHOOSH'
                  'ELASTICSEARCH'. See
		  :ref:`external-fulltext`.
indexlocation     The location of the fulltext index         'data/whooshindex'
republishsource   Whether the Atom files should contain      False
                  links to the original, unparsed, source
		  documents
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
legacyapi         Whether the REST API should provide a      False
                  simpler API for legacy clients. See
		  :doc:`wsgi`.
================= ========================================== =========

.. _keyconcept-documentrepository:

DocumentRepository
------------------

A document repository (docrepo for short) is a class that handles all
aspects of a document collection: Downloading the documents (or
aquiring them in some other way), parsing them into structured
documents, and then re-generating HTML documents with added niceties,
for example references from documents from other docrepos.

You add support for a new collection of documents by subclassing
:py:class:`~ferenda.DocumentRepository`. For more
details, see :doc:`createdocrepos`

Document
--------

A :py:class:`~ferenda.Document` is the main unit of information in
Ferenda. A document is primarily represented in serialized form as a
XHTML 1.1 file with embedded metadata in RDFa format, and in code by
the :py:class:`~ferenda.Document` class. The class has five
properties:

* ``meta`` (a RDFLib :py:class:`~rdflib.graph.Graph`)
* ``body`` (a tree of building blocks, normally instances of
  :py:mod:`ferenda.elements` classes, representing the structure and
  content of the document)
* ``lang`` (an `IETF language
  <http://en.wikipedia.org/wiki/IETF_language_tag>`_ tag, eg ``sv`` or
  ``en-GB``)
* ``uri`` (a string representing the canonical URI for this document)
* ``basefile`` (a short internal id)

The method :py:meth:`~ferenda.DocumentRepository.render_xhtml` (which
is called automatically, as long as your ``parse`` method use the
:py:func:`~ferenda.decorators.managedparsing` decorator) renders a
:py:class:`~ferenda.Document` object into a XHTML 1.1+RDFa document.

Identifiers
-----------

Documents, and parts of documents, in ferenda have a couple of
different identifiers, and it's useful to understand the difference
and relation between them.

* ``basefile``: The *internal id* for a document. This is is internal
  to the document repository and is used as the base for the filenames
  for the stored files . The basefile isn't totally random and is
  expected to have some relationship with a human-readable identifier
  for the document. As an example from the RFC docrepo, the basefile
  for RFC 1147 would simply be "1147". By the rules encoded in
  :py:class:`~ferenda.DocumentStore`, this results in the downloaded
  file ``rfc/downloads/1147.txt``, the parsed file
  ``rfc/parsed/1147.xhtml`` and the generated file
  ``rfc/generated/1147.html``. Only documents themselves, not parts of
  documents, have basefile identifiers.

* ``uri``: The *canonical URI* for a document **or** a part of a
  document (generally speaking, a *resource*). This identifier is used
  when storing data related to the resource in a triple store and a
  fulltext search engine, and is also used as the external URL for the
  document when republishing (see :doc:`wsgi` and also
  :ref:`parsing-uri`). URI:s for documents can be set by settings the
  ``uri`` property of the Document object.  URIs for parts of
  documents are set by setting the ``uri`` property on any
  :py:mod:`~ferenda.elements` based object in the body tree. When
  rendering the document into XHTML, render_xhtml creates RDFa
  statements based on this property and the ``meta`` property.

* ``dcterms:identifier``: The *human readable* identifier for a document
  or a part of a document. If the document has an established
  human-readable identifier, such as "RFC 1147" or "2003/98/EC" (The
  EU directive on the re-use of public sector information), the
  dcterms:identifier is used for this. Unlike ``basefile`` and ``uri``,
  this identifier isn't set directly as a property on an
  object. Instead, you add a triple with ``dcterms:identifier`` as the
  predicate to the object's ``meta`` property, see :doc:`docmetadata`
  and also `DCMI Terms
  <http://dublincore.org/documents/2012/06/14/dcmi-terms/#terms-identifier>`_.
 
DocumentEntry
-------------

Apart from information about what a document contains, there is also
information about how it has been handled, such as when a document was
first downloaded or updated from a remote source, the URL from where
it came, and when it was made available through Ferenda. .This
information is encapsulated in the :py:class:`~ferenda.DocumentEntry`
class.  Such objects are created and updated by various methods in
:py:class:`~ferenda.DocumentRepository`. The objects are persisted to
JSON files, stored alongside the documents themselves, and are used by
the :py:meth:`~ferenda.DocumentRepository.news` method in order to
create valid Atom feeds.

.. _file-storage:

File storage
------------

During the course of processing, data about each individual document
is stored in many different files in various formats. The
:class:`~ferenda.DocumentStore` class handles most aspects of this
file handling. A configured DocumentStore object is available as the
``store`` property on any DocumentRepository object.

Example: If a created docrepo object ``d`` has the alias ``foo``, and
handles a document with the basefile identifier ``bar``, data about
the document is then stored:

* When downloaded, the original data as retrieved from the remote
  server, is stored as ``data/foo/downloaded/bar.html``, as determined
  by ``d.store.``:meth:`~ferenda.DocumentStore.downloaded_path`

* At the same time, a DocumentEntry object is serialized as
  ``data/foo/entries/bar.json``, as determined by
  ``d.store.``:meth:`~ferenda.DocumentStore.documententry_path`

* If the downloaded source needs to be transformed into some
  intermediate format before parsing (which is the case for eg. PDF or
  Word documents), the intermediate data is stored as
  ``data/foo/intermediate/bar.xml``, as determined by
  ``d.store.``:meth:`~ferenda.DocumentStore.intermediate_path`

* When the downloaded data has been parsed, the parsed XHTML+RDFa
  document is stored as ``data/foo/parsed/bar.xhtml``, as determined
  by ``d.store.``:meth:`~ferenda.DocumentStore.parsed_path`

* From the parsed document is automatically destilled a RDF/XML file
  containing all RDFa statements from the parsed file, which is stored
  as ``data/foo/distilled/bar.rdf``, as determined by ``d.store.``
  ``data/foo/annotations/bar.grit.txt``, as determined by
  ``d.store.``:meth:`~ferenda.DocumentStore.annotation_path`.

* During the ``relate`` step, all documents which are referred to by
  any other document are marked as dependencies of that document. If
  the ``bar`` document is dependent on another document, then this
  dependency is recorded in a dependency file stored at
  ``data/foo/deps/bar.txt``, as determined by
  ``d.store.``:meth:`~ferenda.DocumentStore.dependencies_path`.

* Just prior to the generation of browser-ready HTML5 files, all
  metadata in the system as a whole which is relevant to ``bar`` is
  serialized in an annotation file in GRIT/XML format at
  ``data/foo/annotations/bar.grit.txt``, as determined by
  ``d.store.``:meth:`~ferenda.DocumentStore.annotation_path`.

* Finally, the generated HTML5 file is created at
  ``data/foo/generated/bar.html``, as determined by
  ``d.store.``:meth:`~ferenda.DocumentStore.generated_path`. (This
  step also updates the serialized DocumentEntry object described
  above)


Archiving 
^^^^^^^^^

Whenever a new version of an existing document is downloaded, an
archiving process takes place when
:meth:`~ferenda.DocumentStore.archive` is called (by
:meth:`~ferenda.DocumentRepository.download_if_needed`). This method
requires a version id, which can be any string that uniquely
identifies a certain revision of the document. When called, all of the
above files are moved into the subdirectory in the following way
(assuming that the version id is "42"):

The result of this process is that a version id for the previously
existing files is calculated (by default, this is just a simple
incrementing integer, but the document in your docrepo might have a
more suitable version identifier already, in which case you should
override :py:meth:`~ferenda.DocumentRepository.get_archive_version` to
return this), and then all the above files (if they have been
generated) are moved into the subdirectory ``archive`` in the
following way.

``data/foo/downloaded/bar.html`` -> ``data/foo/archive/downloaded/bar/42.html``

The method :py:meth:`~ferenda.DocumentRepository.get_archive_version` is
used to calculate the version id. The default implementation just
provides a simple incrementing integer, but if the documents in your
docrepo has a more suitable version identifier already, you should
override :py:meth:`~ferenda.DocumentRepository.get_archive_version` to
return this.

The archive path is calculated by providing the optional ``version``
parameter to any of the ``*_path`` methods above.

To list all archived versions for a given basefile, use the
:meth:`~ferenda.DocumentStore.list_versions` method.

The ``open_*`` methods
^^^^^^^^^^^^^^^^^^^^^^

In many cases, you don't really need to know the filename that the
``*_path`` methods return, because you only want to read from or write to
it. For these cases, you can use the ``open_*`` methods instead. These
work as context managers just as the builtin open method do, and can
be used in the same way:

Instead of:

.. literalinclude:: examples/keyconcepts-file.py
   :start-after: # begin path
   :end-before: # end path


use:

.. literalinclude:: examples/keyconcepts-file.py
   :start-after: # begin open
   :end-before: # end open


Attachments
^^^^^^^^^^^

In many cases, a single file cannot represent the entirety of a
document. For example, a downloaded HTML file may need a series of
inline images. These can be handled as attachments by the download
method. Just use the optional attachment parameter to the appropriate
*_path / open_* methods:

.. literalinclude:: examples/keyconcepts-attachments.py
   :language: python		    
   :lines: 2-14,18-22	    

.. note:: 

   The DocumentStore object must be configured to handle attachments
   by setting the ``storage_policy`` property to ``dir``. This alters
   the behaviour of all ``*_path`` methods, so that eg. the main
   downloaded path becomes ``data/foo/downloaded/bar/index.html``
   instead of ``data/foo/downloaded/bar.html``

To list all attachments for a document, use
:meth:`~ferenda.DocumentStore.list_attachments` method.

Note that only some of the ``*_path`` / ``open_*`` methods supports the
``attachment`` parameter (it doesn't make sense to have attachments for
DocumentEntry files or distilled RDF/XML files).

Resources and the loadpath
--------------------------

Whenever ferenda needs any *resource file*, eg. an XSLT stylesheet, a
SPARQL query template or some RDF triples in a Turtle (``.ttl``) file,
it uses a :py:class:`~ferenda.ResourceLoader` instance to look in a
series of different "system" directories.

By placing files in the correct directories, and optionally
configuring the ``loadpath`` config option, you can substitute your
own resource file if the system versions aren't to your liking.
