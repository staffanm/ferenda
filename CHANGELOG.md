2013-08-?? RELEASE 0.1.2
========================

* If using a RDFLib based triple store (storetype="SQLITE" or
  "SLEEPYCAT"), when generating all documents, all triples are read
  into memory, which speeds up the SPARQL querying considerably

* The TripleStore class has been overhauled and split into
  subclasses. Also gained the above inmemory functionality + the
  possibility of using command-line curl instead of requests when
  up/downloading large datasets.

* Content-negotiation when using the WSGI app (as described in
  doc/wsgi.rst) is being developed
	
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
