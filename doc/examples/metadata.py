from ferenda import DocumentRepository

class ExampleDocrepo(DocumentRepository):

    # Basic way, using RDFLib API
# begin basic
    def parse_metadata_from_soup(self,soup,doc):
        from rdflib import Namespace, Literal, URIRef
        from datetime import datetime
        title = "My Document title" # or find it using the BeautifulSoup object passed
        authors = ["Fred Bloggs", "Joe Shmoe"] # ditto
        identifier = "Docno 2013:4711"
        # note that python types can be used:
        pubdate = datetime(2013,1,6,10,8,0) 

        # Set up commonly used namespaces
        RDF  = Namespace('http://www.w3.org/1999/02/22-rdf-syntax-ns#')
        DCT  = Namespace('http://purl.org/dc/terms/')
        PROV = Namespace('http://www.w3.org/ns/prov-o/')
        # convert doc.uri to a RDFLib URIRef
        docuri = URIRef(doc.uri)

        # Start setting metadata:

        # Mandatory - describe what type of thing this is. self.rdf_type
        # defaults to foaf:Document, but can be overridden by your
        # subclass
        doc.meta.add((docuri, RDF.type, self.rdf_type))

        # Optional - Make a note on what code generated this data
        doc.meta.add((docuri, PROV.wasGeneratedBy, Literal(self.qualified_class_name())))

        # Everything else is also optional, although dct:title is strongly
        # recommended
        doc.meta.add((docuri, DCT.identifier, Literal(identifier)))
        # Note that we specify the language of the title. 
        doc.meta.add((docuri, DCT.title, Literal(title, lang=doc.lang)))
        # Multiple values can be set for a specific metadata property
        for author in authors:
            doc.meta.add((docuri, DCT.author, Literal(author)))
# end basic

class DescriberDocrepo(DocumentRepository):
# begin simpler                     
    # Simpler way                   
    def parse_metadata_from_soup(self, soup, doc):
        from ferenda import Describer
        from datetime import datetime
        title = "My Document title"
        authors = ["Fred Bloggs", "Joe Shmoe"]
        identifier = "Docno 2013:4711"
        pubdate = datetime(2013,1,6,10,8,0)
        d = Describer(doc.meta, doc.uri)
        d.rdftype(self.rdf_type)
        d.value(self.ns['prov'].wasGeneratedBy, self.qualified_class_name())
        d.value(self.ns['dct'].title, title, lang=doc.lang)
        d.value(self.ns['dct'].identifier, identifier)
        for author in authors:
            d.value(self.ns['dct'].author, author)
# end simpler

# begin part
    def parse_document_from_soup(self, soup, doc):
        from ferenda.elements import Page
        from ferenda import Describer
        part = Page(["This is a part of a document"],
                    ordinal=42,
                    uri="http://example.org/doc#42",
                    meta=self.make_graph())
        d = Describer(part.meta, part.uri)
        d.rdftype(self.ns['bibo'].DocumentPart)
        # the dct:identifier for a document part is often whatever
        # would be the preferred way to cite that part in another
        # document
        d.value(self.ns['dct'].identifier, "Doc:4711, p 42")
# end part                     

# test running code
                    
d = ExampleDocrepo()
doc = d.make_document("doc1")                    
d.parse_metadata_from_soup(None, doc)

d = DescriberDocrepo()
d.parse_metadata_from_soup(None, doc)
d.parse_document_from_soup(None, doc)
return_value = True
                    
