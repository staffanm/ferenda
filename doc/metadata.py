from ferenda.sources import DocumentRepository

class ExampleDocrepo(DocumentRepository):

    # Basic way, using RDFLib API
    def parse_metadata_from_soup(self,soup,doc):
        from rdflib import Namespace, Literal, URIRef, RDF
        title = "My Document title" # or find it using the BeautifulSoup object passed
        authors = ["Fred Bloggs", "Joe Shmoe"] # ditto
        identifier = "Docno 2013:4711"
        pubdate = datetime.datetime(2013,1,6,10,8,0) # note that python types can be used

        # Set up commonly used namespaces
        RDF  = Namespace('http://www.w3.org/1999/02/22-rdf-syntax-ns#')
        DCT  = Namespace('http://purl.org/dc/terms/')
        PROV = Namespace('http://www.w3.org/ns/prov-o/')

        # Start setting metadata:

        # Mandatory - describe what type of thing this is. self.rdf_type
        # defaults to foaf:Document, but can be overridden by your
        # subclass
        doc.meta.add((URIRef(doc.uri), RDF['type'], self.rdf_type))

        # Optional - Make a note on what code generated this data
        doc.meta.add((URIRef(doc.uri), PROV['wasGeneratedBy'], Literal(self.qualified_class_name())))

        # Everything else is also optional, although dct:title is strongly
        # recommended
        doc.meta.add((URIRef(doc.uri), DCT['identifier'], Literal(identifier))
        # Note that we specify the language of the title. 
        doc.meta.add((URIRef(doc.uri), DCT['title'], Literal(title, lang=doc.lang))

        # Multiple values can be set for a specific metadata property
        for author in authors:
            doc.meta.add((URIRef(doc.uri), DCT['author'], Literal(author)))

    # Simpler way                   
    from rdflib Literal
    from ferenda import Describer
    def parse_metadata_from_soup(self,soup,doc):
        title = "My Document title"
        authors = ["Fred Bloggs", "Joe Shmoe"]
        identifier = "Docno 2013:4711"
        pubdate = datetime.datetime(2013,1,6,10,8,0)
        d = Describer(doc.meta, doc.uri)
        d.rdftype(self.rdf_type)
        d.value(self.ns['prov'].wasGeneratedBy, self.qualified_class_name())
        d.value(self.ns['dct'].title, Literal(title, lang=doc.lang))
        d.value(self.ns['dct'].identifier, identifier)
	for author in authors:
	    d.value(self.ns['dct'].author, author)
