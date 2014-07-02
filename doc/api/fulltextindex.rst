The ``FulltextIndex`` class
============================

Abstracts access to full text indexes (right now only `Whoosh
<https://pypi.python.org/pypi/Whoosh>`_ and `ElasticSearch
<http://www.elasticsearch.org/>`_ is supported, but maybe later, `Solr
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
.. autoclass:: ferenda.fulltextindex.Datetime
.. autoclass:: ferenda.fulltextindex.Text
.. autoclass:: ferenda.fulltextindex.Label
.. autoclass:: ferenda.fulltextindex.Keyword
.. autoclass:: ferenda.fulltextindex.Boolean
.. autoclass:: ferenda.fulltextindex.URI
.. autoclass:: ferenda.fulltextindex.Resource


Search field classes
--------------------

.. autoclass:: ferenda.fulltextindex.SearchModifier
.. autoclass:: ferenda.fulltextindex.Less
.. autoclass:: ferenda.fulltextindex.More
.. autoclass:: ferenda.fulltextindex.Between
