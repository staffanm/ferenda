Customizing the news feeds
==========================

During the ``news`` step, all documents in a docrepo are published in
one or more feeds. Each feed is made available in both Atom and HTML
formats. You can control which feeds are created, and which documents
are included in each feed, by overriding
:meth:`ferenda.DocumentRepository.news_criteria` to return a set of
:class:`~ferenda.NewsCriteria` objects, one for each feed. The process
is similar to defining criteria for the TOC pages, but somewhat
simpler and not directly connected to the data in the triple store.

Each :class:`~ferenda.NewsCriteria` object should have a ``basefile``,
which is a slug-like short identifier for the feed. The default
implementation creates a single feed named "main". It should also have
a feed ``title``, which is used when presenting the feed. The default
implementation uses the title "All documents".

The interesting part of the :class:`~ferenda.NewsCriteria` object is
the ``selector`` parameter, which should be a function that gets
called once for each document, with the corresponding
:class:`~ferenda.DocumentEntry` object. It should return either
``True`` or ``False``, depending on whether this document should be
included in this feed or not. The :class:`~ferenda.DocumentEntry`
object does not directly give access to the metadata for the document,
but this information can be found by looking up the distilled RDF file
that contains metadata about this document.

The entries in the feed are, by default, sorted descending on their
``updated`` property. If you need to override this, you can provide a
``key`` function which is called with a
:class:`~ferenda.DocumentEntry` object and returns a value that can be
used for sorting.

