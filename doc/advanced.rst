Advanced topics
===============


Composite docrepos
------------------

In some cases, a document collection may available from multiple
sources, with varying degrees of completeness and/or quality. For
example, in a collection of US patents, some patents may be available
in structured XML with good metadata through a easy-to-use API, some
in tag-soup style HTML with no metadata, requiring screenscraping, and
some in the form of TIFF files that you scanned yourself. The
implementation of both download() and parse() will differ wildly for
these sources. You'll have something like this:

.. literalinclude:: examples/patents.py
   :start-after: # begin subrepos
   :end-before: # end subrepos

But since the result of all three parse() implementations are
XHTML1.1+RDFa documents (possibly with varying degrees of data
fidelity), the implementation of generate() will be substantially the
same. Furthermore, you probably want to present a unified document
collection to the end user, presenting documents derived from
structured XML if they're available, documents derived from tagsoup
HTML if an XML version wasn't available, and finally documents derived
from your scanned documents if nothing else is available.

The class :class:`~ferenda.CompositeRepository` makes this
possible. You specify a number of subordinate docrepo classes using
the ``subrepos`` class property.

.. literalinclude:: examples/patents.py
   :start-after: # begin composite
   :end-before: # end composite
  
The CompositeRepository docrepo then acts as a proxy for all of your
specialized repositories:

.. literalinclude:: examples/composite-repository.sh
   :start-after: # begin example
   :end-before: # end example
	      
Note that ``patents.XMLPatents`` and the other subrepos are never
registered in ferenda.ini``. They're just called behind-the-scenes by
``patents.CompositePatents``.


Patch files
-----------

It is not uncommon that source documents in a document repository
contains formatting irregularities, sensitive information that must be
redacted, or just outright errors. In some cases, your parse
implementation can detect and correct these things, but in other
cases, the irregularities are so uncommon or unique that this is not
possible to do in a general way.

As an alternative, you can patch the source document (or it's
intermediate representation) before the main part of your parsing
logic.

The method :meth:`~ferenda.DocumentRepository.patch_if_needed`
automates most of this work for you. It expects a basefile and the
corresponding source document as a string, looks in a *patch
directory* for a corresponding patch file, and applies it if found.

By default, the patch directory is alongside the data directory. The
patch file for document foo in repository bar should be placed in
``patches/bar/foo.patch``. An optional description of the patch (as a
plaintext, UTF-8 encoded file) can be placed in
``patches/bar/foo.desc``.


:meth:`~ferenda.DocumentRepository.patch_if_needed` returns a tuple
(text, description). If there was no available patch, text is
identical to the text passed in and description is None. If there was
a patch available and it applied cleanly, text is the patched text and
description is a description of the patch (or "(No patch description
available)"). If there was a patch, but it didn't apply cleanly, a
PatchError is raised.

.. note:: 

   There is a ``mkpatch`` command in the Devel class which aims to
   automate the creation of patch files. It does not work at the
   moment.


External annotations
--------------------

Ferenda contains a general docrepo class that fetches data from a
separate MediaWiki server and stores this as annotations/descriptions
related to the documents in your main docrepos. This makes it possible
to present a source document and commentary on it (including
annotations about individual sections) side-by-side.

See :class:`ferenda.sources.general.MediaWiki`


Keyword hubs
------------

Ferenda also contains a general docrepo class that lists all keywords
used by documents in your main docrepos (by default, it looks for all
``dcterms:subject`` properties used in any document) and generate
documents for each of them. These documents have no content of their
own, but act as hub pages that list all documents that use a certain
keyword in one place.

When used together with the MediaWiki module above, this makes it
possible to write editorial descriptions about each keyword used, that
is presented alongside the list of documents that use that keyword.

See :class:`ferenda.sources.general.Keyword`

.. _custom-common-data:

Custom common data
------------------

In many cases, you want to describe documents using references to
other things that are not documents, but which should be named using
URIs rather than plain text literals. This includes things like
companies, publishing entities, print series and abstract things like
the topic/keyword of a document. You can define a RDF graph containing
more information about each such thing that you know of beforehand, eg
if we want to model that some RFCs are published in the Internet
Architecture Board (IAB) stream, we can define the following small
graph::

    <http://localhost:8000/ext/iab> a foaf:Organization;
        foaf:name "Internet Architecture Board (IAB)";
        skos:altLabel "IAB";
        foaf:homepage <https://www.iab.org/> .

If this is placed in ``res/extra/[alias].ttl``, eg
``res/extra/rfc.ttl``, the graph is made available as
:py:data:`~ferenda.DocumentRepository.commondata`, and is also
provided as the third ``resource_graph`` argument to any selector/key
functions of your :py:class:`~ferenda.Facet` objects.


.. _custom-ontologies:

Custom ontologies
-----------------

Some parts of ferenda, notably :doc:`restapi`, can make use of
ontologies that your docrepo uses. This is so far only used to provide
human-readable descriptions of predicates used (as determined by
``rdfs:label`` or ``rdfs:comment``). Ferenda will try to find an
ontology for any namespace you use in
:py:data:`~ferenda.DocumentRepository.namespaces`, and directly
supports many common vocabularies (``bibo``, ``dc``, ``dcterms``,
``foaf``, ``prov``, ``rdf``, ``rdfs``, ``schema`` and ``skos``). If
you have defined your own custom ontology, place it (in Turtle format)
as ``res/vocab/[alias].ttl``, eg. ``res/vocab/rfc.ttl`` to make
Ferenda read it.

Parallel processing
-------------------

It's common to use ferenda with document collections with tens of
thousands of documents. If a single document takes a second to parse,
it means the entire document collection will take three hours or more,
which is not ideal for quick turnaround. Ferenda, and in particular
the ``ferenda-build.py`` tool, can run tasks in parallel to speed
things up.

Multiprocessing on a single machine
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

The simplest way of speeding up processing is to use the ``processes``
parameter, eg::

    ./ferenda-build.py rfc parse --all --processes=4

This will create 4 processes (started by a fifth control proccess),
each processing individual documents as instructed by the control
process. As a rule of thumb, you should create as many processes as
you have CPU cores.


Distributed processing
^^^^^^^^^^^^^^^^^^^^^^

A more complex, but also more scalable way, is to set up a bunch of
computers acting as processing clients, together with a main (control)
system. Each of these clients must have access to the same code and
data directory as the main system (ie they should all mount the same
network file system). On each client, you then run (assuming that your
main system has the IP address 192.168.1.42, and that this particular
client has 4 CPU cores)::

    ./ferenda-build.py all buildclient --serverhost=192.168.1.42 --processes=4

On the main system, you first start a message queue with::

    ./ferenda-build.py all buildqueue

Then you can run ``ferenda-build.py`` as normal but with the ``buildqueue``
parameter, eg::

    ./ferenda-build rfc parse --all --buildqueue
    
This will put each file to be processed in the message queue, where
all clients will pick up these jobs and process them.
    
The clients and the message queue can be kept running indefinitely
(although the clients will need to be restarted when you change the
code that they're running).

If you're not running ferenda on windows, you can skip the separate
message queue process. Just start your clients like above, then start
``ferenda-build.py`` on your main system with the ``buildserver`` parameter,
eg::

    ./ferenda-build.py rfc parse --all --buildserver

This sets up a temporary in-subprocess message queue that your clients
will connect to as soon as it's up.

.. note::

   Because of reasons, this in-subprocess queue does not work on
   Windows. On that platform you'll need to run the message queue
   separately, as described initially.
