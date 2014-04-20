2014-04-?? RELEASE 0.1.7
========================

Setting DocumentStore.config now updates the associated DocumentStore
object with the config.datadir parameter

CompositeRepository.parse now raises ParseError if no subrepository
is able to parse the given basefile.

CompositeRepository.parse no longer requires that all subrepos have
storage_policy == "dir".

New method DocumentRepository.parseneeded returns True iff parsing of
the document is needed (logic moved from
ferenda.decorators.parseifneeded)

ferenda.decorators.render (by default called when calling
DocumentRepository.parse()) now serialize the entire document to JSON,
which later can be loaded to recreate the entire document object
tree. Controlled by config parameter serializejson.

ferenda.decorators.render now validates that required triples are
present in the output (Which triples are needed is controlled by
DocumentRepository.required_predicates).

ferenda.decorators now has a new newstate decorator, used in
ferenda.FSMParser

ferenda.Devel now has a new csvinventory action

.required_predicates

New compress parameter (Can either be empty or "bz2") controls whether
intermediate files are compressed to save space.

DocumentRepository.download_if_needed now sets both the If-None-match
and If-modified-since HTTP headers.

New method DocumentRepository.download_is_different is used to control
whether a newly downloaded resource is semantically different from a
previously downloaded resource (to avoid having each ASP.Net VIEWSTATE
change result in an archived document).

.parseneeded


DocumentRepository.render_xhtml now creates RDFa 1.1

New method DocumentRepository.construct_sparql_query allows for more
complex overrides than just setting the sparql_annotations class
attribute.

DocumentStore.path now takes an extra storage_policy parameter.

DocumentStore now stores multiple basefiles in a single directory even
when storage_policy == "dir" for all methods that cannot handle
attachments (like distilled_path, documententry_path etc)


New methods DocumentStore.open_intermediate, .serialized_path and
open_serialized

Elements.serialize and .deserialize now takes a format parameter,
which can be either "xml" (default) or "json". The "json" format
allows for full roundtripping of all documents.

New excepttion ferenda.errors.NoDownloadedFileError.

PDFReader now handles any word processing format that
OpenOffice/LibreOffice can handle, by first using soffice to convert
it to a PDF. It also handles PDFs that consists entirely of scanned
pages without text information, by first running the images through
the tesseract OCR engine. Finally, a new keep_xml parameter allows for
either removing the intermediate XML files or compressing them using
bz2 to save space.

New method PDFReader.is_empty

New method PDFReader.textboxes iterates through all textboxes on all
pages. The user can provide a glue function to automatically
concatenate textboxes that should be considered part of the same
paragraph (or other meaningful unit of text).

New debug method PDFReader.drawboxes can use the same glue function,
and creates a new pdf with all the resulting textboxes marked
up. (Requires PyPDF2 and reportlab, which makes this particular
feature Python 2-only).

PDFReader.Textbox objects can now be added to each other to form
larger Textbox objects.

All of the Swedish legal source docrepos (under
ferenda.sources.legal.se) have been overhauled and now mostly work.

ferenda.Transformer now optionally logs the equivalent xsltproc
command line when transforming using XSLT.

new method TripleStore.update, performs SPARQL
UPDATE/DELETE/DROP/CLEAR queries.

ferenda.util has new gYearMonth and gYear classes that subclass
datetime.date, but are useful when handling RDF literals that should
have the datatype xsd:gYearMonth (or xsd:gYear)

2013-11-13 RELEASE 0.1.6.1
==========================

This hotfix release corrected an error in setup.py that prevented
installs when using python 3.

2013-11-13 RELEASE 0.1.6
========================

This release mainly contains bug fixes and development infrastructure
changes. 95 % of the main code base is covered through the unit test
suite, and the examples featured in the documentation is now
automatically tested as well. Whenever discrepancies between the map
(documentation) and reality (code) has been found, reality has been
adjusted to be in accordance with the map.

The default HTML5 theme has also been updated, and should scale nicely
from screen widths ranging from mobile phones in portrait mode to
wide-screen desktops. The various bundled css and js files has been
upgraded to their most recent versions.

Backwards-incompatible changes:

* The DocumentStore.open_generated method was removed as noone was
  using it.

* The (non-documented) modules legalref and legaluri, which were
  specific to swedish legal references, have been moved into the
  ferenda.sources.legal.se namespace

* The (non-documented) feature where CSS files specified in the
  configuration could be in SCSS format, and automatically
  compiled/transformed, has been removed, since the library used
  (pyScss) currently has problems on the Python 3 platform.

New features:

* The :meth:`ferenda.Devel.mkpatch` command now actually works.

* The `republishsource` configuration parameter is now available, and
  controls whether your Atom feeds link to the original document file
  as it was fetched from the source, or to the parsed version. See
  :ref:`configuration`.

* The entire RDF dataset for a particular docrepo is now available
  through the ReST API in various formats using the same content
  negotiation mechanisms as the documents themselves. See :doc:`wsgi`.

* ferenda-setup now auto-configures ``indextype`` (and checks whether
  ElasticSearch is available, before falling back to Whoosh) in
  addition to ``storetype``.


2013-09-29 RELEASE 0.1.5
========================

Documentation, particularly code examples, has been updated to better
fit reality. They have also been added to the test suite, so they're
almost guaranteed to be updated when the API changes.

Backwards-incompatible changes

* Transformation of XHTML1.1+RDFa files to HTML5 is now done
  using the new Transformer class, instead of the
  DocumentRepository.transform_to_html method, which has been removed

* DocumentRepository.list_basefiles_for (which was a shortcut for
  calling list_basefiles_for on the docrepos' store object) has been
  removed. Typical change needed:

  -        for basefile in self.list_basefiles_for("parse"):
  +        for basefile in self.store.list_basefiles_for("parse"):

New features:

* New ferenda.Transformer class (see above)

* A new decorator, ferenda.decorators.downloadmax, can be used to
  limit the maximum number of documents that a docrepo will
  download. It looks for eitther the "FERENDA_DOWNLOADMAX" environment
  variable or the downloadmax configuration parameteter. This is
  primarily useful for testing and trying out new docrepos.

* DocumentRepository.render_xhtml will now include RDFa statements for
  all (non-BNode) subjects in doc.meta, not just the doc.uri
  subject. This makes it possible to state that a document is written
  by some person or published by some entity, and then include
  metadata on that person/entity. It also makes it possible to
  describe documents related to the main document, using the
  information gleaned from the main document

* DocumentStore now has a intermediate_path method -- previously some
  derived subclasses implemented their own, but now it's part of the
  base class.

* ferenda.errors.DocumentRemovedError now has a dummyfile attribute,
  which is used by ferenda.manager.run to avoid endless re-parsing of
  downloaded files that do not contain an actual document.

* A new shim module, ferenda.compat (modelled after six.moves),
  simplified imports of modules that may or may not be present in the
  stdlib depending on python version. So far, this includes
  OrderedDict, unittest and mock.

Infrastructural changes:

* Most of the bundled document repository classes in ferenda.sources
  has been overhauled and adapted to the changes that has occurred to
  the API since the old days.

* Continous integration and coverage is now set up with Travis-CI
  (https://travis-ci.org/staffanm/ferenda/) and Coveralls
  (https://coveralls.io/r/staffanm/ferenda)


2013-08-26 RELEASE 0.1.4
========================

* ElasticSearch is now supported as an alternate backend to Whoosh for
  fulltext indexing and searching.

* Documentation, particularly "Creating your own document
  repositories" have been substantially overhauled, and in the process
  various bugs that prevented the usage of custom SPARQL queries and
  XSLT transforms were fixed.

* The example RFC docrepo parser has been improved.


2013-08-11 RELEASE 0.1.3
========================

* Search functionality when running under WSGI is now
  implemented. Still a bit basic and not really customizable
  (everything is done by manager._wsgi_search), but seems to actually
  work.

* New docrepo: ferenda.sources.general.Static, for publishing static
  content (such as "About", "Contact", "Legal info") that goes into
  the site footer.

* The FulltextIndex class have been split up similarly to TripleStore
  and the road has been paved to get alternative implementations that
  connect to other fulltext index servers. ElasticSearch is next up to
  be implemented, but is not done yet.

* General improvement of documentation

2013-08-02 RELEASE 0.1.2
========================

* If using a RDFLib based triple store (storetype="SQLITE" or
  "SLEEPYCAT"), when generating all documents, all triples are read
  into memory, which speeds up the SPARQL querying considerably

* The TripleStore class has been overhauled and split into
  subclasses. Also gained the above inmemory functionality + the
  possibility of using command-line curl instead of requests when
  up/downloading large datasets.

* Content-negotiation when using the WSGI app (as described in
  doc/wsgi.rst) is supported

2013-07-27 RELEASE 0.1.1
========================

This release fixes a bug with TOC generation on python 2, creates a
correct long_description for pypi and adds some uncommitted CSS
improvements. Running the finished site under WSGI is now tested and
works ok-ish (although search is still unimplemented).

2013-07-26 RELEASE 0.1.0
========================

This is just a test release to test out pypi uploading as well as git
branching and tagging. Neverthless, this code is approaching feature
completeness, except that running a finished site under WSGI hasn't
been tested. Generating a static HTML site should work OK-ish.
