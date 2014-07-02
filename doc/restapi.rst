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
enables you to do that.

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

Searches in body text (+ any stringish fields?)

Result lists
------------

``next`` and ``previous`` fields
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Parameters
----------

if dimension_label is defined, use that. Else use the qname for the
rdftype with underscore, eg ``dcterms_title``

* as wildcard for string data

``_page`` and ``pageSize``
^^^^^^^^^^^^^^^^^^^^^^^^^^

Statistics
----------

Defining facets
---------------

dimension_label for synthesized labels

(alternative: express the synthesized value as a rdf predicate in your
own vocablulary and use a direct facet for it).


Ranges
------

``min-`` and ``max-``

``year-``


Legacy mode
-----------

Ferenda can be made directly compatible with the API used by ``rinfo-service``, which ena
