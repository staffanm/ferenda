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

.. code-block:: py

  from ferenda.citationparser import CitationParser
  from ferenda.uriformatter import URIFormatter
  import ferenda.citations
  import ferenda.uri

  # CitationParser is initialized with a list of pyparsing
  # ParserElements (or any other object that has a scanString method
  # that returns a generator of (tokens,start,end) tuples, where start
  # and end are integer string indicies and tokens are dict-like
  # objects)
  citparser = CitationParser(ferenda.citations.urls)

  # URIFormatter is initialized with a list of tuples, where each
  # tuple is a string (identifying a named ParseResult) and a function
  # (that takes as a single argument a dict-like object and returns a
  # URI string (possibly relative)
  citparser.set_formatter(URIFormatter(("URLRef", ferenda.uri.urls)

  citparser.parse_recursive(doc.body)


Extending the built-in support
------------------------------

.. code-block:: py

  from ferenda.citationparser import CitationParser
  from ferenda.uriformatter import URIFormatter
  
  import ferenda.citations
  import ferenda.uri

  # Create two ParserElements for 
  rfc_citation = "RFC" + Word(nums).setResultName("RFCRef")
  bcp_citation = "BCP" + Word(nums).setResultName("BCPRef")
  std_citation = "STD" + Word(nums).setResultName("STDRef")
  ietf_doc_citation = rfc_citation | bcp_citation | std_citation
  
  endnote_citation = "[" + Word(nums).setResultName("EndnoteRef") + "]"


  # Create a URI formatter for IETF documents (The URI formatters for
  # endnotes is so simple that we just use a lambda function below
  def rfc_uri_formatter(parts):
      # parts is a dict-like object created from the named result parts
      # of our grammar, eg those ParserElement for which we've called
      # .setResultName(), in this case eg. {'RFCRef':'2068'}

      # NOTE: If your document collection contains documents of this
      # type and you're republishing them, feel free to change these
      # URIs to URIs under your control,
      # eg. "http://mynetstandards.org/rfc/%(RFCRef)s/" and so on
      if 'RFCRef' in parts:
            return "http://www.ietf.org/rfc/rfc%(RFCRef)s.txt" % parts
      elif 'BCPRef' in parts:
            return "http://tools.ietf.org/rfc/bcp/bcp(BCPRef)s.txt" % parts
      elif 'STDRef' in parts:
            return "http://rfc-editor.org/std/std(STDRef)s.txt" % parts
      else:
            return None

  # CitationParser is initialized with a list of pyparsing
  # ParserElements (or any other object that has a scanString method
  # that returns a generator of (tokens,start,end) tuples, where start
  # and end are integer string indicies and tokens are dict-like
  # objects)
  citparser = CitationParser(ferenda.citations.urls,
                             rfc_citation,
  			     endnote_citation)

  # URIFormatter is initialized with a list of tuples, where each
  # tuple is a string (identifying a named ParseResult) and a function
  # (that takes as a single argument a dict-like object and returns a
  # URI string (possibly relative)
  citparser.set_formatter(URIFormatter(("URLRef", ferenda.uri.urls),
                                        ("RFCRef", rfc_uri_formatter),
                                        ("EndnoteRef", lambda d: "#endnote-%(EndnoteRef)s" % d)))


  citparser.parse_recursive(doc.body)

This turns this document
  
.. code-block:: html

  <body>
     <h1>Main document</h1>
     <p>A naked URL: http://www.w3.org/pub/WWW/TheProject.html.</p>
     <p>Some IETF document references: See STD 3, BCP 14 and RFC 2068.</p>
     <p>An internal endnote reference: ...relevance ranking, cf. [47]</p>
     <h2>References</h2>
     <p id="47">47: Malmgren, Towards a theory of jurisprudential ranking</p>
  </body>

Into this document (FIXME add rel attributes to all links, unless RDFa
1.1 has some magic for this?)

.. code-block:: html
  
  <body>
     <h1>Main document</h1>
     <p>
       A naked URL: <a href="http://www.w3.org/pub/WWW/TheProject.html"
       >http://www.w3.org/pub/WWW/TheProject.html</a>.
     </p>
     <p>
       Some IETF document references: See <a
       href="http://rfc-editor.org/std/std3.txt">STD 3</a>, <a
       href="http://tools.ietf.org/rfc/bcp/bcp14.txt">BCP 14</a> and
       <a href="http://www.ietf.org/rfc/rfc2068s.txt">RFC
       2068</a>.
     </p>
     <p>
       An internal endnote reference: ...relevance ranking, cf. <a
       href="#endnote-47">[47]</a>
     </p>
     <h2>References</h2>
     <p id="endnote-47">47: Malmgren, Towards a theory of jurisprudential ranking</p>
  </body>


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

.. code-block:: py

   citparser = CitationParser()
   citparser.addgrammar(....)
   citparser.addgrammar(....)
   
   for node in citparser.parse_string(text):
       if isinstance(node,str):
           # non-linked text, add and continue
       if isinstance(node, pyparsingResult):
           node = self.resolve_relative(node,currentloc)
	   uri = uriformatter.format(node)
	   if uri:
	       res.add(Link(uri,node.text,rel="dct:references"))
   
       
     
