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
    def setUp(self):
        super(BasicAPI, self).setUp()
        self.env['PATH_INFO'] = '/-/publ' # or /myapi/
        
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
        # self.env['PATH_INFO'] = "/-/publ?q=r%C3%A4tt*"
        self.env['QUERY_STRING'] = "q=tail"
        self.env['HTTP_ACCEPT'] = 'application/json'
        res = self.call_wsgi(self.env)[2].decode("utf-8")
        got = json.loads(res)
        want = {
            "current": "/-/publ?q=tail",
            "duration": None,
            "items": [
                {
                    "dcterms_identifier": "123(A)",
                    "dcterms_issued": "2014-01-04",
                    "dcterms_publisher": {
                        "iri": "http://example.org/publisher/A",
                        "label": "http://example.org/publisher/A"
                    },
                    "dcterms_title": "Example",
                    "matches": {
                        "text": "<em class=\"match\">tail</em> end of the main document"
                    },
                    "rdf_type": "http://purl.org/ontology/bibo/Standard",
                    "uri": "http://example.org/base/123/a"
                }
            ],
            "itemsPerPage": 10,
            "startIndex": 0,
            "totalResults": 1
        }
        # FIXME: Whoosh and ElasticSearch has slightly different ideas
        # on how to highlight matching snippets.
        if isinstance(self, WhooshBase):
            want['items'][0]['matches']['text'] = "This is the <em class=\"match\">tail</em> end of the main document"
            want['items'][0]['dcterms_issued'] += "T00:00:00"
        self.assertEqual(want, got)

    def test_faceted_query(self):
        self.env['QUERY_STRING'] = "dcterms_publisher=*%2Fpublisher%2FA"
        self.env['HTTP_ACCEPT'] = 'application/json'
        got = json.loads(self.call_wsgi(self.env)[2].decode("utf-8"))
        want = {'current': '/-/publ?dcterms_publisher=*%2Fpublisher%2FA',
                'duration': None,
                'items': [{'dcterms_identifier': '123(A)',
                           'dcterms_issued': '2014-01-04',
                           'dcterms_publisher': {'iri': 'http://example.org/publisher/A',
                                                 'label': 'http://example.org/publisher/A'},
                           'dcterms_title': 'Example',
                           'matches': {'text': 'This is part of the main document, but '
                                       'not of any sub-resource. This is the '
                                       'tail end of the main document'},
                           'rdf_type': 'http://purl.org/ontology/bibo/Standard',
                           'uri': 'http://example.org/base/123/a'}],
                'itemsPerPage': 10,
                'startIndex': 0,
                'totalResults': 1}
        # FIXME: Whoosh (and our own fulltextindex.IndexedType
        # objects) cannot handle a pure date field (always converted
        # to DateTime). Adjust expectations.
        if isinstance(self, WhooshBase):
            want['items'][0]['dcterms_issued'] += "T00:00:00"
        self.assertEqual(want, got)

        # using publisher.iri instead of dcterms_publisher is a test
        # of legacyapi
        self.env['QUERY_STRING'] = "publisher.iri=*%2Fpublisher%2FA"
        got = json.loads(self.call_wsgi(self.env)[2].decode("utf-8"))
        want['current'] = "/-/publ?publisher.iri=*%2Fpublisher%2FA" # FIXME: this illustrates the need to construct 'current' dynamically.
        self.assertEqual(want, got)
        
        
    def test_complex_query(self):
        self.env['QUERY_STRING'] = "q=haystack&dcterms_publisher=*%2Fpublisher%2FB"
        self.env['HTTP_ACCEPT'] = 'application/json'
        got = json.loads(self.call_wsgi(self.env)[2].decode("utf-8"))
        want =  {'current': '/-/publ?q=haystack&dcterms_publisher=*%2Fpublisher%2FB',
                 'duration': None,
                 'items': [{'dcterms_identifier': '123(C)',
                            'dcterms_issued': '2014-05-06',
                            'dcterms_publisher': {'iri': 'http://example.org/publisher/B',
                                                  'label': 'http://example.org/publisher/B'},
                            'dcterms_title': 'Of needles and haystacks',
                            'matches': {'text': ''},
                            'rdf_type': 'http://purl.org/ontology/bibo/Standard',
                            'uri': 'http://example.org/base/123/c'}],
                 'itemsPerPage': 10,
                 'startIndex': 0,
                 'totalResults': 1}

        # FIXME: See above
        if isinstance(self, WhooshBase):
            want['items'][0]['dcterms_issued'] += "T00:00:00"
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

class SesameBase():
    storetype = 'SESAME'
    storelocation = 'http://localhost:8080/openrdf-sesame'
    storerepository = 'ferenda'

# Then the actual testcases are created by combining base classes
class WhooshSQLiteBasicAPI(BasicAPI, WhooshBase, SQLiteBase, WSGI): pass
class WhooshFusekiBasicAPI(BasicAPI, WhooshBase, FusekiBase, WSGI): pass
class WhooshSesameBasicAPI(BasicAPI, WhooshBase, SesameBase, WSGI): pass
class ESSQLiteBasicAPI(BasicAPI, ESBase, SQLiteBase, WSGI): pass
class ESFusekiBasicAPI(BasicAPI, ESBase, FusekiBase, WSGI): pass
class ESSesameBasicAPI(BasicAPI, ESBase, SesameBase, WSGI): pass


#================================================================
# AdvancedAPI test case
#        
# This advaced API test framework uses three docrepos. Each docrepo is
# slightly different in what kind of documents it contains and which
# metadata is stored about each one. (This setup is similar to
# integrationFulltextIndex and the DocRepo1/DocRepo2 , but, you know,
# different...)

from examplerepos import DocRepo1, DocRepo2, DocRepo3


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
        # print(repo._get_triplestore().get_serialized(format="turtle").decode("utf-8"))

    def test_indexing(self):
        # make sure that a given basefile exists in it and exhibits
        # all expected fields. Also make sure that subparts of indexes
        # are properly indexed when they should be (and not when they
        # shouldn't).
        self.env['PATH_INFO'] = '/myapi/'
        self.env['QUERY_STRING'] = 'uri=*/repo1/a'
        status, headers, content = self.call_wsgi(self.env)
        got = json.loads(content.decode("utf-8"))
        self.assertResponse("200 OK",
                            {'Content-Type': 'application/json'},
                            None,
                            status, headers, content)
        want = {"current": "/myapi/?uri=*/repo1/a",
                "duration": None,
                "items": [
                    {
                        "dcterms_issued": "2012-04-01",
                        "dcterms_publisher": {
                            "iri": "http://example.org/vocab/publ1",
                            "label": "Publishing & sons"
                        },
                        "dcterms_title": "A simple doc",
                        "matches": {
                            "text": "This is part of the main document, but not of any sub-resource."
                        },
                        "rdf_type": "http://example.org/vocab/MainType",
                        "uri": "http://example.org/repo1/a"
                    }
                ],
                "itemsPerPage": 10,
                "startIndex": 0,
                "totalResults": 1
            }
        self.assertEqual(want, got)

    def test_faceting(self):
        # make sure wsgi_stats deliver documents in the buckets we
        # expect, and that all buckets are there.
        self.env['PATH_INFO'] = '/myapi/;stats'
        # self.env['PATH_INFO'] = "/-/publ;stats"
        self.env['HTTP_ACCEPT'] = 'application/json'
        status, headers, content = self.call_wsgi(self.env)
        got = json.loads(content.decode("utf-8"))
        want = json.load(open("test/files/api/publ-stats-advanced.json"))
        self.assertResponse("200 OK",
                            {'Content-Type': 'application/json'},
                            None,
                            status, headers, content)
        self.assertEqual(want, got)
        

    def test_query(self):
        # make sure we can do queries on default and custom facets and
        # so on. Also make sure _stats=on works.
        self.env['PATH_INFO'] = '/myapi/'
        self.env['HTTP_ACCEPT'] = 'application/json'

        # test literal string and bool parameters
        self.env['QUERY_STRING'] = "dc_subject=red&schema_free=true"
        from pudb import set_trace; set_trace()
        got = json.loads(self.call_wsgi(self.env)[2].decode("utf-8"))
        want = json.load(open("test/files/api/query-advanced-parameters.json"))
        self.assertEqual(want, got)

        # test a custom facet (is_april_fools) and stats for those results
        self.env['QUERY_STRING'] = "aprilfools=true&_stats=on"
        got = json.loads(self.call_wsgi(self.env)[2].decode("utf-8"))
        want = json.load(open("test/files/api/query-advanced-customfacet.json"))
        self.assertEqual(want, got)

        # test date ranges
        self.env['QUERY_STRING'] = "dcterms_issued.min=2012-04-02&dcterms_issued.max=2012-04-03"
        got = json.loads(self.call_wsgi(self.env)[2].decode("utf-8"))
        want = json.load(open("test/files/api/query-advanced-range.json"))
        self.assertEqual(want, got)
        

# Then the actual testcases are created by combining base classes
class WhooshSQLiteAdvancedAPI(AdvancedAPI, WhooshBase, SQLiteBase, WSGI): pass
class WhooshFusekiAdvancedAPI(AdvancedAPI, WhooshBase, FusekiBase, WSGI): pass
class WhooshSesameAdvancedAPI(AdvancedAPI, WhooshBase, SesameBase, WSGI): pass
class ESSQLiteAdvancedAPI(AdvancedAPI, ESBase, SQLiteBase, WSGI): pass
class ESFusekiAdvancedAPI(AdvancedAPI, ESBase, FusekiBase, WSGI): pass
class ESSesameAdvancedAPI(AdvancedAPI, ESBase, SesameBase, WSGI): pass

