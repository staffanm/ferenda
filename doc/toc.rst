Customizing the table(s) of content
===================================

In order to make the processed documents in a docrepo accessible for a
website visitors, some sort of index or table of contents (TOC) that
lists all available documents must be created. It's often helpful to
create different lists depending on different facets of the
information in documents, eg. to sort document by title, publication
date, document status, author and similar properties.

Ferenda contains a number of methods that help with this task. The
general process has three steps:

1. Determine the criteria for how to group and sort all documents
2. Query the triplestore for basic information about all documents in
   the docrepo
3. Apply these criteria on the basic information from the database

It should be noted that you don't need to do anything in order to get
a very basic TOC. As long as your parse() step has extracted a
``dct:title`` string and optionally a ``dct:issued`` date for each
document, you'll get basic "Sorted by title" and "Sorted by date of
publication" TOCs for free. But if you've been a


Defining criteria for grouping and sorting
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

A criteria in this case is a method for grouping a set into documents
into distinct categories, then sorting the documents, as well as the
categories themseves.

Each criteria is represented by a TocCriteria object. If you want to
customize the table of contents, you have to provide a set of these
objects. You can do this in two different ways.

The first, simple, way is to specify a list of rdf predicates that
you've extracted when parsing your documents. Ferenda has some basic
knowledge about some common predicates and know how to construct
sensible TocCriteria objects for them -- ie. if you specify the
predicate ``dct:issued``, you get a TocCriteria object that groups
documents by year of publication and sorts each group by date of
publication.

To do this, you override
:meth:`~ferenda.DocumentRepository.toc_predicates` to something like
this:


.. literalinclude:: examples/toc.py
   :start-after: # begin predicates
   :end-before: # end predicates


The second, more complicated way is to override
:meth:`~ferenda.DocumentRepository.toc_criteria` and have it return a
list of instantiated :class:`~ferenda.TocCriteria` objects, like this
(equivalent to the above example, but with more possibilities for
customization):

.. literalinclude:: examples/toc.py
   :start-after: # begin criteria
   :end-before: # end criteria

The ``label`` and ``pagetitle`` parameters are useful to control the
headings and labels for the generated pages. They should hopefully be
self-explainatory.

The ``selector`` and ``key`` parameters should be functions (or any
callable -- like the lambda functions above) that accept a dictionary
of string values. These functions are called once each for each row in
the result set generated in the next step (see below) with the
contents of that row. They should each return a single string
value. The ``selector`` function should return the label of a group
that the document belongs to, i.e. the initial letter of the title, or
the year of a publication date. The ``key`` function should return a
value that will be used for sorting, i.e. for document titles it could
return the title without any leading "The", lowercased, spaces removed
etc.

Getting information about all documents
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

The next step is to perform a single SELECT query against the
triplestore that retrieves a single large table, where each document
is a row with a number of properties.

(This is different from the case of getting information related to a
particular document, in that case, a CONSTRUCT query that retrieves a
small RDF graph is used).

If you've used :meth:`~ferenda.DocumentRepository.toc_predicates`
above, a query that selects just those properties is automatically
constructed by the default implementation of toc_query. If you've
created your own :meth:`~ferenda.DocumentRepository.toc_criteria`
implementation, you might need to override this method as well. It
should return a string containing a valid SPARQL SELECT query.

It's called with the graph/context URI for the docrepo in question,
which you'll probably want to use in a SELECT FROM <> clause.

.. note::

   It's totally possible to both override
   :meth:`~ferenda.DocumentRepository.toc_predicates` and
   :meth:`~ferenda.DocumentRepository.toc_criteria`, and then let the
   default implementation of toc_query use the information from
   :meth:`~ferenda.DocumentRepository.toc_predicates` to automatically
   create a suitable query.

Each :class:`~ferenda.TocCriteria` object is initialized with a
binding parameter. The value of this parameter must match one of the
variable bindings specified in the SPARQL query.

.. note:: 

   Note to self: Do we really need the binding parameter to
   TocCriteria, now that the selector and key functions are provided
   with the entire dict of variable mappings?

Your selector and key functions expect a dict of string values. The
keys of this dict must match whatever variable bindings specified in
the SPARQL query as well.

Making the TOC pages
^^^^^^^^^^^^^^^^^^^^

The final step is to apply these criteria to the table of document
properties in order to create a set of static HTML5 pages. This is in
turn done in three different sub-steps, neither of which you'll have
to override.

The first sub-step, :meth:`~ferenda.DocumentRepository.toc_pagesets`,
applies the defined criteria to the data fetched from the triple store
to calculate the complete set of TOC pages needed for each criteria
(in the form of a :class:`~ferenda.TocPageset` object, filled with
:class:`~ferenda.TocPage` objects). If your criteria groups documents
by year of publication date, this method will yield one page for every
year that at least one document was published in.

The next sub-step,
:meth:`~ferenda.DocumentRepository.toc_select_for_pages`, applies the
criteria on the data again, and adds each document to the appropriate
:class:`~ferenda.TocPage` object.

The final sub-step transforms each of these :class:`~ferenda.TocPage`
objects into a HTML5 file. In the process, the method
:meth:`~ferenda.DocumentRepository.toc_item` is called for every
single document listed on every single TOC page. This method controls
how each document is presented when laid out. It's called with a
binding (same as used on each TocCriteria object) and a dict (same as
used on the ``selector`` and ``key`` functions), and is expected to
return a list of :mod:`~ferenda.elements` objects.

As an example, if you want to group by dct:identifier, but present
each document with dct:identifier + dct:title:

.. literalinclude:: examples/toc.py
   :start-after: # begin item
   :end-before: # end item

The generated TOC pages automatically get a visual representation of
each calculated TocPageset in the left navigational column.

The first page
^^^^^^^^^^^^^^

The main way in to each docrepos set of TOC pages is through the tabs
in the main header. That link goes to a special copy of the first page
in the first pageset. The order of criteria specified by
:meth:`~ferenda.DocumentRepository.toc_predicates` or
:meth:`~ferenda.DocumentRepository.toc_criteria` is therefore
important.

