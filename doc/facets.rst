Describing how to group documents using facets
==============================================

A collection of documents can typically be arranged in a set of
groups, such as by year of publication, by document author, or by
keyword. In ferenda, each such method of grouping is described in the
form of a :py:class:`~ferenda.Facet`. By providing a list of Facet
objects in its :py:meth:`~ferenda.DocumentRepository.facets` method,
your docrepo can specify multiple ways of arranging the documents it's
handling. These facets are used to construct a static Table of
contents for your site, as well as defining the fields available for
querying when using the REST API.

A facet object is initialized with a set of parameters that together
define the method of grouping. These include the RDF predicate that
contains the data used for grouping, the datatype to be used for that
data, functions (or other callables) that sorts the data into discrete
groups, and other parameters that affect eg. the sorting order or if a
particular facet is used in a particular context.

Predefined facets and default behaviour
----------------------------------------

=============  =======================
facet          Description of grouping
=============  =======================
rdf:type       Grouped by qname of type
-------------  -----------------------
dcterms:title  Grouped by first letter
=============  =======================


Predefined selectors
--------------------


Combining facets from different docrepos
----------------------------------------


Contexts where facets are used
------------------------------

Table of contents
^^^^^^^^^^^^^^^^^

Each docrepo will have their own set of Table of contents pages. The
facets defined for one docrepo will be present only

The ReST API
^^^^^^^^^^^^

The ReST API uses all defined facets for all repos
simultaneously. This means that you can query eg. all documents
published in a certain year, and get results from all docrepos. This
requires that the defined facets don't clash, eg. that you don't have
two facets based on ``dcterms:publisher`` where one uses URI
references and the other uses.


