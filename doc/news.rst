Customizing the news feeds
==========================

During the ``news`` step, all documents in a docrepo are published in
one or more feeds. Each feed is made available in both Atom and HTML
formats. You can control which feeds are created, and which documents
are included in each feed, by the facets defined for your repo. The
process is similar to defining criteria for the TOC pages.

The main differences are:

* Most properties/RDF predicates of a document are not suitable as
  facets for news feed (it makes little sense to have a feed for
  eg. ``dcterms:title`` or ``dcterms:issued``). By default, only
  ``rdf:type`` and ``dcterms:publisher`` based facets are used for news feed
  generation. You can control this by specifying the ``use_for_feed``
  constructor argument.
* The dict that is passed to the selector and identificator functions
  contains extra fields from the corresponding
  :py:class:`.DocumentEntry` object. Particularly, the ``updated``
  value might be used by your key func in order to sort all entries by
  last-updated-date. The ``summary`` value might be used to contain a
  human-readable summary/representation of the entire document.
* Each row is passed through the :py:meth:`.news_item` method. You may
  override this in order to change the ``title`` or ``summary`` of
  each feed entry for the particular feed being constructed (as
  determined by the ``binding`` argument).
* A special feed, containing all entries within the docrepo, is always
  created.
