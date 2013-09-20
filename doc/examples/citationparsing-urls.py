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
