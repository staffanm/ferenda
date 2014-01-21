2014-01-?? RELEASE 0.1.7
========================

Mainly updates to ferenda.sources.legal.se.regeringen.

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
