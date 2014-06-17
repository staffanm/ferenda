# 3rd party
from rdflib import Graph, Namespace
from rdflib.namespace import RDF, DC, DCTERMS
SCHEMA = Namespace("http://schema.org/")

# mine 
from ferenda import DocumentRepository, Facet, fulltextindex

class DocRepo1(DocumentRepository):
    # this has the default set of facets (rdf:type, dcterms:title,
    # dcterms:publisher, dcterms:issued) and a number of documents such as
    # each bucket in the facet has 2-1-1 facet values
    # 
    #   rdf:type         dcterms:title   dcterms:publisher dcterms:issued
    # A ex:MainType     "A simple doc"   ex:publ1          2012-04-01
    # B ex:MainType     "Other doc"      ex:publ2          2013-06-06
    # C ex:OtherType    "More docs"      ex:publ2          2014-05-06
    # D ex:YetOtherType "Another doc"    ex:publ3          2014-09-23
    alias = "repo1"
    @property
    def commondata(self):
        return Graph().parse(format="turtle", data="""
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix skos: <http://www.w3.org/2004/02/skos/core#> .
@prefix dcterms: <http://purl.org/dc/terms/> .
@prefix foaf: <http://xmlns.com/foaf/0.1/> .

<http://example.org/vocab/publ1> a foaf:Organization ;
    rdfs:label "Publishing & sons"@en .
<http://example.org/vocab/publ2> a foaf:Organization ;
    skos:prefLabel "Bookprinters and associates"@en .
<http://example.org/vocab/publ3> a foaf:Organization ;
    skos:altLabel "BP&A"@en .
<http://example.org/vocab/publ4> a foaf:Organization ;
    dcterms:title "A title is not really a name for an org"@en .
<http://example.org/vocab/company1> a foaf:Organization ;
    dcterms:alternative "Comp Inc"@en .
<http://example.org/vocab/company2> a foaf:Organization ;
    foaf:name "Another company"@en .
#company3 has no label
#<http://example.org/vocab/company3> a foaf:Organization ;
#    foaf:name "A third company"@en .
        """)
        

class DocRepo2(DocRepo1):
    # this repo contains facets that excercize all kinds of fulltext.IndexedType objects
    alias = "repo2"
    namespaces = ['rdf', 'rdfs', 'xsd', 'xsi', 'dcterms', 'dc', 'schema']

    def is_april_fools(self, row, binding):
        return (len(row[binding]) == 10 and # Full YYYY-MM-DD string
                row[binding][5:] == "04-01") # 1st of april
        # this selector sorts into True/False buckets
        
    def facets(self):
        return [Facet(RDF.type),       # fulltextindex.URI
                Facet(DCTERMS.title),      # fulltextindex.Text(boost=4)
                Facet(DCTERMS.identifier), # fulltextindex.Label(boost=16)
                Facet(DCTERMS.issued),     # fulltextindex.Datetime()
                Facet(DCTERMS.issued, selector=self.is_april_fools),     # fulltextindex.Datetime()
                Facet(DCTERMS.publisher),  # fulltextindex.Resource()
                Facet(DC.subject),     # fulltextindex.Keywords()
                Facet(SCHEMA.free)     # fulltextindex.Boolean()
                ]

class DocRepo3(DocRepo1):
    # this repo contains custom facets with custom selectors/keys,
    # unusual predicates like DC.publisher, and non-standard
    # configuration like a title not used for toc (and toplevel only)
    # or DCTERMS.creator for each subsection, or DCTERMS.publisher w/ multiple=True
    alias = "repo3"
    namespaces = ['rdf', 'rdfs', 'xsd', 'xsi', 'dcterms', 'dc', 'schema']

    def my_id_selector(self, row, binding, graph):
        # categorize each ID after the number of characters in it
        return str(len(row[binding]))

    def lexicalkey(self, row, binding): # , graph
        return "".join(row[binding].lower().split())

    def facets(self):
        
        # note that RDF.type is not one of the facets
        return [Facet(DC.publisher),
                Facet(DCTERMS.issued, indexingtype=fulltextindex.Label()),
                Facet(DCTERMS.rightsHolder, indexingtype=fulltextindex.Resource(), multiple_values=True),
                Facet(DCTERMS.title, toplevel_only=True),
                Facet(DCTERMS.identifer, selector=self.my_id_selector, key=self.lexicalkey, label="IDs having %(selected) characters"),
                Facet(DC.creator, toplevel_only=False)]

