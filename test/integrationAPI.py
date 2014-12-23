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
from ferenda import DocumentRepository, FulltextIndex, TripleStore, Facet
from ferenda import util, fulltextindex

class BasicAPI(object):
    def setUp(self):
        super(BasicAPI, self).setUp()
        self.env['PATH_INFO'] = '/myapi/' 
        
    def tearDown(self):
        FulltextIndex.connect(self.indextype, self.indexlocation,
                              [DocumentRepository()]).destroy()
        
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


    stats_want = json.load(open("test/files/api/basicapi-stats.json"))
    def test_stats(self):
        self.env['PATH_INFO'] += ";stats"
        got = json.loads(self.call_wsgi(self.env)[2].decode("utf-8"))
        self.assertEqual(self.stats_want, got)


    fulltext_query_want = "test/files/api/basicapi-fulltext-query.json"
    def test_fulltext_query(self):
        self.env['QUERY_STRING'] = "q=tail"
        res = self.call_wsgi(self.env)[2].decode("utf-8")
        got = json.loads(res)
        want = json.load(open(self.fulltext_query_want))
        # FIXME: Whoosh and ElasticSearch has slightly different ideas
        # on how to highlight matching snippets.
        if isinstance(self, WhooshBase):
            want['items'][0]['matches']['text'] = "This is the <em class=\"match\">tail</em> end of the main document"
            fld = 'issued' if self.app.config.legacyapi else 'dcterms_issued'
            want['items'][0][fld] += "T00:00:00"
        self.assertEqual(want, got)


    faceted_query = "dcterms_publisher=*%2Fpublisher%2FA"
    faceted_query_want = "test/files/api/basicapi-faceted-query.json"
    def test_faceted_query(self):
        self.env['QUERY_STRING'] = self.faceted_query
        got = json.loads(self.call_wsgi(self.env)[2].decode("utf-8"))
        want = json.load(open(self.faceted_query_want))
        # FIXME: Whoosh (and our own fulltextindex.IndexedType
        # objects) cannot handle a pure date field (always converted
        # to DateTime). Adjust expectations.
        if isinstance(self, WhooshBase):
            fld = 'issued' if self.app.config.legacyapi else 'dcterms_issued'
            want['items'][0][fld] += "T00:00:00"
        self.assertEqual(want, got)


    complex_query = "q=haystack&dcterms_publisher=*%2Fpublisher%2FB"
    complex_query_want = "test/files/api/basicapi-complex-query.json"
    def test_complex_query(self):
        self.env['QUERY_STRING'] = self.complex_query
        res = self.call_wsgi(self.env)[2].decode("utf-8")
        got = json.loads(res)
        want = json.load(open(self.complex_query_want))
        # FIXME: See above
        if isinstance(self, WhooshBase):
            fld = 'issued' if self.app.config.legacyapi else 'dcterms_issued'
            want['items'][0][fld] += "T00:00:00"
        self.assertEqual(want, got)

class BasicLegacyAPI(BasicAPI):
    def setUp(self):
        super(BasicLegacyAPI, self).setUp()
        self.app.config.legacyapi = True
        self.env['PATH_INFO'] = '/-/publ'

    stats_want = json.load(open("test/files/api/basicapi-stats.legacy.json"))

    # no fulltext_query is needed, the querystring is identical
    fulltext_query_want = "test/files/api/basicapi-fulltext-query.legacy.json"

    faceted_query = "dcterms_publisher=*%2Fpublisher%2FA"
    faceted_query_want = "test/files/api/basicapi-faceted-query.legacy.json"

    complex_query = "q=haystack&publisher=*%2Fpublisher%2FB"
    complex_query_want = "test/files/api/basicapi-complex-query.legacy.json"
    
    
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
    storerepository = 'ferenda'

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

# and again with the legacy API handling (it's highly doubtful that a
# difference in fulltextindex engine or triple store would trigger an
# error in the legacy API code path but not in the standard API path,
# but this is an exhaustive test...)
class WhooshSQLiteBasicLegacyAPI(BasicLegacyAPI, WhooshBase, SQLiteBase, WSGI): pass
class WhooshFusekiBasicLegacyAPI(BasicLegacyAPI, WhooshBase, FusekiBase, WSGI): pass
class WhooshSesameBasicLegacyAPI(BasicLegacyAPI, WhooshBase, SesameBase, WSGI): pass
class ESSQLiteBasicLegacyAPI(BasicLegacyAPI, ESBase, SQLiteBase, WSGI): pass
class ESFusekiBasicLegacyAPI(BasicLegacyAPI, ESBase, FusekiBase, WSGI): pass
class ESSesameBasicLegacyAPI(BasicLegacyAPI, ESBase, SesameBase, WSGI): pass


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
    storerepository = 'ferenda'
    indextype = 'ELASTICSEARCH'
    indexlocation = 'http://localhost:9200/ferenda/'
    # repos = (DocRepo1(), DocRepo2(), DocRepo3())

    def setUp(self):
        try:
            # the call to put_files_in_place can easily fail and leave
            # the ElasticSearch mapping undeleted -- make sure
            # tearDown runs in this case
            super(AdvancedAPI, self).setUp()
            self.env['PATH_INFO'] = '/myapi/' 
        except Exception as e:
            self.tearDown()
            raise e


    def tearDown(self):
        FulltextIndex.connect(self.indextype, self.indexlocation,
                              [DocumentRepository()]).destroy()
        TripleStore.connect(self.storetype, self.storelocation, self.storerepository).clear()

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

    indexing_want = json.load(open("test/files/api/advancedapi-indexing.json"))
    def test_indexing(self):
        # make sure that a given basefile exists in it and exhibits
        # all expected fields. Also make sure that subparts of indexes
        # are properly indexed when they should be (and not when they
        # shouldn't).
        self.env['QUERY_STRING'] = 'uri=*/repo1/a'
        status, headers, content = self.call_wsgi(self.env)
        got = json.loads(content.decode("utf-8"))
        self.assertEqual(self.indexing_want, got)

    faceting_want = json.load(open("test/files/api/advancedapi-faceting.json"))
    def test_faceting(self):
        # make sure wsgi_stats deliver documents in the buckets we
        # expect, and that all buckets are there.
        self.env['PATH_INFO'] += ';stats'
        status, headers, content = self.call_wsgi(self.env)
        got = json.loads(content.decode("utf-8"))
        self.assertEqual(self.faceting_want, got)
        
    # test literal string and bool parameters
    query_parameters = "dc_subject=red&schema_free=true"
    query_parameters_want = json.load(open("test/files/api/advancedapi-query-parameters.json"))
    def test_query_parameters(self):
        self.env['QUERY_STRING'] = self.query_parameters
        got = json.loads(self.call_wsgi(self.env)[2].decode("utf-8"))
        self.assertEqual(self.query_parameters_want, got)

    # test querying by rdftype -- handled in a very special way
    query_type = "rdf_type=ex:OtherType"
    query_type_want = json.load(open("test/files/api/advancedapi-query-type.json"))
    def test_query_type(self):
        self.env['QUERY_STRING'] = self.query_type
        got = json.loads(self.call_wsgi(self.env)[2].decode("utf-8"))
        self.assertEqual(self.query_type_want, got)
        
    # test a custom facet (is_april_fools) and stats for those results
    query_customfacet = "aprilfools=true&_stats=on"
    query_customfacet_want = json.load(open("test/files/api/advancedapi-query-customfacet.json"))
    def test_query_customfacet(self):
        self.env['QUERY_STRING'] = self.query_customfacet
        got = json.loads(self.call_wsgi(self.env)[2].decode("utf-8"))
        self.assertEqual(self.query_customfacet_want, got)

    # test date ranges (note: these are exclusive ranges, ie documents
    # dated exactly 2012-04-01 or 2012-04-03 are not included in the
    # result set. Maybe this is less than intuitive?
    query_range = "min-dcterms_issued=2012-04-01&max-dcterms_issued=2012-04-03"
    query_range_want = json.load(open("test/files/api/advancedapi-query-range.json"))
    def test_query_range(self):
        self.env['QUERY_STRING'] = self.query_range
        got = json.loads(self.call_wsgi(self.env)[2].decode("utf-8"))
        self.assertEqual(self.query_range_want, got)

    query_yearselector = "year-dcterms_issued=2013"
    query_yearselector_want = json.load(open("test/files/api/advancedapi-query-yearselector.json"))
    def test_query_yearselector(self):
        self.env['QUERY_STRING'] = self.query_yearselector
        got = json.loads(self.call_wsgi(self.env)[2].decode("utf-8"))
        self.assertEqual(self.query_yearselector_want, got)


class AdvancedLegacyAPI(AdvancedAPI):
    def setUp(self):
        super(AdvancedLegacyAPI, self).setUp()
        self.app.config.legacyapi = True
        self.env['PATH_INFO'] = '/-/publ'

    indexing_want = json.load(open("test/files/api/advancedapi-indexing.legacy.json"))
    faceting_want = json.load(open("test/files/api/advancedapi-faceting.legacy.json"))
    query_parameters = "subject=red&free=true"
    query_parameters_want = json.load(open("test/files/api/advancedapi-query-parameters.legacy.json"))
    query_type = "type=OtherType"
    query_type_want = json.load(open("test/files/api/advancedapi-query-type.legacy.json"))
    query_customfacet_want = json.load(open("test/files/api/advancedapi-query-customfacet.legacy.json"))
    query_range = "min-issued=2012-04-01&max-issued=2012-04-03"
    query_range_want = json.load(open("test/files/api/advancedapi-query-range.legacy.json"))
    query_yearselector = "year-issued=2013"
    query_yearselector_want = json.load(open("test/files/api/advancedapi-query-yearselector.legacy.json"))

        
# Then the actual testcases are created by combining base classes
class WhooshSQLiteAdvancedAPI(AdvancedAPI, WhooshBase, SQLiteBase, WSGI): pass
class WhooshFusekiAdvancedAPI(AdvancedAPI, WhooshBase, FusekiBase, WSGI): pass
class WhooshSesameAdvancedAPI(AdvancedAPI, WhooshBase, SesameBase, WSGI): pass
class ESSQLiteAdvancedAPI(AdvancedAPI, ESBase, SQLiteBase, WSGI): pass
class ESFusekiAdvancedAPI(AdvancedAPI, ESBase, FusekiBase, WSGI): pass
class ESSesameAdvancedAPI(AdvancedAPI, ESBase, SesameBase, WSGI): pass
class WhooshSQLiteAdvancedLegacyAPI(AdvancedLegacyAPI, WhooshBase, SQLiteBase, WSGI): pass
class WhooshFusekiAdvancedLegacyAPI(AdvancedLegacyAPI, WhooshBase, FusekiBase, WSGI): pass
class WhooshSesameAdvancedLegacyAPI(AdvancedLegacyAPI, WhooshBase, SesameBase, WSGI): pass
class ESSQLiteAdvancedLegacyAPI(AdvancedLegacyAPI, ESBase, SQLiteBase, WSGI): pass
class ESFusekiAdvancedLegacyAPI(AdvancedLegacyAPI, ESBase, FusekiBase, WSGI): pass
class ESSesameAdvancedLegacyAPI(AdvancedLegacyAPI, ESBase, SesameBase, WSGI): pass

