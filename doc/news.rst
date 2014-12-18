Customizing the news feeds
==========================

During the ``news`` step, all documents in a docrepo are published in
one or more feeds. Each feed is made available in both Atom and HTML
formats. You can control which feeds are created, and which documents
are included in each feed, by the facets defined for your repo. The
process is similar to defining criteria for the TOC pages.

Default behaviour
-----------------

- only rdf:type and dcterms:publisher are used for feeds

How a facet is used by TOC and News
-----------------------------------

- create a list of dicts containing rdf statements abt each doc (and for News, including DocumentEntry things)
- for each facet that should be used (use_for_toc, use_for_feed):
- apply the selector and identificator fucntions to each such dict, yielding a string key (or none)
- create a group for all documents whose selector/identificator is a particular string key
- each such group becomes a TOC page or news feed

Processing the entry in the Atom feed
-------------------------------------

- Override news_item which is called with a dict and a binding (that
  identifies a particular facet)
- change the 'title' or 'summary' fields of the dict as needed
- this only changes the Atom result, not the stored DocumentEntry
