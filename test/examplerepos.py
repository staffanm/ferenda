# builtin
from datetime import date, datetime

# 3rd party
from rdflib import Graph, Namespace, URIRef
from rdflib.namespace import RDF, DC, DCTERMS
SCHEMA = Namespace("http://schema.org/")

# mine 
from ferenda import DocumentRepository, DocumentStore, Facet, fulltextindex, decorators

################################################################
# This module implements six docrepos, that are used all over the test
# suite.
#
# - DocRepo1 - Uses standard facets, has testdata that groups nicely into those facets
# - DocRepo2 - Defines a couple of extra facets and has testdata for those
# - DocRepo3 - Has non-standard type definitions/data for facets
# - staticmockclass - Has no-op implementations of standard methods
# - staticmockclass2 - Has custom method
# - staticmockclass3 - Has custom footer() implementation


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
    namespaces = ['rdf', 'rdfs', 'xsd', 'xsi', 'dcterms', 'foaf',
                  ('ex', 'http://example.org/vocab/')]
    rdf_type = (URIRef('http://example.org/vocab/MainType'),
                URIRef('http://example.org/vocab/OtherType'),
                URIRef('http://example.org/vocab/YetOtherType'))
                
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
    namespaces = ['rdf', 'rdfs', 'xsd', 'xsi', 'dcterms', 'dc', 'schema', 'foaf',
                  ('ex', 'http://example.org/vocab/')]
    rdf_type = URIRef('http://example.org/vocab/MainType')
    def is_april_fools(self, row, binding, resource_graph):
        # during relate_fulltext, row[binding] may be an actual datetime
        d = row[binding]
        if not isinstance(d, date):
            d = datetime.strptime(d, "%Y-%m-%d")
        return (d.month == 4 and d.day == 1)
        # this selector sorts into True/False buckets
        
    def facets(self):
        return [Facet(RDF.type),       # fulltextindex.URI
                Facet(DCTERMS.title),      # fulltextindex.Text(boost=4)
                Facet(DCTERMS.identifier), # fulltextindex.Label(boost=16)
                Facet(DCTERMS.issued),     # fulltextindex.Datetime()
                Facet(DCTERMS.issued,
                      indexingtype=fulltextindex.Boolean(),
                      selector=self.is_april_fools,
                      dimension_type="value",
                      dimension_label="aprilfools"),
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
    namespaces = ['rdf', 'rdfs', 'xsd', 'xsi', 'dcterms', 'dc', 'schema', 'foaf',
                      ('ex', 'http://example.org/vocab/')]
    rdf_type = URIRef('http://example.org/vocab/MainType')
    def my_id_selector(self, row, binding, graph):
        # categorize each ID after the number of characters in it
        return str(len(row[binding]))

    def lexicalkey(self, row, binding): # , graph
        return "".join(row[binding].lower().split())

    def facets(self):
        
        # note that RDF.type is not one of the facets
        return [Facet(DC.publisher),
                # Having a dcterms_issued of type string conflicts
                # with other repos havind dcterms_issued of type date
                # (range queries against the field does not work). Disable for now.
                # Facet(DCTERMS.issued, indexingtype=fulltextindex.Label()),
                Facet(DCTERMS.rightsHolder, indexingtype=fulltextindex.Resource(), multiple_values=True),
                Facet(DCTERMS.title, toplevel_only=True),
                Facet(DCTERMS.identifier, selector=self.my_id_selector, key=self.lexicalkey, label="IDs having %(selected) characters"),
                Facet(DC.creator, toplevel_only=False)]



class staticmockstore(DocumentStore):
    def list_basefiles_for(cls,action):
        return ["arg1","myarg","arg2"]

class staticmockclass(DocumentRepository):
    """Example class for testing"""
    alias = "staticmock"
    resourcebase = None
    documentstore_class = staticmockstore
    namespaces = ('foaf', 'rdfs', 'rdf', 'owl', 'skos')
    
    @decorators.action
    def mymethod(self, arg):
        """Frobnicate the bizbaz"""
        if arg == "myarg":
            return "ok!"

    def download(self):
        return "%s download ok" % self.alias

    def parse(self, basefile):
        return "%s parse %s" % (self.alias, basefile)

    def relate(self, basefile):
        return "%s relate %s" % (self.alias, basefile)

    def generate(self, basefile): 
        return "%s generate %s" % (self.alias, basefile)

    def toc(self): 
        return "%s toc ok" % (self.alias)

    def news(self): 
        return "%s news ok" % (self.alias)

    def internalmethod(self, arg):
        pass

    @classmethod
    def setup(cls, action, config): pass
    @classmethod
    def teardown(cls, action, config): pass
        
        
    def get_default_options(self):
        opts = super(staticmockclass, self).get_default_options()
        opts.update({'datadir': 'data',
                     'loglevel': 'DEBUG',
                     'cssfiles': [self.resourcebase + '/test.css'],
                     'imgfiles': [self.resourcebase + '/test.png'],
                     'jsfiles': [self.resourcebase + '/test.js']})
        return opts
                    
    
    
class staticmockclass2(staticmockclass):
    """Another class for testing"""
    alias="staticmock2"
    def mymethod(self, arg):
        """Frobnicate the bizbaz (alternate implementation)"""
        if arg == "myarg":
            return "yeah!"

class staticmockclass3(staticmockclass):
    """Yet another (overrides footer())"""
    alias="staticmock3"
    def footer(self):
        return (("About", "http://example.org/about"),
                ("Legal", "http://example.org/legal"),
                ("Contact", "http://example.org/contact")
        )
    
