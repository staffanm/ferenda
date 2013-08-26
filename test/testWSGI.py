#!/usr/bin/env python
# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import os, sys
if os.getcwd() not in sys.path: sys.path.insert(0,os.getcwd())
from ferenda.manager import setup_logger; setup_logger('CRITICAL')
if sys.version_info < (2, 7, 0):
    import unittest2 as unittest
else:
    import unittest
try:
    from unittest.mock import Mock
except ImportError:
    from mock import Mock
from io import BytesIO
import shutil
import codecs

from rdflib import Graph
from lxml import etree

from ferenda.testutil import RepoTester
    
from ferenda.manager import make_wsgi_app
from ferenda import DocumentRepository, FulltextIndex
from ferenda import util

# tests the wsgi app in-process, ie not with actual HTTP requests, but
# simulates what make_server().serve_forever() would send and
# recieve. Should be simple enough, yet reasonably realistic, for
# testing the API.
class WSGI(RepoTester): # base class w/o tests
    def setUp(self):
        super(WSGI,self).setUp()
        self.app = make_wsgi_app(port=8000,
                                 documentroot=self.datadir,
                                 apiendpoint="/myapi/",
                                 searchendpoint="/mysearch/",
                                 repos = [self.repo])
        self.env = {'HTTP_ACCEPT': 'text/xml, application/xml, application/xhtml+xml, text/html;q=0.9, text/plain;q=0.8, image/png,*/*;q=0.5',
                    'PATH_INFO':   '/',
                    'SERVER_NAME': 'localhost',
                    'SERVER_PORT': '8000',
                    'wsgi.url_scheme': 'http'}

        # Put files in place: parsed
        util.ensure_dir(self.repo.store.parsed_path("123/a"))
        shutil.copy2("test/files/base/parsed/123/a.xhtml",
                     self.repo.store.parsed_path("123/a"))

        # distilled
        g = Graph()
        g.parse(source="test/files/base/distilled/123/a.ttl", format="turtle")
        with self.repo.store.open_distilled("123/a", "wb") as fp:
            fp.write(g.serialize(format="pretty-xml"))

        # generated
        util.ensure_dir(self.repo.store.generated_path("123/a"))
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


    def call_wsgi(self, environ):
        start_response = Mock()
        buf = BytesIO()
        for chunk in self.app(environ, start_response):
            buf.write(chunk)
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
            self.assertEqual(got_headers[key], value)
        if wanted_content:
            self.assertEqual(wanted_content, got_content)
        
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
                            {'Content-Type': 'text/html'},
                            util.readfile(self.repo.store.generated_path("123/a"), "rb"),
                            status, headers, content)

    def test_xhtml(self):
        # basic test 2: accept: application/xhtml+xml -> parsed file
        self.env['HTTP_ACCEPT'] = 'application/xhtml+xml'
        status, headers, content = self.call_wsgi(self.env)
        self.assertResponse("200 OK",
                            {'Content-Type': 'application/xhtml+xml'},
                            util.readfile(self.repo.store.parsed_path("123/a"), "rb"),
                            status, headers, content)

    def test_rdf(self):
        # basic test 3: accept: application/rdf+xml -> RDF statements (in XML)
        self.env['HTTP_ACCEPT'] = 'application/rdf+xml'
        status, headers, content = self.call_wsgi(self.env)
        self.assertResponse("200 OK",
                            {'Content-Type': 'application/rdf+xml'},
                            util.readfile(self.repo.store.distilled_path("123/a"), "rb"),
                            status, headers, content)

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
        self.assertResponse("200 OK",
                            {'Content-Type': 'text/plain'},
                            None,
                            status, headers, None)
        got = Graph()
        got.parse(data=content, format="nt")
        self.assertEqualGraphs(g, got)

    def test_turtle(self):
        # transform test 5: accept: text/turtle -> RDF statements (in Turtle)
        g = Graph()
        g.parse(source=self.repo.store.distilled_path("123/a"))
        self.env['HTTP_ACCEPT'] = 'text/turtle'
        status, headers, content = self.call_wsgi(self.env)
        self.assertResponse("200 OK",
                            {'Content-Type': 'text/turtle'},
                            None,
                            status, headers, None)
        got = Graph()
        got.parse(data=content, format="turtle")
        self.assertEqualGraphs(g, got)
        
    def test_unacceptable(self):
        self.env['HTTP_ACCEPT'] = 'application/pdf'
        status, headers, content = self.call_wsgi(self.env)
        self.assertResponse("406 Not Acceptable",
                            {'Content-Type': 'text/html'},
                            None,
                            status, headers, None)
    
    def test_extended_rdf(self):
        # extended test 6: accept: "/data" -> extended RDF statements
        self.env['PATH_INFO'] = self.env['PATH_INFO'] + "/data"
        self.env['HTTP_ACCEPT'] = 'application/rdf+xml'
        g = Graph()
        g.parse(source=self.repo.store.distilled_path("123/a"))
        g += self.repo.annotation_file_to_graph(self.repo.store.annotation_path("123/a"))
        status, headers, content = self.call_wsgi(self.env)
        self.assertResponse("200 OK",
                            {'Content-Type': 'application/rdf+xml'},
                            None,
                            status, headers, None)
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
        self.assertResponse("200 OK",
                            {'Content-Type': 'text/plain'},
                            None,
                            status, headers, None)
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
        self.assertResponse("200 OK",
                            {'Content-Type': 'text/turtle'},
                            None,
                            status, headers, None)
        got = Graph()
        got.parse(data=content, format="turtle")
        self.assertEqualGraphs(g, got)


#     # these test require running relate_all and/or toc. skip them for now
#     def test_dataset_html(self):
#         self.env['PATH_INFO'] = "/dataset/base"
#         status, headers, content = self.call_wsgi(self.env)
#         # FIXME: compare result to something (base/toc/index.html)
#         self.assertResponse("200 OK",
#                             {'Content-Type': 'text/html'},
#                             None,
#                             status, headers, None)
# 
#     def test_dataset_ntriples(self):
#         self.env['PATH_INFO'] = "/dataset/base"
#         self.env['HTTP_ACCEPT'] = 'text/plain'
#         status, headers, content = self.call_wsgi(self.env)
#         self.assertResponse("200 OK",
#                             {'Content-Type': 'text/html'},
#                             None,
#                             status, headers, None)
#         got = Graph()
#         got.parse(data=content, format="ntriples")
#         self.assertEqualGraphs(g, got)
# 
# 
#     def test_dataset_turtle(self):
#         self.env['PATH_INFO'] = "/dataset/base"
#         self.env['HTTP_ACCEPT'] = 'text/turtle'
#         status, headers, content = self.call_wsgi(self.env)
#         self.assertResponse("200 OK",
#                             {'Content-Type': 'text/turtle'},
#                             None,
#                             status, headers, None)
#         got = Graph()
#         got.parse(data=content, format="turtle")
#         self.assertEqualGraphs(g, got)
# 
#     def test_dataset_xml(self):
#         self.env['PATH_INFO'] = "/dataset/base"
#         self.env['HTTP_ACCEPT'] = 'application/rdf+xml'
#         status, headers, content = self.call_wsgi(self.env)
#         self.assertResponse("200 OK",
#                             {'Content-Type': 'application/rdf+xml'},
#                             None,
#                             status, headers, None)
#         g = self._dataset_graph()
#         got = Graph()
#         got.parse(data=content, format="xml")
#         self.assertEqualGraphs(g, got)


class Search(object):

    def tearDown(self):
        super(Search,self).tearDown()
        idx = FulltextIndex.connect(self.repo.config.indextype,
                                    self.repo.config.indexlocation)
        idx.destroy()
    
    def _copy_and_distill(self,basefile):
        util.ensure_dir(self.repo.store.parsed_path(basefile))
        shutil.copy2("test/files/base/parsed/%s.xhtml" % basefile,
                     self.repo.store.parsed_path(basefile))
        distilled_graph = Graph()
        with codecs.open(self.repo.store.parsed_path(basefile),
                         encoding="utf-8") as fp: 
            distilled_graph.parse(data=fp.read(), format="rdfa")
        
        util.ensure_dir(self.repo.store.distilled_path(basefile))
        with open(self.repo.store.distilled_path(basefile),
                  "wb") as distilled_file:
            distilled_graph.serialize(distilled_file, format="pretty-xml")

    # So that ESSearch can override the order
    search_multiple_expect = [
        {'title':'Introduction',
         'href':'http://example.org/base/123/a#S1',
         'body':b'<p>This is <strong class="match">part</strong> of document-<strong class="match">part</strong> section 1</p>'},
        {'title':'Definitions and Abbreviations',
         'href':'http://example.org/base/123/a#S2',
         'body':b'<p>second main document <strong class="match">part</strong></p>'},
        {'title':'Example',
         'href':'http://example.org/base/123/a',
         'body':b'<p>This is <strong class="match">part</strong> of the main document</p>'}
    ]
    def test_search_multiple(self):
        # step 1: make sure parsed content is also related (ie in whoosh db)
        self.repo.relate("123/a")

        # search for 'part', which occurs in two Whoosh documents (123/a and 123/a#S1)
        self.env['QUERY_STRING'] = 'q=part'
        status, headers, content = self.call_wsgi(self.env)
        self.assertResponse("200 OK",
                            {'Content-Type': 'text/html; charset=utf-8'},
                            None,
                            status, headers, None)

        t = etree.parse(BytesIO(content))
        css = t.findall("head/link[@rel='stylesheet']")
        self.assertEqual(len(css),4) # normalize, main, ferenda, and fonts.googleapis.com
        self.assertEqual(css[0].get('href'), '../rsrc/css/normalize.css')
        js = t.findall("head/script")
        self.assertEqual(len(js),3) # jquery, modernizr and ferenda
        
        resulthead = t.find(".//article/h1").text
        self.assertEqual(resulthead, "3 matches for 'part'")
        docs = t.findall(".//section[@class='hit']")
        self.assertEqual(len(docs), 3)
        self.assertEqual(docs[0][0].tag, 'h2')
        expect = self.search_multiple_expect
        self.assertIn(expect[0]['title'], docs[0][0][0].text)
        self.assertEqual(expect[0]['href'],  docs[0][0][0].get('href'))
        self.assertEqual(expect[0]['body'],  etree.tostring(docs[0][1]).strip())

        self.assertIn(expect[1]['title'], docs[1][0][0].text)
        self.assertEqual(expect[1]['href'],  docs[1][0][0].get('href'))
        self.assertEqual(expect[1]['body'],  etree.tostring(docs[1][1]).strip())

        self.assertIn(expect[2]['title'], docs[2][0][0].text)
        self.assertEqual(expect[2]['href'],  docs[2][0][0].get('href'))
        self.assertEqual(expect[2]['body'],  etree.tostring(docs[2][1]).strip())

    def test_search_single(self):
        self.repo.relate("123/a")
        # search for 'subsection', which occurs in a single document
        # (123/a#S1.1)
        self.env['QUERY_STRING'] = "q=subsection"
        status, headers, content = self.call_wsgi(self.env)
        t = etree.fromstring(content)
        resulthead = t.find(".//article/h1").text
        self.assertEqual(resulthead, "1 match for 'subsection'")


    highlighted_expect = [
        {'title':'Example',
         'href':'http://example.org/base/123/b1',
         'body':b'<p>sollicitudin justo <strong class="match">needle</strong> tempor ut eu enim ... himenaeos. <strong class="match">Needle</strong> id tincidunt orci</p>'}
        ]
        
    def test_highlighted_snippet(self):
        self._copy_and_distill("123/b")
        self.repo.relate("123/b") # contains one doc with much text and two instances of the sought term
        self.env['QUERY_STRING'] = "q=needle"
        status, headers, content = self.call_wsgi(self.env)
        
        self.assertResponse("200 OK",
                            {'Content-Type': 'text/html; charset=utf-8'},
                            None,
                            status, headers, None)
        
        t = etree.fromstring(content)
        docs = t.findall(".//section[@class='hit']")
        self.assertEqual(self.highlighted_expect[0]['body'],
                         etree.tostring(docs[0][1]).strip())


    def test_paged(self):
        self._copy_and_distill("123/c")
        # 123/c contains 50 docs, 25 of which contains 'needle'
        self.repo.relate("123/c") 
        self.env['QUERY_STRING'] = "q=needle"
        status, headers, content = self.call_wsgi(self.env)
        self.assertResponse("200 OK",
                            {'Content-Type': 'text/html; charset=utf-8'},
                            None,
                            status, headers, None)                            
        
        t = etree.fromstring(content)
        docs = t.findall(".//section[@class='hit']")
        self.assertEqual(10, len(docs)) # default page size (too small?)
        pager = t.find(".//div[@class='pager']")
        
        # assert that pager looks smth like this:
        # <div class="pager">
        #   <p class="label">Results 1-10 of 25</p>
        #   <span class="page">1</span>
        #   <a href="/mysearch/?q=needle&p=2" class="page">2</a>
        #   <a href="/mysearch/?q=needle&p=3" class="page">3</a>
        # </div>
        self.assertEqual(4,len(pager))
        self.assertEqual('p',pager[0].tag)
        self.assertEqual('Results 1-10 of 25',pager[0].text)
        self.assertEqual('span',pager[1].tag)
        self.assertEqual('a',pager[2].tag)
        self.assertEqual('/mysearch/?q=needle&p=2',pager[2].get('href'))

        self.env['QUERY_STRING'] = "q=needle&p=2"
        status, headers, content = self.call_wsgi(self.env)
        t = etree.fromstring(content)
        docs = t.findall(".//section[@class='hit']")
        self.assertEqual(10, len(docs)) 
        pager = t.find(".//div[@class='pager']")
        self.assertEqual(4,len(pager))
        self.assertEqual('Results 11-20 of 25',pager[0].text)
        self.assertEqual('/mysearch/?q=needle&p=1',pager[1].get('href'))

        self.env['QUERY_STRING'] = "q=needle&p=3"
        status, headers, content = self.call_wsgi(self.env)
        t = etree.fromstring(content)
        docs = t.findall(".//section[@class='hit']")
        self.assertEqual(5, len(docs)) # only 5 remaining docs
        pager = t.find(".//div[@class='pager']")
        self.assertEqual(4,len(pager))
        self.assertEqual('Results 21-25 of 25',pager[0].text)


class WhooshSearch(Search, WSGI):
    def setUp(self):
        super(WhooshSearch, self).setUp()
        self.env['PATH_INFO'] = '/mysearch/'


@unittest.skipIf('SKIP_ELASTICSEARCH_TESTS' in os.environ,
                 "Skipping Elasticsearch tests")    
class ESSearch(Search, WSGI):
    # FIXME: Can't yet control ordering and fragment construction to
    # the point where Whoosh and ES act identicallyy. In the meantime,
    # here's a slightly different ordering of the expected results.
    search_multiple_expect = [
        {'title':'Introduction',
         'href':'http://example.org/base/123/a#S1',
         'body':b'<p>This is <strong class="match">part</strong> of document-<strong class="match">part</strong> section 1</p>'},
        {'title':'Definitions and Abbreviations',
         'href':'http://example.org/base/123/a#S2',
         'body':b'<p>This is the second main document <strong class="match">part</strong></p>'},
        {'title':'Example',
         'href':'http://example.org/base/123/a',
         'body':b'<p>This is <strong class="match">part</strong> of the main document</p>'}
    ]

    highlighted_expect = [
        {'title':'Example',
         'href':'http://example.org/base/123/b1',
         'body':b'<p><strong class="match">needle</strong> tempor ut eu enim. Aenean porta ... inceptos himenaeos. <strong class="match">Needle</strong> id</p>'}]


    def setUp(self):
        super(ESSearch, self).setUp()
        self.repo.config.indexlocation = "http://localhost:9200/ferenda/"
        self.repo.config.indextype = "ELASTICSEARCH"
        self.env['PATH_INFO'] = '/mysearch/'
        
