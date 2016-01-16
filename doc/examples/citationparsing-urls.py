# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from ferenda.compat import Mock
from ferenda.elements.html import elements_from_soup
from bs4 import BeautifulSoup
doc = Mock()
doc.body = elements_from_soup(BeautifulSoup("""<html>
<body>
URLs often appear like http://example.org/foo, in running text
</body>
</html>""", "lxml").body)
# begin
from ferenda import CitationParser
from ferenda import URIFormatter
import ferenda.citationpatterns
import ferenda.uriformats

# CitationParser is initialized with a list of pyparsing
# ParserElements (or any other object that has a scanString method
# that returns a generator of (tokens,start,end) tuples, where start
# and end are integer string indicies and tokens are dict-like
# objects)
citparser = CitationParser(ferenda.citationpatterns.url)

# URIFormatter is initialized with a list of tuples, where each
# tuple is a string (identifying a named ParseResult) and a function
# (that takes as a single argument a dict-like object and returns a
# URI string (possibly relative)
citparser.set_formatter(URIFormatter(("URLRef", ferenda.uriformats.url)))

citparser.parse_recursive(doc.body)
# end 
return_value = True
