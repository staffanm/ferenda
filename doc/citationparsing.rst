Citation parsing
================

In many cases, the text in the body of a document contains references
(citations) to other documents in the same or related document
collections. A good implementation of a document repository needs to
find and express these references. In ferenda, references are
expressed as basic hyperlinks which uses the ``rel`` attribute to
specify the sort of relationship that the reference expresses. The
process of citation parsing consists of analysing the raw text,
finding references within that text, constructing sensible URIs for
each reference, and formatting these as ``<a href="..."
rel="...">[citation]</a>`` style links.

Since standards for expressing references / citations are very
diverse, Ferenda requires that the docrepo programmer specifies the
basic rules of how to recognize a reference, and how to put together
the properties from a reference (such as year of publication, or page)
into a URI.

The built-in solution
---------------------

Ferenda uses the `Pyparsing <http://pyparsing.wikispaces.com/>`_
library in order to find and process citations. As an example, we'll
specify citation patterns and URI formats for references that occurr
in RFC documents. These are primarily of three different kinds
(examples come from RFC 2616):

1. URL references, eg "GET http://www.w3.org/pub/WWW/TheProject.html HTTP/1.1"
2. IETF document references, eg "STD 3", "BCP 14" and "RFC 2068"
3. Internal endnote references, eg "[47]" and "[33]"

We'd like to make sure that any URL reference gets turned into a link
to that same URL, that any IETF document reference gets turned into
the canonical URI for that document, and that internal endote
references gets turned into document-relative links, eg "#endnote-47"
and "#endnote-33". (This requires that other parts of the
:meth:`~ferenda.DocumentRepository.parse` process has created IDs for
these in ``doc.body``, which we assume has been done).

Turning URL references in plain text into real links is so common that
ferenda has built-in support for this. The support comes in two parts:
First running a parser that detects URLs in the textual content, and
secondly, for each match, running a URL formatter on the parse result.

At the end of your :meth:`~ferenda.DocumentRepository.parse` method,
do the following.

.. literalinclude:: examples/citationparsing-urls.py
   :language: python		     
   :start-after: # begin       
   :end-before: # end

The :meth:`~ferenda.CitationParser.parse_recursive` takes any
:mod:`~ferenda.elements` document tree and modifies it in-place to
mark up any references to proper :class:`~ferenda.elements.Link`
objects.

Extending the built-in support
------------------------------

Building your own citation patterns and URI formats is fairly
simple. First, specify your patterns in the form of a pyparsing
parseExpression, and make sure that both the expression as a whole,
and any individual significant properties, are named by calling
``.setResultName``.

Then, create a set of formatting functions that takes the named
properties from the parse expressions above and use them to create a
URI.

Finally, initialize a :class:`~ferenda.CitationParser` object from
your parse expressions and a :class:`~ferenda.URIFormatter` object
that maps named parse expressions to their corresponding URI
formatting function, and call
:meth:`~ferenda.CitationParser.parse_recursive`

.. literalinclude:: examples/citationparsing-parsers.py
   :start-after: # begin       
   :end-before: # end

This turns this document
  
.. literalinclude:: examples/citationparsing-before.xhtml
   :language: html

Into this document:

.. literalinclude:: examples/citationparsing-after.xhtml
   :language: html

Rolling your own
----------------

For more complicated situations you can skip calling
:meth:`~ferenda.CitationParser.parse_recursive` and instead do your
own processing with the optional support of
:class:`~ferenda.CitationParser`.

This is needed in particular for complicated ``ParserElement`` objects
which may contain several sub-``ParserElement`` which needs to be
turned into individual links. As an example, the text "under Article
56 (2), Article 57 or Article 100a of the Treaty establishing the
European Community" may be matched by a single top-level ParseResult
(and probably must be, if "Article 56 (2)" is to actually reference
article 56(2) in the Treaty), but should be turned in to three
separate links.

In those cases, iterate through your ``doc.body`` yourself, and for each
text part do something like the following:

.. literalinclude:: examples/citationparsing-custom.py
   :start-after: # begin       
   :end-before: # end
     
