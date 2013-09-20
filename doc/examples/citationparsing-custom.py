citparser = CitationParser()
citparser.addgrammar()
citparser.addgrammar()

for node in citparser.parse_string(text):
    if isinstance(node,str):
        # non-linked text, add and continue
    if isinstance(node, pyparsingResult):
        node = self.resolve_relative(node,currentloc)
        uri = uriformatter.format(node)
        if uri:
            res.add(Link(uri,node.text,rel="dct:references"))
