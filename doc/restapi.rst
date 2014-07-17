The ReST API for querying
=========================

Ferenda tries to adhere to Linked Data principles, which makes it easy
to explain how to get information about any individual document or any
complete dataset (see :ref:`urls_used`). Sometimes it's desirable to
query for all documents matching a particular criteria, including full
text search. Ferenda has a simple API, based on the ``rinfo-service``
component of `RDL <https://github.com/rinfo/rdl>`_, and inspired by
`Linked data API
<https://code.google.com/p/linked-data-api/wiki/Specification>`_, that
enables you to do that. This API only provides search/select
operations that returns a result list. For information about each
individual result in that list, use the methods described in
:ref:`urls_used`.

.. note::

   Much of the things described below are also possible to do in pure
   SPARQL. Ferenda does not expose any open SPARQL endpoints to the
   world, though. But if you find the below API lacking in some
   aspect, it's certainly possible to directly expose your chosen
   triplestores SPARQL endpoint (as long as you're using Fuseki or
   Sesame) to the world.


The default endpoint to query is your main URL + ``/api/``,
eg. ``http://localhost:8000/api/``. The requests always use GET and
encode their parameters in the URL, and the responses are always in
JSON format.


Free text queries
-----------------

The simplest form of query is a free text query that is run against
all text of all documents. Use the parameter ``q``,
eg. ``http://localhost:8000/api/?q=tail`` returns all documents
(and document fragments) containing the word "tail".

Result lists
------------

The result of a query will be a JSON document containing some general
properties of the result, and a list of result items, eg:

.. literalinclude:: ../test/files/api/basicapi-fulltext-query.json
    
Each result item contain all fields that have been indexed (as
specified by your docrepos' facets, see :doc:`facets`, the document
URI (as the field ``iri``) and optionally a field ``matches`` that
provides a snipped of the matching text.

Parameters
----------

Any indexed property, as defined by your facets, can be used for
querying. The parameter is the same as the qname for the rdftype with
``_`` instead of ``:``, eq to search all documents that have
``dcterms:publisher`` set to ```http://example.org/publisher/A``, use
``http://localhost:8000/api/?dcterms_publisher=http%3A%2F%2Fexample.org%2Fpublisher%2FA``

You can use * as a wildcard for any string data, eg. the above could
be shortened to
``http://localhost:8000/api/?dcterms_publisher=*%2Fpublisher%2FA``.

If you have a facet with a set ``dimension_label``, you can use that
label directly as a parameter, eg ``http://localhost:8000/api/?aprilfools=true``. 


Paging
^^^^^^

By default, the result list only contains 10 results. You can inspect
the properties startIndex and totalResults to find out if there are
more results, and use the special parameter ``_page`` to request
subsequent pages of results. You can also request a different length
of the result list through the ``_pageSize`` parameter.

Statistics
----------

By requesting the special resource ``;stats``, eg
``http://localhost:8000/api/;stats``, you can get a statistics view
over all documents in all your docrepos for each of your defined
facets including the number of document for each value of it's
selector, eg:

.. literalinclude:: ../test/files/api/basicapi-stats.json

You can also get the same information for the documents in any result
list by setting the special parameter ``_stats=on``.


Ranges
------

``min-`` and ``max-``

``year-``


Support resources
-----------------

var-common.json and var-terms.json.

Legacy mode
-----------

Ferenda can be made directly compatible with the API used by ``rinfo-service``, which enables...
