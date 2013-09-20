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
