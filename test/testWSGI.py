#!/usr/bin/env python
# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import os, sys
if os.getcwd() not in sys.path: sys.path.insert(0,os.getcwd())
from ferenda.manager import setup_logger; setup_logger('CRITICAL')
# unittest is imported by ferenda.testutil.RepoTester
# if sys.version_info < (2, 7, 0):
#     import unittest2 as unittest
# else:
#     import unittest
try:
    from unittest.mock import Mock
except ImportError:
    from mock import Mock
from io import BytesIO
import shutil

from rdflib import Graph
from lxml import etree

from ferenda.testutil import RepoTester
    
from ferenda.manager import make_wsgi_app
from ferenda import DocumentRepository
from ferenda import util

# tests the wsgi app in-process, ie not with actual HTTP requests, but
# simulates what make_server().serve_forever() would send and
# recieve. Should be simple enough, yet reasonably realistic, for
# testing the API.
class WSGI(RepoTester):
    def setUp(self):
        super(WSGI,self).setUp()
        self.app = make_wsgi_app(port=8000,
                                 documentroot=self.datadir,
                                 apiendpoint="/myapi/",
                                 searchendpoint="/mysearch/",
                                 repos = [self.repo])

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
        
    
    def test_content_negotiation(self):
        # basic test 1: accept: text/html -> generated file
        env = {'HTTP_ACCEPT': 'text/html',
               'PATH_INFO':   '/res/base/123/a',
               'SERVER_NAME': 'localhost',
               'SERVER_PORT': '8000',
               'wsgi.url_scheme': 'http'}
        status, headers, content = self.call_wsgi(env)
        self.assertResponse("200 OK",
                            {'Content-Type': 'text/html'},
                            util.readfile(self.repo.store.generated_path("123/a"), "rb"),
                            
                            status, headers, content)

        # basic test 2: accept: application/xhtml+xml -> parsed file
        env['HTTP_ACCEPT'] = 'application/xhtml+xml'
        status, headers, content = self.call_wsgi(env)
        self.assertResponse("200 OK",
                            {'Content-Type': 'application/xhtml+xml'},
                            util.readfile(self.repo.store.parsed_path("123/a"), "rb"),
                            status, headers, content)

        # basic test 3: accept: application/rdf+xml -> RDF statements (in XML)
        env['HTTP_ACCEPT'] = 'application/rdf+xml'
        status, headers, content = self.call_wsgi(env)
        self.assertResponse("200 OK",
                            {'Content-Type': 'application/rdf+xml'},
                            util.readfile(self.repo.store.distilled_path("123/a"), "rb"),
                            status, headers, content)

        # Serialization may upset order of triples -- it's not
        # guaranteed that two isomorphic graphs serialize to the exact
        # same byte stream. Therefore, we only compare headers, not
        # content, and follow up with a proper graph comparison
        
        # transform test 4: accept: text/plain -> RDF statements (in NTriples)
        g = Graph()
        g.parse(source=self.repo.store.distilled_path("123/a"))
        env['HTTP_ACCEPT'] = 'text/plain'
        status, headers, content = self.call_wsgi(env)
        self.assertResponse("200 OK",
                            {'Content-Type': 'text/plain'},
                            None,
                            status, headers, None)
        got = Graph()
        got.parse(data=content, format="nt")
        self.assertEqualGraphs(g, got)
        
        # transform test 5: accept: text/turtle -> RDF statements (in Turtle)
        env['HTTP_ACCEPT'] = 'text/turtle'
        status, headers, content = self.call_wsgi(env)
        self.assertResponse("200 OK",
                            {'Content-Type': 'text/turtle'},
                            None,
                            status, headers, None)
        got = Graph()
        got.parse(data=content, format="turtle")
        self.assertEqualGraphs(g, got)
        
        # extended test 6: accept: "/data" -> extended RDF statements
        env['PATH_INFO'] = env['PATH_INFO'] + "/data"
        env['HTTP_ACCEPT'] = 'application/rdf+xml'
        g += self.repo.annotation_file_to_graph(self.repo.store.annotation_path("123/a"))
        status, headers, content = self.call_wsgi(env)
        self.assertResponse("200 OK",
                            {'Content-Type': 'application/rdf+xml'},
                            None,
                            status, headers, None)
        got = Graph()
        got.parse(data=content)
        self.assertEqualGraphs(g, got)
        
        # extended test 7: accept: "/data" + "text/plain" -> extended
        # RDF statements in NTriples
        env['HTTP_ACCEPT'] = 'text/plain'
        status, headers, content = self.call_wsgi(env)
        self.assertResponse("200 OK",
                            {'Content-Type': 'text/plain'},
                            None,
                            status, headers, None)
        got = Graph()
        got.parse(data=content, format="nt")
        self.assertEqualGraphs(g, got)

        # extended test 7: accept: "/data" + "text/turtle" -> extended
        # RDF statements in Turtle
        env['HTTP_ACCEPT'] = 'text/turtle'
        status, headers, content = self.call_wsgi(env)
        self.assertResponse("200 OK",
                            {'Content-Type': 'text/turtle'},
                            None,
                            status, headers, None)
        got = Graph()
        got.parse(data=content, format="turtle")
        self.assertEqualGraphs(g, got)

    def test_search(self):
        # step 1: make sure parsed content is also related (ie in whoosh db)
        self.repo.relate("123/a")

        # search for 'part', which occurs in two Whoosh documents (123/a and 123/a#S1)
        env = {'HTTP_ACCEPT': 'text/html',
               'PATH_INFO':   '/mysearch/',
               'QUERY_STRING': 'q=part',
               'SERVER_NAME': 'localhost',
               'SERVER_PORT': '8000',
               'wsgi.url_scheme': 'http'}
        status, headers, content = self.call_wsgi(env)
        self.assertResponse("200 OK",
                            {'Content-Type': 'text/html; charset=utf-8'},
                            None,
                            status, headers, None)

        t = etree.fromstring(content)
        css = t.findall("head/link[@rel='stylesheet']")
        self.assertEqual(len(css),4) # normalize, main, ferenda, and fonts.googleapis.com
        js = t.findall("head/script")
        self.assertEqual(len(js),3) # jquery, modernizr and ferenda
        
        resulthead = t.find(".//article/h1").text
        self.assertEqual(resulthead, "2 matches for 'part'")
        docs = t.findall(".//div[@class='section-wrapper']")
        self.assertEqual(len(docs), 2)
        self.assertEqual(docs[0][0].tag, 'h2')
        self.assertEqual(docs[0][0][0].text, 'Main')
        self.assertEqual(docs[0][0][0].get('href'), 'http://example.org/base/123/a')
        self.assertEqual(etree.tostring(docs[0][0][1]),
                         'This is <strong class="match term0">part</strong> of the main document, but not part of any sub-resource')
        
        self.assertEqual(docs[0][0].tag, 'h2')
        self.assertEqual(docs[1][0][0].text, '1st sect')
        self.assertEqual(docs[1][0][0].get('href'), 'http://example.org/base/123/a')
        self.assertEqual(etree.tostring(docs[0][1][1]),
                         'This is <strong class="match term0">part</strong> of document-part section 1')
        
        
        # search for 'subsection', which occurs in a single document
        # (123/a#S1.1)
        env['PATH_INFO'] = "/mysearch/?q=subsection"
        status, headers, content = self.call_wsgi(env)
        self.assertIn("1 match for 'subsection'", content)

        
                            
        


