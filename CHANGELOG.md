2014-07-?? RELEASE 0.2.0

This release adds a REST-based HTTP API and includes a lot of
infrastructure to support repo-defined querying and aggregation of
arbitrary document properties. This also led to a generalization of
the TocCriteria class and associated methods, which are now replaced
by the Facet class.

The REST API should be considered an alpha version and is definitly
not stable.

Backwards-incompatible changes:

The class TocCriteria and the DocumentRepository methods
toc_predicates, toc_criteria et al have been removed and replaced with
the Facet class and similar methods.

ferenda.sources.legal.se.direktiv.DirPolopoly and
ferenda.sources.legal.se.propositioner.PropPolo has been renamed to
...DirRegeringen and ...PropRegeringen, respectively.

New features:

A REST API enables clients to do faceted querying (ie document whose
properties have specified values), full-text search or combinations.

Several popular RDF ontologies are included and exposed using the REST
API. A docrepo can include custom RDF ontologies that are used in the
same way. All ontologies used by a docrepo is available as a RDFLib
graph from the .ontologies property

Docrepos can include extra/common data that describes things which
your documents refer to, like companies, publishing entities, print
series and abstract things like the topic/keyword of a document. This
information is provided in the form of a RDF graph, which is also
exposed using the REST API. All common data defined for a docrepo is
available as the .commondata property.

New method DocumentRepository.lookup_resource lookup resource URIs
from the common data using foaf:name labels (or any other RDF
predicate that you might want to use)

New class Facet and new methods DocumentRepository.facets,
.faceted_data, facet_query and facet_seltct to go with that
class. These replace the TocCriteria class and the methods
DocumentRepository.toc_select, .toc_query, .toc_criteria and
.toc_predicates.

The WSGI app now provides content negotiation using file extensions as
well as a the HTTP Accept header, ie. requesting
"http://localhost:8000/res/base/123.ttl" gives the same result as
requesting the resource "http://localhost:8000/res/base/123" using the
"Accept: text/turtle" header.

New exceptions ferenda.errors.SchemaConflictError and .SchemaMappingError.

The FulltextIndex class now creates a schema in the underlying
fulltext enginge based upon the used docrepos, and the facets that
those repos define. The FulltextIndex.update method now takes
arbitrary arguments that are stored as separate fields in the fulltext
index. Similarly, the FulltextIndex.query method now takes arbitrary
arguments that are used to limit the search to only those documents
whose properties match the arguments.

ferenda.Devel has a new ´destroyindex' action which completely removes
the fulltext index, which might be needed whenever its schema
changes. If you add any new facets, you'll need to run
"./ferenda-build.py devel destroyindex" followed by
"./ferenda-build.py all relate --all --force"

The docrepos ferenda.sources.tech.RFC and W3Standards have been
updated with their own ontologies and commondata. The result of parse
now creates better RDF, in particular things like dcterms:creator and
dcterms:subject not point to URIs (defined in commondata) instead of
plain string literals.

Infrastructural changes:

cssmin is no longer bundled within ferenda. Instead it's marked as a
dependency so that pip/easy_install automatically downloads it from
pypi.

The prefix for DCMI Metadata Terms have been changed from "dct" to
"dcterms" in all code and documentation.

testutil now has a Py23DocChecker that can be used with
doctest.DocTestSuite() to enable single-source doctests that work with
both python 2 and 3.

New method ferenda.util.json_default_date, usable as the default
argument of json.dump to serialize datetime object into JSON strings.

2014-04-22 RELEASE 0.1.7
========================

This release mainly updates the swedish legal sources, which now does
a decent job of downloading and parsing a variety of legal
information. During the course of that work, a number of changes
needed to be made to the core of ferenda. The release is still a part
of the 0.1 series because the REST API isn't done yet (once it's in,
that will be release 0.2)

Backwards-incompatible changes:

CompositeRepository.parse now raises ParseError if no subrepository
is able to parse the given basefile.

New features:

ferenda.CompositeRepository.parse no longer requires that all subrepos have
storage_policy == "dir".

Setting ferenda.DocumentStore.config now updates the associated DocumentStore
object with the config.datadir parameter

New method ferenda.DocumentRepository.construct_sparql_query() allows
for more complex overrides than just setting the sparql_annotations
class attribute.

New method DocumentRepository.download_is_different() is used to control
whether a newly downloaded resource is semantically different from a
previously downloaded resource (to avoid having each ASP.Net VIEWSTATE
change result in an archived document).

New method DocumentRepository.parseneeded(): returns True iff parsing
of the document is needed (logic moved from
ferenda.decorators.parseifneeded)

New class variable ferenda.DocumentRepository.required_predicates:
Controls which predicates that is expected to be in the output data
from .parse()

The method ferenda.DocumentRepository.download_if_needed() now sets both
the If-None-match and If-modified-since HTTP headers.

The method ferenda.DocumentRepository.render_xhtml() now creates RDFa 1.1

New 'compress' parameter (Can either be empty or "bz2") controls whether
intermediate files are compressed to save space.

The method ferenda.DocumentStore.path() now takes an extra storage_policy parameter.

The class ferenda.DocumentStore now stores multiple basefiles in a
single directory even when storage_policy == "dir" for all methods
that cannot handle attachments (like distilled_path,
documententry_path etc)

New methods ferenda.DocumentStore.open_intermediate(), .serialized_path() and
open_serialized()

The decorator @ferenda.decorators.render (by default called when calling
DocumentRepository.parse()) now serialize the entire document to JSON,
which later can be loaded to recreate the entire document object
tree. Controlled by config parameter serializejson.

The decorator @ferenda.decorators.render now validates that required triples (as
determined by .required_predicates) are present in the output.

New decorator @ferenda.decorators.newstate, used in
ferenda.FSMParser

The docrepo ferenda.Devel now has a new csvinventory action

The functions ferenda.Elements.serialize() and .deserialize() now takes a format parameter,
which can be either "xml" (default) or "json". The "json" format
allows for full roundtripping of all documents.

New exception ferenda.errors.NoDownloadedFileError.

The class ferenda.PDFReader now handles any word processing format that
OpenOffice/LibreOffice can handle, by first using soffice to convert
it to a PDF. It also handles PDFs that consists entirely of scanned
pages without text information, by first running the images through
the tesseract OCR engine. Finally, a new keep_xml parameter allows for
either removing the intermediate XML files or compressing them using
bz2 to save space.

New method ferenda.PDFReader.is_empty()

New method ferenda.PDFReader.textboxes() iterates through all
textboxes on all pages. The user can provide a glue function to
automatically concatenate textboxes that should be considered part of
the same paragraph (or other meaningful unit of text).

New debug method ferenda.PDFReader.drawboxes() can use the same glue
function, and creates a new pdf with all the resulting textboxes
marked up. (Requires PyPDF2 and reportlab, which makes this particular
feature Python 2-only).

ferenda.PDFReader.Textbox objects can now be added to each other to form
larger Textbox objects.

ferenda.Transformer now optionally logs the equivalent xsltproc
command line when transforming using XSLT.

new method ferenda.TripleStore.update(), performs SPARQL
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
