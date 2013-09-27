# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from ferenda.compat import Mock
from ferenda.elements.html import elements_from_soup
from bs4 import BeautifulSoup

doc = Mock()
doc.body = elements_from_soup(BeautifulSoup(open("doc/examples/citationparsing-before.xhtml").read()).body)

# begin
from pyparsing import Word, nums

from ferenda import CitationParser
from ferenda import URIFormatter
import ferenda.citationpatterns
import ferenda.uriformats

# Create two ParserElements for IETF document references and internal
# references
rfc_citation = "RFC" + Word(nums).setResultsName("RFCRef")
bcp_citation = "BCP" + Word(nums).setResultsName("BCPRef")
std_citation = "STD" + Word(nums).setResultsName("STDRef")
ietf_doc_citation = (rfc_citation | bcp_citation | std_citation).setResultsName("IETFRef")

endnote_citation = ("[" + Word(nums).setResultsName("EndnoteID") + "]").setResultsName("EndnoteRef")

# Create a URI formatter for IETF documents (URI formatter for endnotes
# is so simple that we just use a lambda function below
def rfc_uri_formatter(parts):
    # parts is a dict-like object created from the named result parts
    # of our grammar, eg those ParserElement for which we've called
    # .setResultsName(), in this case eg. {'RFCRef':'2068'}

    # NOTE: If your document collection contains documents of this
    # type and you're republishing them, feel free to change these
    # URIs to URIs under your control,
    # eg. "http://mynetstandards.org/rfc/%(RFCRef)s/" and so on
    if 'RFCRef' in parts:
          return "http://www.ietf.org/rfc/rfc%(RFCRef)s.txt" % parts
    elif 'BCPRef' in parts:
          return "http://tools.ietf.org/rfc/bcp/bcp%(BCPRef)s.txt" % parts
    elif 'STDRef' in parts:
          return "http://rfc-editor.org/std/std%(STDRef)s.txt" % parts
    else:
          return None

# CitationParser is initialized with a list of pyparsing
# ParserElements (or any other object that has a scanString method
# that returns a generator of (tokens,start,end) tuples, where start
# and end are integer string indicies and tokens are dict-like
# objects)
citparser = CitationParser(ferenda.citationpatterns.url,
                           ietf_doc_citation,
                           endnote_citation)

# URIFormatter is initialized with a list of tuples, where each
# tuple is a string (identifying a named ParseResult) and a function
# (that takes as a single argument a dict-like object and returns a
# URI string (possibly relative)
citparser.set_formatter(URIFormatter(("url", ferenda.uriformats.url),
                                      ("IETFRef", rfc_uri_formatter),
                                      ("EndnoteRef", lambda d: "#endnote-%(EndnoteID)s" % d)))

citparser.parse_recursive(doc.body)

# end
from lxml import etree
return_value = etree.tostring(doc.body.as_xhtml("http://example.org/doc/"))
