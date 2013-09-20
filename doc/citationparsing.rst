Citation parsing
================

The problem
-----------
- Body text of documents often contains valuable references to other documents in the same or related document collections
- Finding these resources and expressing them in the doc.body tree is universally a need
- Different document collections have different citation standards/tradition
- Regexes are often not enough

The built-in solution
---------------------

Ferenda uses the Pyparsing library in order to find and process
citations. As an example, consider RFC documents. They often have
three kinds of citations in the body of the text (examples come from
RFC 2616):

1. URL references, eg "GET http://www.w3.org/pub/WWW/TheProject.html HTTP/1.1"
2. IETF document references, eg "STD 3", "BCP 14" and "RFC 2068"
3. Internal endnote references, eg "[47]" and "[33]"

We'd like to make sure that any URL reference gets turned into a link
to that same URL, that any IETF document reference gets turned into
the canonical URI for that document, and that internal endote
references gets turned into document-relative links, eg "#endnote-47"
and "#endnote-33". (This requires that other parts of the parse()
process has created IDs for these in doc.body, which we assume has
been done).

Turning URL references in plain text into real links is so common that
ferenda has built-in support for this. The support comes in two parts:
First running a parser that detects URLs in the textual content, and
secondly, for each match, running a URL formatter on the parse result.

At the end of your parse() method, do the following.

.. literalinclude:: examples/citationparsing-urls.py

Extending the built-in support
------------------------------

.. literalinclude:: examples/citationparsing-parsers.py

This turns this document
  
.. literalinclude:: examples/citationparsing-before.xhtml

Into this document:

.. literalinclude:: examples/citationparsing-after.xhtml

Rolling your own
----------------

For more complicated situations you can skip calling
CitationParser.parse_recursive() and instead do your own processing
with the optional support of CitationParser.

This is needed in particular for complicated ParserElements which may
contain several sub-ParserElements which needs to be turned into
individual links. As an example, the text "under Article 56 (2),
Article 57 or Article 100a of the Treaty establishing the European
Community" may be matched by a single top-level ParseResult (and
probably must be, if "Article 56 (2)" is to actually reference article
56(2) in the Treaty), but should be turned in to three separate links.

In those cases, iterate through your doc.body yourself, and for each
text part do something like the following:

.. literalinclude:: examples/citationparsing-custom.py
       
     
