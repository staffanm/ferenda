Parsing and representing document metadata
==========================================

Every document has a number of properties, such as it's title,
authors, publication date, type and much more. These properties are
called metadata. Ferenda does not have a fixed set of which metadata
properties are available for any particular document type. Instead, it
encourages you to describe the document using RDF and any suitable
vocabulary (or vocabularies). If you are new to RDF, a good starting
point is the `RDF Primer <http://www.w3.org/TR/rdf-primer/>`_
document.

Each document has a ``meta`` property which initially is an empty
RDFLib :py:class:`~rdflib.graph.Graph` object. As part of the
:py:meth:`~ferenda.DocumentRepository.parse` method, you
should fill this graph with *triples* (metadata statements) about the
document.

.. _parsing-uri:

Document URI
------------

In order to create these metadata statements, you should first create a 
suitable URI for your document. Preferably, this should be a URI based
on the URL where your web site will be published, ie if you plan on 
publishing it on
``http://mynetstandards.org/``, a URI for RFC 4711 might be
``http://mynetstandards.org/res/rfc/4711`` (ie based on the base URL, the
docrepo alias, and the basefile). By changing the ``url`` variable in
your project configuration file, you can set the base URL from which
all document URIs are derived. If you wish to have more control over
the exact way URIs are constructed, you can override
:py:meth:`~ferenda.DocumentRepository.canonical_uri`. 

.. note::

   In some cases, there will be another *canonical URI* for the
   document you're describing, used by other people in other
   contexts. In these cases, you should specifiy that the metadata
   you're publishing is about the exact same object by adding a triple
   of the type ``owl:sameAs`` with that other canonical URI as value.

The URI for any document is available as a ``uri`` property.


Adding metadata using the RDFLib API
------------------------------------

With this, you can create metadata for your document using the RDFLib
Graph API.

.. literalinclude:: examples/metadata.py
   :start-after: # begin simple
   :end-before: # end simple


A simpler way of adding metadata
--------------------------------
	
The default RDFLib graph API is somewhat cumbersome for adding triples
to a metadata graph. Ferenda has a convenience wrapper,
:py:class:`~ferenda.Describer` (itself a subclass of
:py:class:`rdflib.extras.describer.Describer`) that makes this
somewhat easier. The ``ns`` class property also contains a number of
references to popular vocabularies. The above can be made more succint
like this:

.. literalinclude:: examples/metadata.py
   :start-after: # begin simple
   :end-before: # end simple

.. note::

   :meth:`~ferenda.DocumentRepository.parse_metadata_from_soup()`
   doesn't return anything. It only modifies the ``doc`` object passed
   to it.

Vocabularies
------------

Each RDF vocabulary is defined by a URI, and all terms (types and
properties) of that vocabulary is typically directly derived from
it. The vocabulary URI therefore acts as a namespace. Like namespaces
in XML, a shorter prefix is often assigned to the namespace so that
one can use ``rdf:type`` rather than
``http://www.w3.org/1999/02/22-rdf-syntax-ns#type``. The
DocumentRepository object keeps a dictionary of common
(prefix,namespace)s in the class property ``ns`` -- your code can
modify this list in order to add vocabulary terms relevant for your
documents.

Serialization of metadata
-------------------------

The :py:meth:`~ferenda.DocumentRepository.render_xhtml` method
serializes all information in ``doc.body`` and ``doc.meta`` to a
XHTML+RDFa file (the exact location given by
:py:meth:`~ferenda.DocumentStore.parsed_path`). The metadata specified
by doc.meta ends up in the ``<head>`` section of this XHTML file.

The actual RDF statements are also *distilled* to a separate RDF/XML
file found alongside this file (the location given by
:py:meth:`~ferenda.DocumentStore.distilled_path`) for
convenience.

.. _parsing-metadata-parts:


Metadata about parts of the document
------------------------------------

Just like the main Document object, individual parts of the document
(represented as :py:mod:`ferenda.elements` objects) can have ``uri``
and ``meta`` properties. Unlike the main Document objects, these
properties are not initialized beforehand. But if you do create these
properties, they are used to serialize metadata into RDFa properties
for each

.. literalinclude:: examples/metadata.py
   :start-after: # begin part
   :end-before: # end part

This results in the following document fragment:

.. literalinclude:: examples/metadata-result.xml
