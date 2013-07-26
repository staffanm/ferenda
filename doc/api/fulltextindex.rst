The ``FulltextIndex`` class
============================

Abstracts access to full text indexes (right now only a `Whoosh
<https://pypi.python.org/pypi/Whoosh>`_ backend is supported, but
maybe later `ElasticSearch <http://www.elasticsearch.org/>`_, `Solr
<http://lucene.apache.org/solr/>`_, `Xapian <http://xapian.org/>`_
and/or `Sphinx <http://sphinxsearch.com/>`_ will be supported).

.. autoclass:: ferenda.FulltextIndex
  :members:
  :undoc-members:
  :member-order: bysource

Datatype field classes
----------------------
		 
.. autoclass:: ferenda.fulltextindex.IndexedType
.. autoclass:: ferenda.fulltextindex.Identifier
.. autoclass:: ferenda.fulltextindex.Text
.. autoclass:: ferenda.fulltextindex.Label
.. autoclass:: ferenda.fulltextindex.Keywords
.. autoclass:: ferenda.fulltextindex.Boolean
.. autoclass:: ferenda.fulltextindex.URI


Search field classes
--------------------

.. autoclass:: ferenda.fulltextindex.SearchModifier
.. autoclass:: ferenda.fulltextindex.Less
.. autoclass:: ferenda.fulltextindex.More
.. autoclass:: ferenda.fulltextindex.Between
