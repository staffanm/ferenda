Grouping documents with facets
==============================

A collection of documents can typically be arranged in a set of
groups, such as by year of publication, by document author, or by
keyword. In ferenda, each such method of grouping is described in the
form of a :py:class:`~ferenda.Facet`. By providing a list of Facet
objects in its :py:meth:`~ferenda.DocumentRepository.facets` method,
your docrepo can specify multiple ways of arranging the documents it's
handling. These facets are used to construct a static Table of
contents for your site, as well as creating Atom feeds of all
documents and defining the fields available for querying when using
the REST API.

A facet object is initialized with a set of parameters that together
define the method of grouping. These include the RDF predicate that
contains the data used for grouping, the datatype to be used for that
data, functions (or other callables) that sorts the data into discrete
groups, and other parameters that affect eg. the sorting order or if a
particular facet is used in a particular context. 

Selectors and identificators
----------------------------

The grouping is primarily done through a *selector function*. The
selector function recieves three arguments:

* a dict with some basic information about one document,
* the name of the current facet (binding), and
* optionally some repo-dependent extra data in the form of an RDF graph.

It should return a single string. The selector is called once for
every document in the docrepo, and each document is sorted in one (or
more, see below) group identified by that string. As a simple example,
a selector may group documents into years of publication by finding
the date of the ``dcterms:issued`` property and extracting the year
part of it. The string returned by the should be suitable for end-user
display. 

Each facet also has a similar function called the *identificator
function*. It recieves the same arguments as the selector function,
but should return a string that is well suited for eg. a URI fragment,
ie. not contain spaces or non-ascii characters.

The :py:class:`~ferenda.Facet` class has a number of classmethods that
can act as selectors and/or identificators.

Contexts where facets are used
------------------------------

Table of contents
^^^^^^^^^^^^^^^^^

Each docrepo will have their own set of Table of contents pages. The
TOC for a docrepo will contain one set of pages for each defined
facet, unless ``use_for_toc`` is set to ``False``.

Atom feeds
^^^^^^^^^^

Each docrepo will have a set of feedsets, where each feedset is based
on a facet (only those that has the property ``use_for_feed`` set to
``True``). The structure of each feedset will mirror the structure of
each set of TOC pages, and re-uses the same selector and identificator
methods. It makes sense to have a separate feed for eg. each publisher
or subject matter in a repository that comprises a reasonable amount
of publishers and subject matters (using ``dcterms:publisher`` or
``dcterms:subject`` as the base for facets), but it does not make much
sense to eg. have a feed for all documents published in 1975 (using
``dcterms:published`` as the base for a facet). Therefore, the default
value for ``use_for_feed`` is ``False``.

Furthermore, a "main" feedset with a single feed containing
all documents is also constructed.

The feeds are always sorted by the updated property (most recent
updated first), taken from the corresponding
:py:class:`~ferenda.DocumentEntry` object.

The ReST API
^^^^^^^^^^^^

The ReST API uses all defined facets for all repos
simultaneously. This means that you can query eg. all documents
published in a certain year, and get results from all docrepos. This
requires that the defined facets don't clash, eg. that you don't have
two facets based on ``dcterms:publisher`` where one uses URI
references and the other uses.

The fulltext index
^^^^^^^^^^^^^^^^^^

The metadata that each facet uses is stored as a separate field in the
fulltext index. Facet can specify exactly how a particular facet
should be stored (ie if the field should be boosted in any particular
way). Note that the data stored in the fulltext index is not passed
through the selector function, the original RDF data is stored as-is.

Grouping a document in several groups
-------------------------------------

If a docrepo uses a facet that has ``multiple_values`` set to
``True``, it's possible for that facet to categorize the document in
more than one group (a typical usecase is documents that have multiple
``dcterms:subject`` keywords, or articles that have multiple
``dcterms:creator`` authors).


Combining facets from different docrepos
----------------------------------------

Facets that map to the same fulltextindex field must be equal. The
rules for equality: If the ``rdftype`` and the ``dimension_type`` and
``dimension_label`` and ``selector`` is equal, then the facets are
equal. ``selector`` functions are only equal if they are the same function
object, ie it's not just enough that they are two functions that work
identically.
