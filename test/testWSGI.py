# -*- coding: utf-8 -*-
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
from builtins import *

from io import BytesIO
import codecs
import datetime
import json
import os
import shutil

from lxml import etree
from rdflib import Graph

from ferenda.compat import Mock, patch
from ferenda import manager, util, fulltextindex
from ferenda.elements import html
from ferenda.testutil import RepoTester

# tests the wsgi app in-process, ie not with actual HTTP requests, but
# simulates what make_server().serve_forever() would send and
# recieve. Should be simple enough, yet reasonably realistic, for
# testing the API.

DEFAULT_HTTP_ACCEPT = 'text/xml, application/xml, application/xhtml+xml, text/html;q=0.9, text/plain;q=0.8, image/png,*/*;q=0.5'

from testCompositeRepo import CompositeExample

class Pathresolve(RepoTester):
    
    repoclass = CompositeExample

    def setUp(self):
        super(Pathresolve, self).setUp()
        self.p = self.repo.requesthandler.path
        
    
    def test_basic(self):
        p = self.repo.requesthandler.path
        self.assertEqual(self.p("http://localhost:8000/res/base/123/a"),
                         self.datadir + "/base/generated/123/a/index.html")

    def test_repo_param(self):
        self.assertEqual(self.p("http://localhost:8000/res/base/123/a?repo=a"),
                         self.datadir + "/a/generated/123/a.html")

    def test_dir_param(self):
        self.assertEqual(self.p("http://localhost:8000/res/base/123/a?dir=downloaded"),
                         self.datadir + "/base/downloaded/123/a/index.html")

    def test_attachment_param(self):
        self.assertEqual(self.p("http://localhost:8000/res/base/123/a?attachment=foo.pdf"),
                         self.datadir + "/base/generated/123/a/foo.pdf")


    def test_all_params(self):
        self.assertEqual(self.p("http://localhost:8000/res/base/123/a?repo=b&"
                           "dir=downloaded&attachment=foo.pdf"),
                         self.datadir + "/b/downloaded/123/a/foo.pdf")

    def test_no_pathfunc(self):
        # this path does not resolve to a static file but rather a transformation function
        self.assertIsNone(self.p("http://localhost:8000/res/base/123/a.json"))

    def test_dataset(self):
        self.assertEqual(self.p("http://localhost:8000/dataset/base"),
                         self.datadir + "/base/toc/index.html")

    def test_dataset_params(self):
        self.assertEqual(self.p("http://localhost:8000/dataset/base?title=a"),
                         self.datadir + "/base/toc/title/a.html")

    def test_dataset_feed(self):
        self.assertEqual(self.p("http://localhost:8000/dataset/base/feed"),
                         self.datadir + "/base/feed/main.html")

    def test_dataset_feed_params(self):
        self.assertEqual(self.p("http://localhost:8000/dataset/base/feed?type=foo"),
                         self.datadir + "/base/feed/foo.html")

    def test_dataset_feed_atom(self):
        self.assertEqual(self.p("http://localhost:8000/dataset/base/feed.atom"),
                         self.datadir + "/base/feed/main.atom")

    def test_dataset_feed_atom_params(self):
        self.assertEqual(self.p("http://localhost:8000/dataset/base/feed.atom?type=foo"),
                         self.datadir + "/base/feed/foo.atom")
        


class WSGI(RepoTester): # base class w/o tests
    storetype = 'SQLITE'
    storelocation = 'data/ferenda.sqlite' # append self.datadir
    storerepository = 'ferenda'
    indextype = 'WHOOSH'
    indexlocation = 'data/whooshindex' # append self.datadir
    def setUp(self):
        super(WSGI,self).setUp()
        if self.storelocation.startswith("data/"):
            self.storelocation = self.storelocation.replace("data", self.datadir)
        if self.indexlocation.startswith("data/"):
            self.indexlocation = self.indexlocation.replace("data", self.datadir)
        self.put_files_in_place()
        # use self.repo (simple testcases) or self.repos (complex
        # testcases like AdvancedAPI)?
        if hasattr(self, 'repos'):
            repos = self.repos
        else:
            repos = [self.repo]
            
        # print("making app: %s %s" % (self.storetype, self.indextype))
        self.app = manager.make_wsgi_app(port=8000,
                                         documentroot=self.datadir,
                                         apiendpoint="/myapi/",
                                         searchendpoint="/mysearch/",
                                         url="http://localhost:8000/",
                                         repos=repos,
                                         storetype=self.storetype,
                                         storelocation=self.storelocation,
                                         storerepository=self.storerepository,
                                         indextype=self.indextype,
                                         indexlocation=self.indexlocation)
        self.env = {'HTTP_ACCEPT': DEFAULT_HTTP_ACCEPT,
                    'PATH_INFO':   '/',
                    'SERVER_NAME': 'localhost',
                    'SERVER_PORT': '8000',
                    'QUERY_STRING': '',
                    'wsgi.url_scheme': 'http'}

    def ttl_to_rdf_xml(self, inpath, outpath, store=None):
        if not store:
            store = self.repo.store
        g = Graph()
        g.parse(data=util.readfile(inpath, encoding="utf-8"), format="turtle")
        with store._open(outpath, "wb") as fp:
            fp.write(g.serialize(format="pretty-xml"))
        return g

    def put_files_in_place(self):
        # Put files in place: parsed
        util.ensure_dir(self.repo.store.parsed_path("123/a"))
        shutil.copy2("test/files/base/parsed/123/a.xhtml",
                     self.repo.store.parsed_path("123/a"))
        g = self.ttl_to_rdf_xml("test/files/base/distilled/123/a.ttl",
                                self.repo.store.distilled_path("123/a"))

        # generated
        util.ensure_dir(self.repo.store.generated_path("123/a"))
        shutil.copy2("test/files/base/generated/123/a.html",
                     self.repo.store.generated_path("123/a"))
        shutil.copy2("test/files/base/generated/123/a.html",
                     self.repo.store.generated_path("123/a"))

        # annotations
        util.ensure_dir(self.repo.store.annotation_path("123/a"))
        shutil.copy2("test/files/base/annotations/123/a.grit.xml",
                     self.repo.store.annotation_path("123/a"))

        # config
        resources = self.datadir+os.sep+"rsrc"+os.sep+"resources.xml"
        util.ensure_dir(resources)
        shutil.copy2("test/files/base/rsrc/resources.xml",
                     resources)

        # index.html
        index = self.datadir+os.sep+"index.html"
        with open(index, "wb") as fp:
            fp.write(b'<h1>index.html</h1>')

        # toc/index.html + toc/title/a.html
        with self.repo.store.open("index", "toc", ".html", "wb") as fp:
            fp.write(b'<h1>TOC for base</h1>')
        with self.repo.store.open("title/a", "toc", ".html", "wb") as fp:
            fp.write(b'<h1>Title starting with "a"</h1>')

        # distilled/dump.nt
        with self.repo.store.open("dump", "distilled", ".nt", "wb") as fp:
            fp.write(g.serialize(format="nt"))

    def call_wsgi(self, environ):
        start_response = Mock()
        buf = BytesIO()
        iterable = self.app(environ, start_response)
        for chunk in iterable:
            buf.write(chunk)
        if hasattr(iterable, 'close'):
            iterable.close()
        call_args = start_response.mock_calls[0][1]
        # call_kwargs = start_response.mock_calls[0][2]
        return call_args[0], call_args[1], buf.getvalue()

    def assertResponse(self,
                       wanted_status,
                       wanted_headers,
                       wanted_content,
                       got_status,
                       got_headers,
                       got_content):
        self.assertEqual(wanted_status, got_status)
        got_headers = dict(got_headers)
        for (key, value) in wanted_headers.items():
            self.assertEqual(value, got_headers[key])
        if wanted_content:
            self.assertEqual(wanted_content, got_content)


class Fileserving(WSGI):
    def test_index_html(self):
        self.env['PATH_INFO'] = '/'
        status, headers, content = self.call_wsgi(self.env)
        self.assertResponse("200 OK",
                            {'Content-Type': 'text/html; charset=utf-8'},
                            b'<h1>index.html</h1>',
                            status, headers, content)

    def test_not_found(self):
        self.env['PATH_INFO'] = '/nonexistent'
        status, headers, content = self.call_wsgi(self.env)
        msg = '<h1>404</h1>\n\nThe path /nonexistent not found at %s/nonexistent.\n\nExamined 1 repos.\n\n<pre>base: (unknown reason)\n</pre>\n' % self.datadir
        self.assertResponse("404 Not Found",
                            {'Content-Type': 'text/html; charset=utf-8'},
                            msg.encode(),
                            status, headers, content)


# most parts of the API are tested with integrationAPI
class API(WSGI):
    def setUp(self):
       super(API, self).setUp()
       self.env['PATH_INFO'] = '/myapi/'

    def test_basic(self):
        status, headers, content = self.call_wsgi(self.env)
        self.assertResponse("200 OK",
                            {'Content-Type': 'application/json'},
                            None,
                            status, headers, content)
        got = json.loads(content.decode())
        want = {'current': '/myapi/?',
                  'duration': None,
                  'items': [],
                  'itemsPerPage': 10,
                  'startIndex': -10, # Hmm, probably not correct
                  'totalResults': 0}
        self.assertEqual(want, got)

    def test_parameters(self):
        # normal api
        res = ([], {'firstresult': 1,
                    'totalresults': 0})
        self.env['QUERY_STRING'] = "rdf_type=bibo:Standard&dcterms_title=Hello+World&dcterms_issued=2014-06-30&schema_free=true"
        config = {'connect.return_value':
                  Mock(**{'query.return_value': res,
                          'schema.return_value': {'dcterms_issued': fulltextindex.Datetime(),
                                                  'schema_free': fulltextindex.Boolean()}})}
        want = {'q': None,
                'dcterms_title': "Hello World",
                'dcterms_issued': datetime.datetime(2014,6,30,0,0,0),
                'schema_free': True,
                'rdf_type': 'bibo:Standard', # FIXME: should be http://purl.org/ontology/bibo/Standard -- but requires that self.repos in wsgiapp is set up
                'pagenum': 1,
                'pagelen': 10,
                'ac_query': False,
                'exclude_types': None}
        with patch('ferenda.wsgiapp.FulltextIndex', **config):
            status, headers, content = self.call_wsgi(self.env)
            config['connect.return_value'].query.assert_called_once_with(**want)

    def test_parameters_legacy(self):
        # legacy api
        res = ([], {'firstresult': 1,
                    'totalresults': 0})
        # FIXME: we leave out free=true (should map to schema_free=True)
        self.env['QUERY_STRING'] = "type=Standard&title=Hello+World&issued=2014-06-30&schema_free=true"
        self.app.config.legacyapi = True
        config = {'connect.return_value': 
                  Mock(**{'query.return_value': res,
                          'schema.return_value': {'dcterms_issued': fulltextindex.Datetime(),
                                                  'schema_free': fulltextindex.Boolean(),
                                                  'dcterms_title': None,
                                                  'rdf_type': None}})}

        want = {'q': None,
                'dcterms_title': "Hello World",
                'dcterms_issued': datetime.datetime(2014,6,30,0,0,0),
                'schema_free': True,
                'rdf_type': '*Standard', # should be bibo:Standard or even http://purl.org/ontology/bibo/Standard, but requires proper context handling to work
                'pagenum': 1,
                'pagelen': 10,
                'ac_query': False,
                'exclude_types': None}

        with patch('ferenda.wsgiapp.FulltextIndex', **config):
            status, headers, content = self.call_wsgi(self.env)
            config['connect.return_value'].query.assert_called_once_with(**want)
    # this is the same data that can be extracted from
    # test/files/base/distilled/
    fakedata = [{'dcterms_identifier': '123(A)',
                 'dcterms_issued': '2014-01-04',
                 'dcterms_publisher': 'http://example.org/publisher/A',
                 'dcterms_title': 'Example',
                 'rdf_type': 'http://purl.org/ontology/bibo/Standard',
                 'uri': 'http://example.org/base/123/a'},
                {'dcterms_identifier': '123(B)',
                 'dcterms_issued': '2013-09-23',
                 'dcterms_publisher': 'http://example.org/publisher/B',
                 'dcterms_title': 'Example 2',
                 'rdf_type': 'http://purl.org/ontology/bibo/Standard',
                 'uri': 'http://example.org/base/123/b'},
                {'dcterms_identifier': '123(C)',
                 'dcterms_issued': '2014-05-06',
                 'dcterms_publisher': 'http://example.org/publisher/B',
                 'dcterms_title': 'Of needles and haystacks',
                 'rdf_type': 'http://purl.org/ontology/bibo/Standard',
                 'uri': 'http://example.org/base/123/c'}]

    def test_stats(self):
        self.env['PATH_INFO'] += ";stats"
        self.app.repos[0].faceted_data = Mock(return_value=self.fakedata)
        status, headers, content = self.call_wsgi(self.env)
        got = json.loads(content.decode("utf-8"))
        want = json.load(open("test/files/api/basicapi-stats.json"))
        self.assertEqual(want, got)

    def test_stats_legacy(self):
        self.env['PATH_INFO'] += ";stats"
        self.app.config.legacyapi = True
        # self.app.repos[0].faceted_data = Mock(return_value=self.fakedata)
        # status, headers, content = self.call_wsgi(self.env)
        # got = json.loads(content)
        # want = json.load(open("test/files/api/basicapi-stats.legacy.json"))
        # self.assertEqual(want, got)

        
        
    
                  
        
class Runserver(WSGI):
    def test_make_wsgi_app_args(self):
        res = manager.make_wsgi_app(port='8080',
                                    documentroot=self.datadir,
                                    apiendpoint='/api-endpoint/',
                                    searchendpoint='/search-endpoint/',
                                    repos=[])
        self.assertTrue(callable(res))

    def test_make_wsgi_app_ini(self):
        inifile = self.datadir + os.sep + "ferenda.ini"
        with open(inifile, "w") as fp:
            fp.write("""[__root__]
datadir = /dev/null
url = http://localhost:7777/
apiendpoint = /myapi/
searchendpoint = /mysearch/            
indextype = WHOOSH
indexlocation = data/whooshindex        
""")
        res = manager.make_wsgi_app(inifile)
        self.assertTrue(callable(res))
    
    def test_runserver(self):
        m = Mock()
        with patch('ferenda.manager.make_server', return_value=m) as m2:
            manager.runserver([])
            self.assertTrue(m2.called)
            self.assertTrue(m.serve_forever.called)

class Parameters(WSGI):

    def test_attachment_param(self):
        self.repo.store.storage_policy = "dir"
        util.ensure_dir(self.repo.store.generated_path("123/a"))
        cssdata = b"/* css data goes here */"
        csspath = self.repo.store.generated_path("123/a", attachment="index.css")
        with open(csspath, "wb") as fp:
            fp.write(cssdata)
        self.env["PATH_INFO"] = "/res/base/123/a?attachment=index.css"
        status, headers, content = self.call_wsgi(self.env)
        want = ["200 OK",
                {'Content-Type': 'text/css'},
                cssdata]
        self.assertResponse(want[0], want[1], want[2],
                            status, headers, content)
        

    def test_dataset_param(self):
        util.ensure_dir(self.repo.store.generated_path("123/a"))
        tocdata = b"<!-- specific toc page goes here -->"
        tocpath = self.repo.store.resourcepath("toc/title/a.html")
        with open(tocpath, "wb") as fp:
            fp.write(tocdata)
        self.env["PATH_INFO"] = "/dataset/base?title=a"
        status, headers, content = self.call_wsgi(self.env)
        want = ["200 OK",
                {'Content-Type': 'text/html; charset=utf-8'},
                tocdata]
        self.assertResponse(want[0], want[1], want[2],
                            status, headers, content)
        

    def test_feed_param(self):
        tocdata = b"<!-- specific feed goes here -->"
        tocpath = self.repo.store.resourcepath("feed/a.atom")
        util.ensure_dir(tocpath)
        with open(tocpath, "wb") as fp:
            fp.write(tocdata)
        self.env["PATH_INFO"] = "/dataset/base/feed.atom?title=a"
        status, headers, content = self.call_wsgi(self.env)
        want = ["200 OK",
                {'Content-Type': 'application/atom+xml'},
                tocdata]
        self.assertResponse(want[0], want[1], want[2],
                            status, headers, content)


class ConNeg(WSGI):
    def setUp(self):
       super(ConNeg, self).setUp()
       self.env['PATH_INFO'] = '/res/base/123/a'

    def test_basic(self):
        # basic test 1: accept: text/html -> generated file
        # Note that our Accept header has a more complicated value 
        # typical of a real-life browse
        status, headers, content = self.call_wsgi(self.env)
        self.assertResponse("200 OK",
                            {'Content-Type': 'text/html; charset=utf-8'},
                            util.readfile(self.repo.store.generated_path("123/a"), "rb"),
                            status, headers, content)

    def test_xhtml(self):
        # basic test 2: accept: application/xhtml+xml -> parsed file
        self.env['HTTP_ACCEPT'] = 'application/xhtml+xml'
        status, headers, content = self.call_wsgi(self.env)
        want = ["200 OK",
                {'Content-Type': 'application/xhtml+xml'},
                util.readfile(self.repo.store.parsed_path("123/a"), "rb")]
        self.assertResponse(want[0], want[1], want[2], status, headers, content)
        # variation: use file extension
        self.env["HTTP_ACCEPT"] = DEFAULT_HTTP_ACCEPT
        self.env["PATH_INFO"] += ".xhtml"
        status, headers, content = self.call_wsgi(self.env)
        self.assertResponse(want[0], want[1], want[2], status, headers, content)

    def test_rdf(self):
        # basic test 3: accept: application/rdf+xml -> RDF statements (in XML)
        self.env['HTTP_ACCEPT'] = 'application/rdf+xml'
        status, headers, content = self.call_wsgi(self.env)
        want = ["200 OK",
                {'Content-Type': 'application/rdf+xml'},
                util.readfile(self.repo.store.distilled_path("123/a"), "rb")]
        self.assertResponse(want[0], want[1], want[2],
                            status, headers, content)

        # variation: use file extension
        self.env["HTTP_ACCEPT"] = DEFAULT_HTTP_ACCEPT
        self.env["PATH_INFO"] += ".rdf"
        status, headers, content = self.call_wsgi(self.env)
        self.assertResponse(want[0], want[1], want[2], status, headers, content)


    def test_ntriples(self):
        # Serialization may upset order of triples -- it's not
        # guaranteed that two isomorphic graphs serialize to the exact
        # same byte stream. Therefore, we only compare headers, not
        # content, and follow up with a proper graph comparison
        
        # transform test 4: accept: text/plain -> RDF statements (in NTriples)
        g = Graph()
        g.parse(source=self.repo.store.distilled_path("123/a"))
        self.env['HTTP_ACCEPT'] = 'text/plain'
        status, headers, content = self.call_wsgi(self.env)
        want = ["200 OK",
                {'Content-Type': 'text/plain'},
                None]
        self.assertResponse(want[0], want[1], want[2],
                            status, headers, None)
        got = Graph()
        got.parse(data=content, format="nt")
        self.assertEqualGraphs(g, got)

        # variation: use file extension
        self.env["HTTP_ACCEPT"] = DEFAULT_HTTP_ACCEPT
        self.env["PATH_INFO"] += ".nt"
        status, headers, content = self.call_wsgi(self.env)
        self.assertResponse(want[0], want[1], want[2], status, headers, content)
        got = Graph()
        got.parse(data=content, format="nt")
        self.assertEqualGraphs(g, got)

    def test_turtle(self):
        # transform test 5: accept: text/turtle -> RDF statements (in Turtle)
        g = Graph()
        g.parse(source=self.repo.store.distilled_path("123/a"))
        self.env['HTTP_ACCEPT'] = 'text/turtle'
        status, headers, content = self.call_wsgi(self.env)
        want = ["200 OK",
                {'Content-Type': 'text/turtle'},
                None]
        self.assertResponse(want[0], want[1], want[2],
                            status, headers, None)
        got = Graph()
        got.parse(data=content, format="turtle")
        self.assertEqualGraphs(g, got)

        # variation: use file extension
        self.env["HTTP_ACCEPT"] = DEFAULT_HTTP_ACCEPT
        self.env["PATH_INFO"] += ".ttl"
        status, headers, content = self.call_wsgi(self.env)
        self.assertResponse(want[0], want[1], want[2], status, headers, content)
        got = Graph()
        got.parse(data=content, format="turtle")
        self.assertEqualGraphs(g, got)

    def test_json(self):
        # transform test 6: accept: application/json -> RDF statements (in JSON-LD)
        g = Graph()
        g.parse(source=self.repo.store.distilled_path("123/a"))
        self.env['HTTP_ACCEPT'] = 'application/json'
        status, headers, content = self.call_wsgi(self.env)
        want = ["200 OK",
                {'Content-Type': 'application/json'},
                None]
        self.assertResponse(want[0], want[1], want[2],
                            status, headers, None)
        got = Graph()
        got.parse(data=content, format="json-ld")
        self.assertEqualGraphs(g, got)
        
        # variation: use file extension
        self.env["HTTP_ACCEPT"] = DEFAULT_HTTP_ACCEPT
        self.env["PATH_INFO"] += ".json"
        status, headers, content = self.call_wsgi(self.env)
        self.assertResponse(want[0], want[1], want[2], status, headers, content)
        got = Graph()
        got.parse(data=content, format="json-ld")
        self.assertEqualGraphs(g, got)

    def test_unacceptable(self):
        self.env['HTTP_ACCEPT'] = 'application/pdf'
        status, headers, content = self.call_wsgi(self.env)
        want = ["406 Not Acceptable",
                {'Content-Type': 'text/html; charset=utf-8'},
                None]
        self.assertResponse(want[0], want[1], want[2],
                            status, headers, None)
    
        # variation: unknown file extension should also be unacceptable
        self.env["HTTP_ACCEPT"] = DEFAULT_HTTP_ACCEPT
        self.env["PATH_INFO"] += ".pdf"
        status, headers, content = self.call_wsgi(self.env)
        self.assertResponse(want[0], want[1], want[2], status, headers, content)


    def test_extended_rdf(self):
        # extended test 6: accept: "/data" -> extended RDF statements
        self.env['PATH_INFO'] = self.env['PATH_INFO'] + "/data"
        self.env['HTTP_ACCEPT'] = 'application/rdf+xml'
        g = Graph()
        g.parse(source=self.repo.store.distilled_path("123/a"))
        g += self.repo.annotation_file_to_graph(self.repo.store.annotation_path("123/a"))
        status, headers, content = self.call_wsgi(self.env)
        want = ["200 OK",
                {'Content-Type': 'application/rdf+xml'},
                None]
        self.assertResponse(want[0], want[1], want[2],
                            status, headers, None)
        got = Graph()
        got.parse(data=content)
        self.assertEqualGraphs(g, got)

        # variation: use file extension
        self.env["HTTP_ACCEPT"] = DEFAULT_HTTP_ACCEPT
        self.env["PATH_INFO"] += ".rdf"
        status, headers, content = self.call_wsgi(self.env)
        self.assertResponse(want[0], want[1], want[2], status, headers, content)
        got = Graph()
        got.parse(data=content)
        self.assertEqualGraphs(g, got)

    def test_extended_ntriples(self):
        # extended test 7: accept: "/data" + "text/plain" -> extended
        # RDF statements in NTriples
        self.env['PATH_INFO'] = self.env['PATH_INFO'] + "/data"       
        self.env['HTTP_ACCEPT'] = 'text/plain'
        g = Graph()
        g.parse(source=self.repo.store.distilled_path("123/a"))
        g += self.repo.annotation_file_to_graph(self.repo.store.annotation_path("123/a"))
        status, headers, content = self.call_wsgi(self.env)
        want = ["200 OK",
                 {'Content-Type': 'text/plain'},
                 None]
        self.assertResponse(want[0], want[1], want[2],
                            status, headers, None)
        got = Graph()
        got.parse(data=content, format="nt")
        self.assertEqualGraphs(g, got)

        # variation: use file extension
        self.env["HTTP_ACCEPT"] = DEFAULT_HTTP_ACCEPT
        self.env["PATH_INFO"] += ".nt"
        status, headers, content = self.call_wsgi(self.env)
        self.assertResponse(want[0], want[1], want[2], status, headers, content)
        got = Graph()
        got.parse(data=content, format="nt")
        self.assertEqualGraphs(g, got)

    def test_extended_turtle(self):
        # extended test 7: accept: "/data" + "text/turtle" -> extended
        # RDF statements in Turtle
        self.env['PATH_INFO'] = self.env['PATH_INFO'] + "/data"       
        self.env['HTTP_ACCEPT'] = 'text/turtle'
        g = Graph()
        g.parse(source=self.repo.store.distilled_path("123/a"))
        g += self.repo.annotation_file_to_graph(self.repo.store.annotation_path("123/a"))
        status, headers, content = self.call_wsgi(self.env)
        want = ["200 OK",
                {'Content-Type': 'text/turtle'},
                None]
        self.assertResponse(want[0], want[1], want[2],
                            status, headers, None)
        got = Graph()
        got.parse(data=content, format="turtle")
        self.assertEqualGraphs(g, got)

        # variation: use file extension
        self.env["HTTP_ACCEPT"] = DEFAULT_HTTP_ACCEPT
        self.env["PATH_INFO"] += ".ttl"
        status, headers, content = self.call_wsgi(self.env)
        self.assertResponse(want[0], want[1], want[2], status, headers, content)
        got = Graph()
        got.parse(data=content, format="turtle")
        self.assertEqualGraphs(g, got)

    def test_dataset_html(self):
        self.env['PATH_INFO'] = "/dataset/base"
        status, headers, content = self.call_wsgi(self.env)
        self.assertResponse("200 OK",
                            {'Content-Type': 'text/html; charset=utf-8'},
                            b'<h1>TOC for base</h1>',
                            status, headers, content)

    def test_dataset_html_param(self):
        self.env['PATH_INFO'] = "/dataset/base"
        self.env['QUERY_STRING'] = "title=a"
        status, headers, content = self.call_wsgi(self.env)
        self.assertResponse("200 OK",
                            {'Content-Type': 'text/html; charset=utf-8'},
                            b'<h1>Title starting with "a"</h1>',
                            status, headers, content)

    def test_dataset_ntriples(self):
        self.env['PATH_INFO'] = "/dataset/base"
        self.env['HTTP_ACCEPT'] = 'text/plain'
        status, headers, content = self.call_wsgi(self.env)
        want = ("200 OK",
                {'Content-Type': 'text/plain'},
                None)
        self.assertResponse(want[0], want[1], want[2],
                            status, headers, None)
        wantgraph = Graph()
        wantgraph.parse(source="test/files/base/distilled/123/a.ttl",
                   format="turtle")
        gotgraph = Graph()
        gotgraph.parse(data=content, format="nt")
        self.assertEqualGraphs(wantgraph, gotgraph)

        # variation: use file extension
        self.env["HTTP_ACCEPT"] = DEFAULT_HTTP_ACCEPT
        self.env["PATH_INFO"] += ".nt"
        status, headers, content = self.call_wsgi(self.env)
        self.assertResponse(want[0], want[1], want[2], status, headers, content)
        gotgraph = Graph()
        gotgraph.parse(data=content, format="nt")
        self.assertEqualGraphs(wantgraph, gotgraph)


    def test_dataset_turtle(self):
        self.env['PATH_INFO'] = "/dataset/base"
        self.env['HTTP_ACCEPT'] = 'text/turtle'
        status, headers, content = self.call_wsgi(self.env)
        want = ["200 OK",
                {'Content-Type': 'text/turtle'},
                None]
        self.assertResponse(want[0], want[1], want[2],
                            status, headers, None)
        wantgraph = Graph()
        wantgraph.parse(source="test/files/base/distilled/123/a.ttl",
                   format="turtle")
        gotgraph = Graph()
        gotgraph.parse(data=content, format="turtle")
        self.assertEqualGraphs(wantgraph, gotgraph)

        # variation: use file extension
        self.env["HTTP_ACCEPT"] = DEFAULT_HTTP_ACCEPT
        self.env["PATH_INFO"] += ".ttl"
        status, headers, content = self.call_wsgi(self.env)
        self.assertResponse(want[0], want[1], want[2], status, headers, content)
        gotgraph = Graph()
        gotgraph.parse(data=content, format="turtle")
        self.assertEqualGraphs(wantgraph, gotgraph)


    def test_dataset_xml(self):
        self.env['PATH_INFO'] = "/dataset/base"
        self.env['HTTP_ACCEPT'] = 'application/rdf+xml'
        status, headers, content = self.call_wsgi(self.env)
        want = ["200 OK",
                {'Content-Type': 'application/rdf+xml'},
                None]
        self.assertResponse(want[0], want[1], want[2],
                            status, headers, None)
        wantgraph = Graph()
        wantgraph.parse(source="test/files/base/distilled/123/a.ttl",
                   format="turtle")
        gotgraph = Graph()
        gotgraph.parse(data=content, format="xml")
        self.assertEqualGraphs(wantgraph, gotgraph)

        # variation: use file extension
        self.env["HTTP_ACCEPT"] = DEFAULT_HTTP_ACCEPT
        self.env["PATH_INFO"] += ".rdf"
        status, headers, content = self.call_wsgi(self.env)
        self.assertResponse(want[0], want[1], want[2], status, headers, content)
        gotgraph = Graph()
        gotgraph.parse(data=content, format="xml")
        self.assertEqualGraphs(wantgraph, gotgraph)

class Search(WSGI):

    def setUp(self):
        super(Search, self).setUp()
        self.env['PATH_INFO'] = '/mysearch/'


    def test_search_single(self):
        self.env['QUERY_STRING'] = "q=subsection"
        res = ([{'dcterms_title': 'Result #1',
                 'uri': 'http://example.org',
                 'text': 'Text that contains the subsection term'}],
               {'pagenum': 1,
                'pagecount': 1,
                'firstresult': 1,
                'lastresult': 1,
                'totalresults': 1})
        
        config = {'connect.return_value': Mock(**{'query.return_value': res})}
        with patch('ferenda.wsgiapp.FulltextIndex', **config):
            status, headers, content = self.call_wsgi(self.env)
        t = etree.fromstring(content)
        resulthead = t.find(".//article/h1").text
        self.assertEqual("1 match for 'subsection'", resulthead)


    def test_search_multiple(self):
        self.env['QUERY_STRING'] = "q=part"
        res = ([{'dcterms_title':'Introduction',
                 'dcterms_identifier': '123/a¶1',
                 'uri':'http://example.org/base/123/a#S1',
                 'text': html.P(['This is ',
                                 html.Strong(['part'], **{'class':'match'}),
                                 ' of document-',
                                 html.Strong(['part'], **{'class':'match'}),
                            ' section 1</p>'])},
                {#'title':'Definitions and Abbreviations',
                 'uri':'http://example.org/base/123/a#S2',
                 'text':html.P(['second main document ',
                                html.Strong(['part'], **{'class':'match'})])},
                {'dcterms_title':'Example',
                 'uri':'http://example.org/base/123/a',
                 'text': html.P(['This is ',
                                 html.Strong(['part'], **{'class':'match'}),
                                 ' of the main document'])}],
               {'pagenum': 1,
                'pagecount': 1,
                'firstresult': 1,
                'lastresult': 3,
                'totalresults': 3})
        
        config = {'connect.return_value': Mock(**{'query.return_value': res})}
        with patch('ferenda.wsgiapp.FulltextIndex', **config):
            status, headers, content = self.call_wsgi(self.env)
        self.assertResponse("200 OK",
                            {'Content-Type': 'text/html; charset=utf-8'},
                            None,
                            status, headers, None)
        t = etree.parse(BytesIO(content))
        css = t.findall("head/link[@rel='stylesheet']")
        self.assertEqual(len(css), 3) # bootstrap, bootstrap-theme, ferenda and sfs (?!)
        self.assertEqual('https://maxcdn.bootstrapcdn.com/bootstrap/3.3.7/css/bootstrap.min.css',
                         css[0].get('href'))
        js = t.findall("body/script")
        self.assertEqual(len(js), 5) # jquery, bootstrap, hammer, ferenda, typeahead
        
        resulthead = t.find(".//article/h1").text
        self.assertEqual(resulthead, "3 matches for 'part'")
        docs = t.findall(".//section[@class='hit']")
        self.assertEqual(len(docs), 3)
        self.assertEqual(docs[0][0].tag, 'h2')
        expect = res[0]
        self.assertIn(expect[0]['dcterms_title'], docs[0][0][0].text)
        self.assertEqual(expect[0]['uri'], docs[0][0][0].get('href'))
        self.assertEqualXML(expect[0]['text'].as_xhtml(),
                            docs[0][1],
                            namespace_aware=False)

        self.assertIn(expect[1]['dcterms_title'], docs[1][0][0].text)
        self.assertEqual(expect[1]['uri'], docs[1][0][0].get('href'))
        self.assertEqualXML(expect[1]['text'].as_xhtml(),
                            docs[1][1],
                            namespace_aware=False)
                         
        self.assertIn(expect[2]['dcterms_title'], docs[2][0][0].text)
        self.assertEqual(expect[2]['uri'], docs[2][0][0].get('href'))
        self.assertEqualXML(expect[2]['text'].as_xhtml(),
                            docs[2][1],
                            namespace_aware=False)
                         

        
    def test_highlighted_snippet(self):
        res = ([{'title':'Example',
                 'uri':'http://example.org/base/123/b1',
                 'text':html.P(['sollicitudin justo ',
                                html.Strong(['needle'], **{'class':'match'}),
                                ' tempor ut eu enim ... himenaeos. ',
                                html.Strong(['Needle'], **{'class':'match'}),
                                ' id tincidunt orci'])}],
               {'pagenum': 1,
                'pagecount': 1,
                'firstresult': 1,
                'lastresult': 1,
                'totalresults': 1})

        self.env['QUERY_STRING'] = "q=needle"
        config = {'connect.return_value': Mock(**{'query.return_value': res})}
        with patch('ferenda.wsgiapp.FulltextIndex', **config):
            status, headers, content = self.call_wsgi(self.env)
        
        self.assertResponse("200 OK",
                            {'Content-Type': 'text/html; charset=utf-8'},
                            None,
                            status, headers, None)
        
        t = etree.fromstring(content)
        docs = t.findall(".//section[@class='hit']")
        self.assertEqualXML(res[0][0]['text'].as_xhtml(),
                            docs[0][1],
                            namespace_aware=False)

    def test_paged(self):
        # FIXME: This should check overflow, ie when results are so
        # many that the pager cannot show all pages and uses »
        # characters.
        def mkres(page=1, pagesize=10, total=25):
            hits = []
            for i in range((page-1)*pagesize, min(page*pagesize, total)):
                hits.append(
                    {'title':'',
                     'uri':'http://example.org/base/123/c#S%d'% ((i*2)-1),
                     'text': html.P(['This is a needle document'])})
            return (hits,
                    {'pagenum': page,
                     'pagecount': int(total / pagesize) + 1,
                     'firstresult': (page - 1) * pagesize + 1,
                     'lastresult': (page - 1) * pagesize + len(hits),
                     'totalresults': total})

        self.env['QUERY_STRING'] = "q=needle"
        res = mkres()
        
        config = {'connect.return_value': Mock(**{'query.return_value': res})}
        with patch('ferenda.wsgiapp.FulltextIndex', **config):
            status, headers, content = self.call_wsgi(self.env)
        self.assertResponse("200 OK",
                            {'Content-Type': 'text/html; charset=utf-8'},
                            None,
                            status, headers, None)                            
        
        t = etree.fromstring(content)
        docs = t.findall(".//section[@class='hit']")
        self.assertEqual(10, len(docs)) # default page size (too small?)
        # assert that pager looks smth like this:
        # <div class="pager">
        #   <p class="label">Results 1-10 of 25</p>
        #   <ul class="pagination">
        #     <li class="active"><a href="/mysearch/?q=needle&p=1">1</a>
        #     <li><a href="/mysearch/?q=needle&p=2">2</a>
        #     <li><a href="/mysearch/?q=needle&p=3">2</a>
        #   </ul>
        # </div>
        pager = t.find(".//div[@class='pager']")
        pagination = pager.find("ul[@class='pagination']")
        self.assertEqual('p', pager[0].tag)
        self.assertEqual('Results 1-10 of 25', pager[0].text)
        self.assertEqual(3, len(pagination))
        self.assertEqual('li', pagination[0].tag)
        self.assertEqual('a', pagination[1][0].tag)
        self.assertEqual('/mysearch/?q=needle&p=2',
                         pagination[1][0].get('href'))

        self.env['QUERY_STRING'] = "q=needle&p=2"
        res = mkres(page=2)
        config = {'connect.return_value': Mock(**{'query.return_value': res})}
        with patch('ferenda.wsgiapp.FulltextIndex', **config):
            status, headers, content = self.call_wsgi(self.env)
        t = etree.fromstring(content)
        docs = t.findall(".//section[@class='hit']")
        self.assertEqual(10, len(docs))
        pager = t.find(".//div[@class='pager']")
        pagination = pager.find("ul[@class='pagination']")
        self.assertEqual('Results 11-20 of 25',pager[0].text)
        self.assertEqual(3,len(pagination))
        self.assertEqual('/mysearch/?q=needle&p=1',pagination[0][0].get('href'))

        self.env['QUERY_STRING'] = "q=needle&p=3"
        res = mkres(page=3)
        config = {'connect.return_value': Mock(**{'query.return_value': res})}
        with patch('ferenda.wsgiapp.FulltextIndex', **config):
            status, headers, content = self.call_wsgi(self.env)
        t = etree.fromstring(content)
        docs = t.findall(".//section[@class='hit']")
        self.assertEqual(5, len(docs))  # only 5 remaining docs
        pager = t.find(".//div[@class='pager']")
        pagination = pager.find("ul[@class='pagination']")
        self.assertEqual(3, len(pagination))
        self.assertEqual('Results 21-25 of 25', pager[0].text)
    
