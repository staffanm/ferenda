2013-08-?? RELEASE 0.1.4

* ElasticSearch is now supported as an alternate backend to Whoosh for
  fulltext indexing and searching.

* Documentation, particularly "Creating your own document
  repositories" have been substantially overhauled, and in the process
  various bugs that prevented the usage of custom SPARQL queries and
  XSLT transforms were fixed.

2013-08-11 RELEASE 0.1.3

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
