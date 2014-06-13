# -*- coding: utf-8 -*-
from __future__ import unicode_literals

# sys
import json
import os
import sys
import codecs
import shutil

# 3rd party
from rdflib import Graph, Namespace, URIRef
from rdflib.namespace import RDF, DC, DCTERMS
SCHEMA = Namespace("http://schema.org/")

# mine
from testWSGI import WSGI  # provides the nice call_wsgi func
from ferenda import DocumentRepository, FulltextIndex, Facet
from ferenda import util, fulltextindex

class BasicAPI(object):
    # is called by WSGI.setUp
    def put_files_in_place(self):
        self.repo = None
        self.repos = [DocumentRepository(datadir=self.datadir,
                                         storetype = self.storetype,
                                         storelocation = self.storelocation,
                                         storerepository = self.storerepository,
                                         indextype = self.indextype,
                                         indexlocation = self.indexlocation)]
        # create three basic documents (at parsed and distilled)
        #
        # each document should have a dcterms:title, a dcterms:issued and a
        # dcterms:publisher, which has a URI
        #
        # basefile  dcterms:title  dcterms:issued  dcterms:publisher
        # 123/a     "Example"      2014-01-04      <http://example.org/publisher/A>
        # 123/b     "Example 2"    2013-09-23      <http://example.org/publisher/B>
        # 123/c     "Of needles"   2014-05-06      <http://example.org/publisher/B>
        for i in ('a','b','c'):
            self.ttl_to_rdf_xml("test/files/base/distilled/123/%s.ttl" % i,
                                self.repos[0].store.distilled_path("123/%s" % i),
                                self.repos[0].store)
            util.ensure_dir(self.repos[0].store.parsed_path("123/%s" % i))
            shutil.copy2("test/files/base/parsed/123/%s.xhtml" % i,
                                self.repos[0].store.parsed_path("123/%s" % i))
            self.repos[0].relate("123/%s" % i)
            # prepare a base.ttl (or var-common.js) that maps
            # <http://example.org/publisher/B> to "Publishing house B"

        self.repos[0].rdf_type = self.repos[0].ns['bibo'].Standard

    def test_stats(self):
        self.env['PATH_INFO'] = "/-/publ;stats"
        self.env['HTTP_ACCEPT'] = 'application/json'
        got = json.loads(self.call_wsgi(self.env)[2].decode("utf-8"))
        want = json.load(open("test/files/api/publ-stats.json"))
        self.assertEqual(want, got)

    def test_fulltext_query(self):
        self.env['PATH_INFO'] = "/-/publ?q=r%C3%A4tt*"
        self.env['HTTP_ACCEPT'] = 'application/json'
        got = json.loads(self.call_wsgi(self.env)[2].decode("utf-8"))
        want = {}
        self.assertEqual(want, got)

    def test_faceted_query(self):
        self.env['PATH_INFO'] = "/-/publ?publisher.iri=*%2Fregeringskansliet"
        self.env['HTTP_ACCEPT'] = 'application/json'
        got = json.loads(self.call_wsgi(self.env)[2].decode("utf-8"))
        want = {}
        self.assertEqual(want, got)

    def test_complex_query(self):
        self.env['PATH_INFO'] = "/-/publ?q=r%C3%A4tt*&publisher.iri=*%2Fregeringskansliet"
        self.env['HTTP_ACCEPT'] = 'application/json'
        got = json.loads(self.call_wsgi(self.env)[2].decode("utf-8"))
        want = {}
        self.assertEqual(want, got)

# Mixin-style classes that are mixed with BasicAPI 
class WhooshBase():
    indextype = 'WHOOSH'
    indexlocation = 'data/whooshindex' 

class ESBase():
    indextype = 'ELASTICSEARCH'
    indexlocation = 'http://localhost:9200/ferenda/'

class SQLiteBase():
    storetype = 'SQLITE'
    storelocation = 'data/ferenda.sqlite' # append self.datadir
    storerepository = 'ferenda'

class FusekiBase():
    storetype = 'FUSEKI'
    storelocation = 'http://localhost:3030/'
    storerepository = 'ds'

# Then the actual testcases are created by combining base classes
class WhooshSQLiteBasicAPI(BasicAPI, WhooshBase, SQLiteBase, WSGI): pass
class WhooshFusekiBasicAPI(BasicAPI, WhooshBase, FusekiBase, WSGI): pass
class ESSQLiteBasicAPI(BasicAPI, ESBase, SQLiteBase, WSGI): pass
class ESFusekiBasicAPI(BasicAPI, ESBase, FusekiBase, WSGI): pass


#================================================================
# AdvancedAPI test case
#        
# This advaced API test framework uses three docrepos. Each docrepo is
# slightly different in what kind of documents it contains and which
# metadata is stored about each one. (This setup is similar to
# integrationFulltextIndex and the DocRepo1/DocRepo2 , but, you know,
# different...)

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
                Facet(DCTERMS.rightsHolder, indexingtype=fulltextindex.Resources(), multiple_values=True),
                Facet(DCTERMS.title, toplevel_only=True),
                Facet(DCTERMS.identifer, selector=self.my_id_selector, key=self.lexicalkey, label="IDs having %(selected) characters"),
                Facet(DC.creator, toplevel_only=False)]


class AdvancedAPI(object):

    storetype = 'FUSEKI'
    storelocation = 'http://localhost:3030/'
    storerepository = 'ds'
    indextype = 'ELASTICSEARCH'
    indexlocation = 'http://localhost:9200/ferenda/'
    # repos = (DocRepo1(), DocRepo2(), DocRepo3())

    def setUp(self):
        try:
            # the call to put_files_in_place can fail and leave the
            # ElasticSearch mapping undeleted -- make sure tearDown
            # runs in this case
            return super(AdvancedAPI, self).setUp()
        except:
            self.tearDown()


    def tearDown(self):
        FulltextIndex.connect(self.indextype, self.indexlocation).destroy()

    def put_files_in_place(self):
        self.repos = []
        for repoclass in DocRepo1, DocRepo2, DocRepo3:
            repo = repoclass(datadir=self.datadir,
                             storetype = self.storetype,
                             storelocation = self.storelocation,
                             storerepository = self.storerepository,
                             indextype = self.indextype,
                             indexlocation = self.indexlocation
            )
            self.repos.append(repo)

        for repo in self.repos:
            for basefile in "a", "b", "c", "d":
                util.ensure_dir(repo.store.parsed_path(basefile))
                # Put files in place: parsed
                parsed_path = "test/files/testrepos/%s/parsed/%s.xhtml" % (repo.alias, basefile)
                shutil.copy2(parsed_path, repo.store.parsed_path(basefile))

                # FIXME: This distilling code is copied from
                # decorators.render -- should perhaps move to a
                # DocumentRepository method like render_xhtml
                distilled_graph = Graph()
                with codecs.open(repo.store.parsed_path(basefile),
                                 encoding="utf-8") as fp:  # unicode
                    distilled_graph.parse(data=fp.read(), format="rdfa",
                                          publicID=repo.canonical_uri(basefile))
                distilled_graph.bind("dc", URIRef("http://purl.org/dc/elements/1.1/"))
                distilled_graph.bind("dcterms", URIRef("http://example.org/this-prefix-should-not-be-used"))
                util.ensure_dir(repo.store.distilled_path(basefile))
                with open(repo.store.distilled_path(basefile),
                          "wb") as distilled_file:
                    distilled_graph.serialize(distilled_file, format="pretty-xml")
                    # print("#======= %s/%s ========" % (repo.alias, basefile))
                    # print(distilled_graph.serialize(format="turtle").decode())
                # finally index all the data into the triplestore/fulltextindex
                repo.relate(basefile, self.repos)


    def test_indexing(self):
        # make sure that a given basefile exists in it and exhibits
        # all expected fields. Also make sure that subparts of indexes
        # are properly indexed when they should be (and not when they
        # shouldn't).
        self.env['PATH_INFO'] = '/myapi/'
        self.env['QUERY_STRING'] = 'uri=*/repo1/a'
        status, headers, content = self.call_wsgi(self.env)
        self.assertResponse("200 OK",
                            {'Content-Type': 'application/json'},
                            json.dumps({'hello': 'world'}),
                            status, headers, content)
        pass

    def test_faceting(self):
        # make sure wsgi_stats deliver documents in the buckets we
        # expect, and that all buckets are there.
        self.fail("not implemented")

    def test_query(self):
        # make sure we can do queries on default and custom facets and
        # so on. Also make sure _stats=on works.
        self.fail("not implemented")

    def test_toc(self):
        # make sure that toc generates all pagesets and that each page
        # contains the correct docs in the correct order (in addiction
        # to what testDocRepo.TOC tests).
        self.fail("not implemented")
        

# Then the actual testcases are created by combining base classes
class WhooshSQLiteAdvancedAPI(AdvancedAPI, WhooshBase, SQLiteBase, WSGI): pass
class WhooshFusekiAdvancedAPI(AdvancedAPI, WhooshBase, FusekiBase, WSGI): pass
class ESSQLiteAdvancedAPI(AdvancedAPI, ESBase, SQLiteBase, WSGI): pass
class ESFusekiAdvancedAPI(AdvancedAPI, ESBase, FusekiBase, WSGI): pass

