# begin
from ferenda import CitationParser, URIFormatter, citationpatterns, uriformats
from ferenda.elements import Link

citparser = CitationParser()
citparser.add_grammar(citationpatterns.url)
formatter = URIFormatter(("url", uriformats.url))

res = []
text = "An example: http://example.org/. That is all."

for node in citparser.parse_string(text):
    if isinstance(node,str):
        # non-linked text, add and continue
        res.append(node)
    if isinstance(node, tuple):
        (text, match) = node
        uri = formatter.format(match)
        if uri:
            res.append(Link(uri, text, rel="dct:references"))
# end
return_value = True
