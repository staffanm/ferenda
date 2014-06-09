# -*- coding: utf-8 -*-
from __future__ import unicode_literals

# sys
import json
import os
import sys
import shutil

# mine
from testWSGI import WSGI  # provides the nice call_wsgi func
from ferenda import DocumentRepository
from ferenda import util

class BasicAPI(WSGI):
    # note: self.repo already contains a initialized DocumentRepository
    repos = [DocumentRepository()]

    # is called by WSGI.setUp
    def put_files_in_place(self):
        # create three basic documents (at parsed and distilled)
        #
        # each document should have a dcterms:title, a dcterms:issued and a
        # dcterms:publisher, which has a URI
        #
        # basefile  dcterms:title	  dcterms:issued  dcterms:publisher
        # 123/a     "Example"     2014-01-04  <http://example.org/publisher/A>
        # 123/b     "Example 2"   2013-09-23  <http://example.org/publisher/B>
        # 123/c     "Of needles"  2014-05-06  <http://example.org/publisher/B>
        for i in ('a','b','c'):
            self.ttl_to_rdf_xml("test/files/base/distilled/123/%s.ttl" % i,
                                self.repo.store.distilled_path("123/%s" % i))
            util.ensure_dir(self.repo.store.parsed_path("123/%s" % i))
            shutil.copy2("test/files/base/parsed/123/%s.xhtml" % i,
                                self.repo.store.parsed_path("123/%s" % i))
            self.repo.relate("123/%s" % i)
            # prepare a base.ttl (or var-common.js) that maps
            # <http://example.org/publisher/B> to "Publishing house B"
        self.repo.rdf_type = self.repo.ns['bibo'].Standard

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
