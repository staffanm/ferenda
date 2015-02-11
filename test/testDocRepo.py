# -*- coding: utf-8 -*-
from __future__ import unicode_literals, print_function

import sys, os
from ferenda.compat import unittest
if os.getcwd() not in sys.path: sys.path.insert(0,os.getcwd())

from ferenda.manager import setup_logger; setup_logger('CRITICAL')

from datetime import datetime,date,timedelta
from operator import itemgetter, attrgetter
import codecs
import collections
import shutil
import tempfile
import time
import calendar
import json
import copy
import unicodedata

import lxml.etree as etree
from lxml.etree import XSLT
from lxml.builder import ElementMaker
import rdflib
import requests.exceptions

import six
from six import text_type as str
from ferenda.compat import Mock, MagicMock, patch, call
from bs4 import BeautifulSoup
from layeredconfig import LayeredConfig, Defaults, INIFile
import doctest

from ferenda import DocumentEntry, TocPageset, TocPage, \
    Describer, TripleStore, FulltextIndex, Facet
from ferenda.fulltextindex import WhooshIndex
from ferenda.errors import *


# The main system under test (SUT)
from ferenda import DocumentRepository
from ferenda.testutil import RepoTester

# helper classes
from examplerepos import DocRepo1, DocRepo2, DocRepo3

# various utility functions which occasionally needs patching out
from ferenda import util
from ferenda.elements import serialize, Link

class Repo(RepoTester):
    # TODO: Many parts of this class could be divided into subclasses
    # (like Generate, Toc, News, Storage and Archive already has)

    # class Repo(RepoTester)
    def test_init(self):
        # make sure self.ns is properly initialized
        class StandardNS(DocumentRepository):
            namespaces = ('rdf','dcterms')
        d = StandardNS()
        want = {'rdf':
                rdflib.Namespace('http://www.w3.org/1999/02/22-rdf-syntax-ns#'),
                'dcterms':
                rdflib.Namespace('http://purl.org/dc/terms/')}
        self.assertEqual(want, d.ns)

        class OwnNS(DocumentRepository):
            namespaces = ('rdf',('ex', 'http://example.org/vocab'))
        d = OwnNS()
        want = {'rdf':
                rdflib.Namespace('http://www.w3.org/1999/02/22-rdf-syntax-ns#'),
                'ex':
                rdflib.Namespace('http://example.org/vocab')}
        self.assertEqual(want, d.ns)

    def test_setup_teardown(self):
        defaults = {'example':'config',
                    'setup': None,
                    'teardown': None}

        # It's possible that this is mock-able
        class HasSetup(DocumentRepository):
            @classmethod
            def parse_all_setup(cls, config):
                config.setup = "parse"
        config = LayeredConfig(Defaults(copy.copy(defaults)))
        HasSetup.setup("parse", config)
        HasSetup.teardown("parse", config)
        self.assertEqual(config.setup, "parse")
        self.assertEqual(config.teardown, None)
        
        class HasTeardown(DocumentRepository):
            relate_all_setup = None
            
            @classmethod
            def relate_all_teardown(cls, config):
                config.teardown = "relate"
                
        config = LayeredConfig(Defaults(copy.copy(defaults)))
        HasTeardown.setup("relate", config)
        HasTeardown.teardown("relate", config)
        self.assertEqual(config.setup, None)
        self.assertEqual(config.teardown, "relate")

    def test_dataset_uri(self):
        repo = DocumentRepository()
        self.assertEqual(repo.dataset_uri(), "http://localhost:8000/dataset/base")
        self.assertEqual(repo.dataset_uri('key','value'), "http://localhost:8000/dataset/base?key=value")

    def test_qualified_class_name(self):
        repo = DocumentRepository()
        self.assertEqual(repo.qualified_class_name(),
                         "ferenda.documentrepository.DocumentRepository")

    # class Download(RepoTester)
    def test_download(self):
        # test index file contains four links that matches
        # d.document_url. Three of these contains link text that
        # matches d.basefile_template, and should thus be downloaded
        d = DocumentRepository(loglevel='CRITICAL', datadir=self.datadir)

        d.start_url = "http://localhost/fake/url"
        d.download_single = Mock()
        d.download_single.return_value = True
        d.log = Mock()
        
        # test1: run download, make sure download_single is hit the
        # right amount of times, make sure d.log.error is called once,
        # and ensure lastdownload is set
        mockresponse = Mock()
        with open("%s/files/base/downloaded/index.htm" %
                  os.path.dirname(__file__)) as fp:
            mockresponse.text = fp.read()
        with patch('requests.get', return_value=mockresponse):
            self.assertTrue(d.download())

        # the index file relly has four eligble links, but one is a
        # dupe -- make sure it's filtered out.
        self.assertEqual(d.download_single.call_count,3)
        d.download_single.assert_has_calls([call("123/a","http://example.org/docs/1.html"),
                                            call("123/b","http://example.org/docs/2.html"),
                                            call("124/a","http://example.org/docs/3.html")])
        self.assertAlmostEqualDatetime(d.config.lastdownload,
                                       datetime.now())
        d.download_single.reset_mock()

        # test1.1: Run download with a different index file, where the
        # link text provides no value and instead the links themselves
        # must match document_url_regex.
        mockresponse = Mock()
        with open("%s/files/base/downloaded/index2.htm" % os.path.dirname(__file__)) as fp:
            mockresponse.text = fp.read()
        with patch('requests.get',return_value=mockresponse):
            self.assertTrue(d.download())
        
        self.assertEqual(d.download_single.call_count,3)
        d.download_single.assert_has_calls([call("1","http://example.org/docs/1.html"),
                                            call("2","http://example.org/docs/2.html"),
                                            call("3","http://example.org/docs/3.html")])
        self.assertAlmostEqualDatetime(d.config.lastdownload,
                                       datetime.now())
        d.download_single.reset_mock()
        
        
        # test2: create 2 out of 3 files. make sure download_single is
        # hit only for the remaining file.
        util.ensure_dir(self.datadir+"/base/downloaded/123/a.html")
        open(self.datadir+"/base/downloaded/123/a.html","w").close()
        open(self.datadir+"/base/downloaded/123/b.html","w").close()

        with open("%s/files/base/downloaded/index.htm" % os.path.dirname(__file__)) as fp:
            mockresponse.text = fp.read()
        with patch('requests.get',return_value=mockresponse):
            self.assertTrue(d.download())
        d.download_single.assert_called_once_with("124/a","http://example.org/docs/3.html")
        d.download_single.reset_mock()
        
        # test3: set refresh = True, make sure download_single is hit thrice again.
        d.config.refresh = True
        with patch('requests.get',return_value=mockresponse):
            self.assertTrue(d.download())
        self.assertEqual(d.download_single.call_count,3)
        d.download_single.assert_has_calls([call("123/a","http://example.org/docs/1.html"),
                                            call("123/b","http://example.org/docs/2.html"),
                                            call("124/a","http://example.org/docs/3.html")])
        d.download_single.reset_mock()
        
        # test4: set refresh = False, create the 3rd file, make sure
        # download returns false as nothing changed
        util.ensure_dir(self.datadir+"/base/downloaded/124/a.html")
        open(self.datadir+"/base/downloaded/124/a.html","w").close()
        d.download_single.return_value = False
        d.config.refresh = False
        with patch('requests.get',return_value=mockresponse):
            self.assertFalse(d.download())
        self.assertFalse(d.download_single.error.called)
        d.download_single.reset_mock()

        # test5: basefile parameter
        with patch('requests.get',return_value=mockresponse):
            self.assertFalse(d.download("123/a"))

        # test6: basefile parameter w/o document_url_template
        d.document_url_template = None
        with self.assertRaises(ValueError):
            d.download("123/a")
        
        

    def test_download_single(self):
        url_location = None # The local location of the URL. 
        def my_get(url,**kwargs):
            res = Mock()
            with open(url_location,"rb") as fp:
                res.content = fp.read()
            res.headers = collections.defaultdict(lambda:None)
            res.headers['X-These-Headers-Are'] = 'Faked'
            res.status_code = 200
            return res
        
        d = DocumentRepository(loglevel='CRITICAL', datadir=self.datadir)

        # test1: New file
        url_location = "test/files/base/downloaded/123/a-version1.htm"
        self.assertFalse(os.path.exists(self.datadir+"/base/downloaded/123/a.html"))
        # the url will be dynamically constructed using the
        # document_url template

        with patch('requests.get',side_effect = my_get) as mock_get:
            self.assertTrue(d.download_single("123/a")) 
            self.assertEqual(mock_get.call_args[0][0],
                             "http://example.org/docs/123/a.html")
        self.assertTrue(os.path.exists(self.datadir+"/base/downloaded/123/a.html"))
        self.assertTrue(os.path.exists(self.datadir+"/base/entries/123/a.json"))
        p = DocumentEntry(self.datadir+"/base/entries/123/a.json")
        self.assertIsInstance(p, DocumentEntry)
        self.assertAlmostEqualDatetime(p.orig_created, datetime.now())
        self.assertEqual(p.orig_created, p.orig_updated)
        self.assertEqual(p.orig_created, p.orig_checked)
        self.assertEqual(p.orig_url, "http://example.org/docs/123/a.html")
        self.assertEqual(util.readfile(self.datadir+"/base/downloaded/123/a.html"),
                         util.readfile("test/files/base/downloaded/123/a-version1.htm"))
        # d.browser.retrieve.reset_mock()
        
        # test2: updated file
        time.sleep(0.1) 
        url_location = "test/files/base/downloaded/123/a-version2.htm"
        with patch('requests.get',side_effect = my_get) as mock_get:
            self.assertTrue(d.download_single("123/a", "http://example.org/very/specific/url"))
            self.assertEqual(mock_get.call_args[0][0],
                             "http://example.org/very/specific/url")
        self.assertTrue(os.path.exists(self.datadir+"/base/downloaded/123/a.html"))  

        # make sure download_single tucked away the previous version
        self.assertTrue(os.path.exists(self.datadir+"/base/archive/downloaded/123/a/1.html"))
        self.assertTrue(os.path.exists(self.datadir+"/base/entries/123/a.json"))
        p = DocumentEntry(self.datadir+"/base/entries/123/a.json")
        self.assertAlmostEqualDatetime(p.orig_updated, datetime.now())
        self.assertNotEqual(p.orig_created, p.orig_updated)
        self.assertEqual(p.orig_updated, p.orig_checked)
        self.assertEqual(p.orig_url, "http://example.org/very/specific/url") # orig_url has been modified from test1
        self.assertEqual(util.readfile(self.datadir+"/base/downloaded/123/a.html"),
                         util.readfile("test/files/base/downloaded/123/a-version2.htm"))
        self.assertEqual(util.readfile(self.datadir+"/base/archive/downloaded/123/a/1.html"),
                         util.readfile("test/files/base/downloaded/123/a-version1.htm"))

        # test3: unchanged file
        time.sleep(0.1)
        url_location = "test/files/base/downloaded/123/a-version2.htm" # same as above, ie unchanged
        # d.browser.retrieve.return_value = util.readfile("test/files/base/downloaded/123/a-version2.htm")
        with patch('requests.get',side_effect = my_get) as mock_get:
            self.assertFalse(d.download_single("123/a", "http://example.org/123/a.htm"))
            self.assertEqual(mock_get.call_args[0][0],
                             "http://example.org/123/a.htm")

        p = DocumentEntry(self.datadir+"/base/entries/123/a.json")
        self.assertAlmostEqualDatetime(p.orig_checked, datetime.now())
        self.assertNotEqual(p.orig_created, p.orig_updated)
        self.assertNotEqual(p.orig_created, p.orig_checked)
        self.assertEqual(p.orig_url, "http://example.org/123/a.htm")
        self.assertEqual(util.readfile(self.datadir+"/base/downloaded/123/a.html"),
                         util.readfile("test/files/base/downloaded/123/a-version2.htm"))


    @patch('requests.get')
    def test_download_if_needed(self, mock_get):

        def my_get(url,headers, timeout=None):
            # observes the scoped variables "last_modified" (should
            # contain a formatted date string according to HTTP rules)
            # and "etag" (opaque string).
            resp = Mock()
            resp.status_code=200
            if "If-modified-since" in headers:
                if not expect_if_modified_since:
                    resp.status_code = 400
                    return resp
                if (last_modified and
                    (util.parse_rfc822_date(headers["If-modified-since"]) > 
                     util.parse_rfc822_date(last_modified))):
                    resp.status_code=304
                    return resp
            if "If-none-match" in headers:
                if not expect_if_none_match:
                    resp.status_code=400
                    return resp
                if headers["If-none-match"] == etag:
                    resp.status_code=304
                    return resp
            # Then make sure the response contains appropriate headers
            headers = {}
            if last_modified:
                headers["last-modified"] = last_modified
            else:
                headers["last-modified"] = None
            if etag:
                headers["etag"] = etag
            else:
                headers["etag"] = None

            # And if needed, slurp content from a specified file
            content = None
            if url_location:
                if os.path.exists(url_location):
                    with open(url_location,"rb") as fp:
                        content = fp.read()
                else:
                    resp.status_code = 404
                    resp.raise_for_status.side_effect = requests.exceptions.HTTPError
                    resp.content = b'<h1>404 not found</h1>'
            resp.content = content
            resp.headers = headers
            return resp

        url_location =  None
        last_modified = None
        etag =          None
        expect_if_modified_since = False
        expect_if_none_match     = False
        mock_get.side_effect = my_get
        d = DocumentRepository(loglevel='CRITICAL',datadir=self.datadir)

        # test1: file does not exist, we should not send a
        # if-modified-since, recieve a last-modified header and verify
        # file mtime
        last_modified = "Mon, 4 Aug 1997 02:14:00 EST"
        etag = None
        expect_if_modified_since = False
        expect_if_none_match = False
        url_location = "test/files/base/downloaded/123/a-version1.htm"
        self.assertFalse(os.path.exists(self.datadir+"/base/downloaded/example.html"))

        self.assertTrue(d.download_if_needed("http://example.org/document",
                                             "example"))
        self.assertTrue(mock_get.called)
        self.assertTrue(os.path.exists(self.datadir+"/base/downloaded/example.html"))
        self.assertFalse(os.path.exists(self.datadir+"/base/downloaded/example.html.etag"))
        self.assertEqual(os.stat(self.datadir+"/base/downloaded/example.html").st_mtime,
                         calendar.timegm((1997,8,4,2,14,0,0,0,0)) + (60*60*5)) # EST = UTC-5
        mock_get.reset_mock()

        # test2: file exists, we use if-modified-since, we recieve a 304
        last_modified = "Mon, 4 Aug 1997 02:14:00 EST"
        etag = None
        url_location = "test/files/base/downloaded/123/a-version1.htm"
        expect_if_modified_since = True # since file now exists since test1
        expect_if_none_match = False # since no .etag file was created by test1
        self.assertTrue(os.path.exists(self.datadir+"/base/downloaded/example.html"))
        self.assertFalse(d.download_if_needed("http://example.org/document",
                                              "example"))
        self.assertTrue(mock_get.called)
        self.assertTrue(os.path.exists(self.datadir+"/base/downloaded/example.html"))
        self.assertFalse(os.path.exists(self.datadir+"/base/downloaded/example.html.etag"))
        self.assertEqual(os.stat(self.datadir+"/base/downloaded/example.html").st_mtime,
                         calendar.timegm((1997,8,4,2,14,0,0,0,0)) + (60*60*5)) # EST = UTC-5
        mock_get.reset_mock()
        
        # test3: file exists, we use if-modified-since, we recieve a
        # 200 with later last-modified. Also test the setting of an
        # etag from the server
        last_modified = "Tue, 5 Aug 1997 02:14:00 EST"
        etag = "this-is-my-etag-v1" # will be used in test4
        url_location = "test/files/base/downloaded/123/a-version2.htm"
        expect_if_modified_since = True # since file now exists since test1
        expect_if_none_match = False # since no .etag file was created by test1
        self.assertTrue(os.path.exists(self.datadir+"/base/downloaded/example.html"))
        self.assertTrue(d.download_if_needed("http://example.org/document",
                                             "example"))
        self.assertTrue(mock_get.called)
        self.assertTrue(os.path.exists(self.datadir+"/base/downloaded/example.html")) # since etag is set
        self.assertTrue(os.path.exists(self.datadir+"/base/downloaded/example.html.etag"))
        self.assertEqual(os.stat(self.datadir+"/base/downloaded/example.html").st_mtime,
                         calendar.timegm((1997,8,5,2,14,0,0,0,0)) + (60*60*5)) # EST = UTC-5
        self.assertEqual(etag, util.readfile(self.datadir+"/base/downloaded/example.html.etag"))
        mock_get.reset_mock()
        
        # test4: file and etag exists, we use if-none-match and if-modified_since, we recieve a 304
        last_modified = None
        etag = "this-is-my-etag-v1"
        url_location = "test/files/base/downloaded/123/a-version2.htm"
        expect_if_modified_since = True 
        expect_if_none_match = True
        self.assertFalse(d.download_if_needed("http://example.org/document",
                                              "example"))
        self.assertTrue(mock_get.called)
        self.assertTrue(os.path.exists(self.datadir+"/base/downloaded/example.html"))
        self.assertTrue(os.path.exists(self.datadir+"/base/downloaded/example.html.etag"))
        self.assertEqual(etag, util.readfile(self.datadir+"/base/downloaded/example.html.etag"))
        mock_get.reset_mock()
        
        # test5: file and etag exists, we use if-none-match, we recieve a 200 with a new etag
        last_modified = None
        etag = "this-is-my-etag-v2"
        url_location = "test/files/base/downloaded/123/a-version1.htm"
        expect_if_modified_since = True
        expect_if_none_match = True
        self.assertTrue(d.download_if_needed("http://example.org/document",
                                             "example"))
        self.assertTrue(mock_get.called)
        self.assertTrue(os.path.exists(self.datadir+"/base/downloaded/example.html"))
        self.assertTrue(os.path.exists(self.datadir+"/base/downloaded/example.html.etag"))
        self.assertEqual(etag, util.readfile(self.datadir+"/base/downloaded/example.html.etag"))
        os.unlink(self.datadir+"/base/downloaded/example.html.etag")
        mock_get.reset_mock()
                  
        # test6: file exists, conditionalget is False, document hasn't changed
        d.config.conditionalget = False
        last_modified = None
        etag = None
        url_location = "test/files/base/downloaded/123/a-version1.htm"
        expect_if_modified_since = False
        expect_if_none_match = False
        self.assertFalse(d.download_if_needed("http://example.org/document",
                                              "example"))
        self.assertTrue(mock_get.called)
        self.assertFalse(os.path.exists(self.datadir+"/base/downloaded/example.html.etag"))
        self.assertEqual(util.readfile("test/files/base/downloaded/123/a-version1.htm"),
                         util.readfile(self.datadir+"/base/downloaded/example.html"))
        mock_get.reset_mock()
        
        # test7: file exists, conditionalget is False, document has changed
        d.config.conditionalget = False
        last_modified = None
        etag = None
        url_location = "test/files/base/downloaded/123/a-version2.htm"
        expect_if_modified_since = False
        expect_if_none_match = False
        self.assertTrue(d.download_if_needed("http://example.org/document",
                                             "example"))
        self.assertTrue(mock_get.called)
        self.assertEqual(util.readfile("test/files/base/downloaded/123/a-version2.htm"),
                         util.readfile(self.datadir+"/base/downloaded/example.html"))
        mock_get.reset_mock()

        # test8: 404 Not Found / catch something
        url_location = "test/files/base/downloaded/non-existent"
        with self.assertRaises(requests.exceptions.HTTPError):
            d.download_if_needed("http://example.org/document",
                                 "example")
        mock_get.reset_mock()

        # test9: ConnectionError
        mock_get.side_effect = requests.exceptions.ConnectionError
        self.assertFalse(d.download_if_needed("http://example.org/document",
                                              "example",
                                              sleep=0))
        self.assertEqual(mock_get.call_count, 5)
        mock_get.reset_mock()

        # test10: RequestException
        mock_get.side_effect = requests.exceptions.RequestException
        with self.assertRaises(requests.exceptions.RequestException):
            d.download_if_needed("http://example.org/document",
                                 "example")
        mock_get.reset_mock()

        


    def test_remote_url(self):
        d = DocumentRepository()
        d.config = LayeredConfig(Defaults(d.get_default_options()),
                                 INIFile("ferenda.ini"),
                                 cascade=True)
        self.assertEqual(d.remote_url("123/a"), "http://example.org/docs/123/a.html")
        self.assertEqual(d.remote_url("123:a"), "http://example.org/docs/123%3Aa.html")
        self.assertEqual(d.remote_url("123 a"), "http://example.org/docs/123%20a.html")


    # class Parse(RepoTester)

    def test_parse(self):
        xhtmlns = "{http://www.w3.org/1999/xhtml}"
        xmlns = "{http://www.w3.org/XML/1998/namespace}"

        # test1: make sure that default parsing of a document w/o
        # title and lang tags work
        d = DocumentRepository(loglevel="CRITICAL", datadir=self.datadir)
        config = LayeredConfig(Defaults(d.get_default_options()),
                               INIFile("ferenda.ini"),
                               cascade=True)
        config.datadir = self.datadir
        d.config = config
        path = d.store.downloaded_path("123/a")
        util.ensure_dir(path)
        shutil.copy2("test/files/base/downloaded/123/a-version1.htm",path)
        ret = d.parse("123/a")

        g = rdflib.Graph()
        uri = d.canonical_uri("123/a")
        desc = Describer(g,uri)
        g.parse(d.store.distilled_path("123/a"))
        
        self.assertEqual(len(g),3)
        self.assertEqual(desc.getvalue(d.ns['dcterms'].identifier), "123/a")
        self.assertEqual(len(desc.getvalues(d.ns['dcterms'].title)),0)

        t = etree.parse(d.store.parsed_path("123/a"))

        h = t.getroot()
        self.assertEqual("en", h.get(xmlns+"lang"))
        b = t.find(xhtmlns+"body")
        self.assertEqual("http://localhost:8000/res/base/123/a", b.get("about"))
        ps = t.findall(xhtmlns+"body/"+xhtmlns+"p")
        self.assertEqual(1,len(list(ps)))
        os.unlink(d.store.parsed_path("123/a"))
        os.unlink(d.store.distilled_path("123/a"))

        # test2: make sure that default parsing of a document with a
        # title, lang tag and multiple body elements work.
        d = DocumentRepository(loglevel="CRITICAL",datadir=self.datadir)
        path = d.store.downloaded_path("123/a")
        util.ensure_dir(path)
        shutil.copy2("test/files/base/downloaded/123/a-version2.htm",path)
        ret = d.parse("123/a")

        g = rdflib.Graph()
        uri = d.canonical_uri("123/a")
        desc = Describer(g,uri)
        g.parse(d.store.distilled_path("123/a"))
        
        self.assertEqual(len(g),4)
        self.assertEqual(desc.getvalue(d.ns['dcterms'].identifier), "123/a")
        self.assertEqual(desc.getvalue(d.ns['dcterms'].title), "A document")

        t = etree.parse(d.store.parsed_path("123/a"))
        # util.indent_et(t.getroot())

        h = t.getroot()
        self.assertEqual("en-GB", h.get(xmlns+"lang"))
        b = t.find(xhtmlns+"body")
        self.assertEqual("http://localhost:8000/res/base/123/a", b.get("about"))
        ps = t.findall(xhtmlns+"body/"+xhtmlns+"p")
        self.assertEqual(2,len(list(ps)))
        os.unlink(d.store.parsed_path("123/a"))
        os.unlink(d.store.distilled_path("123/a"))

        # test3: parsing of a ill-formatted document without html section

    def test_soup_from_basefile(self):
        d = DocumentRepository(datadir=self.datadir)
        util.ensure_dir(d.store.downloaded_path("testbasefile"))
        # test 1: Empty tags
        with open(d.store.downloaded_path("testbasefile"), "w") as fp:
            fp.write("<h1>Hello<br>World</h1>")
        soup = d.soup_from_basefile("testbasefile")
        # This fails on py33, since we can't use the lxml parser, and
        # beautifulsoup's html.parser does not know that <br> is a
        # self-closing tag. What are you gonna do?
        self.assertEqual(soup.h1.decode(), '<h1>Hello<br/>World</h1>')
            

        # test 2: Non-ascii characters
        with codecs.open(d.store.downloaded_path("testbasefile"), "w", encoding="utf-8") as fp:
            fp.write("<h1>R\xe4ksm\xf6rg\xe5s</h1>")
        soup = d.soup_from_basefile("testbasefile")
        self.assertEqual(soup.h1.decode(), '<h1>R\xe4ksm\xf6rg\xe5s</h1>')
        
        os.unlink(d.store.downloaded_path("testbasefile"))

    def test_parse_document_from_soup(self):
        d = DocumentRepository()
        doc = d.make_document("testbasefile")
        # test 1: default selector/filters
        testdoc = """
<html>
  <head>
    <title>Test doc</title>
  </head>
  <body>
    <div id="header">
      <h1>Hello</h1>
    </div>
    <div id="main">
      <div class="navbar">
	<ul>
	  <li>Navigation</li>
	</ul>
      </div>
      <script type="javascript">
	// inline javascript code
      </script>
      <p>This is the main content</p>
    </div>
  </body>
</html>"""
        soup = BeautifulSoup(testdoc)
        d.parse_document_from_soup(soup,doc)
        #print("Defaults")
        #print(serialize(doc.body))
        self.assertEqual(serialize(doc.body),"""<Body>
  <Div id="header">
    <H1>
      <str>Hello</str>
    </H1>
  </Div>
  <Div id="main">
    <Div class="navbar">
      <UL>
        <LI>
          <str>Navigation</str>
        </LI>
      </UL>
    </Div><P>
      <str>This is the main content</str>
    </P>
  </Div>
</Body>
""")

        # test 2: adjusted selector/filters
        d.parse_content_selector = "div#main"
        d.parse_filter_selectors = ["script","div.navbar"]
        d.parse_document_from_soup(soup,doc)
        #print("Adjusted")
        #print(serialize(doc.body))
        self.assertEqual(serialize(doc.body),"""<Div id="main">
  <P>
    <str>This is the main content</str>
  </P>
</Div>
""")
        # test 3: selector that do not match anything
        d.parse_content_selector = "article"
        with self.assertRaises(ParseError):
            d.parse_document_from_soup(soup,doc)

        # test 4: selector that matches more than one thing
        d.parse_content_selector = "div"
        d.parse_document_from_soup(soup,doc)

        self.assertEqual(serialize(doc.body),"""<Div id="header">
  <H1>
    <str>Hello</str>
  </H1>
</Div>
""")


    def test_render_xhtml_head(self):
        doc = self.repo.make_document('basefile')
        headmeta = rdflib.Graph().parse(format='n3', data="""
@prefix bibo: <http://purl.org/ontology/bibo/> .
@prefix dcterms: <http://purl.org/dc/terms/> .
@prefix foaf: <http://xmlns.com/foaf/0.1/> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

<http://localhost:8000/res/base/basefile> a bibo:Document;
        dcterms:author <http://localhost:8000/people/fred> ;
        dcterms:title "Document title"@en ;
        dcterms:title "Document title (untyped)" ;
        dcterms:identifier "Doc:1"@en ;
        dcterms:issued "2013-10-17"^^xsd:date .

<http://localhost:8000/people/fred> a foaf:Person;
        foaf:name "Fred Bloggs"@en ;
        dcterms:title "This doesn't make any sense" ;
        dcterms:issued "2013-10-17"^^xsd:date .

<http://localhost:8000/res/base/other> a bibo:Document;
        dcterms:references <http://localhost:8000/res/base/basefile> .

        """)
        doc.meta += headmeta
        doc.lang = None
        
        outfile = self.datadir + "/test.xhtml"
        self.repo.render_xhtml(doc, outfile)
        want = """<html xmlns="http://www.w3.org/1999/xhtml"
                        xmlns:bibo="http://purl.org/ontology/bibo/"
                        xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
                        version="XHTML+RDFa 1.1"
                        xsi:schemaLocation="http://www.w3.org/1999/xhtml http://www.w3.org/MarkUp/SCHEMA/xhtml-rdfa-2.xsd"        
                        xmlns:dcterms="http://purl.org/dc/terms/">
  <head about="http://localhost:8000/res/base/basefile">
    <link href="http://localhost:8000/people/fred" rel="dcterms:author"></link>
    <meta about="http://localhost:8000/people/fred" content="2013-10-17" datatype="xsd:date" property="dcterms:issued"></meta>
    <meta about="http://localhost:8000/people/fred" content="This doesn't make any sense" property="dcterms:title" xml:lang=""></meta>
    <link about="http://localhost:8000/people/fred" href="http://xmlns.com/foaf/0.1/Person" rel="rdf:type"></link>
    <meta about="http://localhost:8000/people/fred" content="Fred Bloggs" property="foaf:name" xml:lang="en"></meta>
    <meta content="Doc:1" property="dcterms:identifier" xml:lang="en"></meta>
    <meta content="2013-10-17" datatype="xsd:date" property="dcterms:issued"></meta>
    <link href="http://localhost:8000/res/base/other" rev="dcterms:references"></link>
    <title property="dcterms:title" xml:lang="">Document title (untyped)</title>
    <title property="dcterms:title">Document title</title>
    <link href="http://purl.org/ontology/bibo/Document" rel="rdf:type"></link>
  </head>      
  <body about="http://localhost:8000/res/base/basefile"/>
</html>"""
        self.assertEqualXML(want, util.readfile(outfile, "rb"))
        

        
    # class Relate(RepoTester)
    @patch('ferenda.documentrepository.TripleStore')
    def test_relate_all_setup(self, mock_store):
        # so that list_basefiles_for finds something
        util.writefile(self.datadir+"/base/distilled/1.rdf", "example")
        config = LayeredConfig(Defaults({'datadir': self.datadir,
                                         'url': 'http://localhost:8000/',
                                         'force': False,
                                         'storetype': 'a',
                                         'storelocation': 'b',
                                         'storerepository': 'c'}))
        self.assertTrue(self.repoclass.relate_all_setup(config))
        self.assertFalse(mock_store.connect.called) # store shouldn't
                                                    # be called unless
                                                    # a total clean
                                                    # and reindex (ie
                                                    # --force) has
                                                    # been requested
        # self.assertTrue(mock_store.connect.return_value.clear.called)
        
        # if triplestore dump is newer than all parsed files, nothing
        # has happened since last relate --all and thus we shouldn't
        # work at all (signalled by relate_all_setup returning False.
        util.writefile(self.datadir+"/base/distilled/dump.nt", "example")
        self.assertFalse(self.repoclass.relate_all_setup(config))

    @patch('ferenda.documentrepository.TripleStore')
    def test_relate_all_teardown(self, mock_store):
        util.writefile(self.datadir+"/base/distilled/dump.nt", "example")
        config = LayeredConfig(Defaults({'datadir': self.datadir,
                                         'url': 'http://localhost:8000/',
                                         'force': False,
                                         'storetype': 'a',
                                         'storelocation': 'b',
                                         'storerepository': 'c'}))
        self.assertTrue(self.repoclass.relate_all_teardown(config))
        self.assertTrue(mock_store.connect.called)
        self.assertTrue(mock_store.connect.return_value.get_serialized_file.called)

    test_rdf_xml = b"""<?xml version="1.0" encoding="utf-8"?>
<rdf:RDF
  xmlns:dcterms="http://purl.org/dc/terms/"
  xmlns:bibo="http://purl.org/ontology/bibo/"
  xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
>
  <bibo:Document rdf:about="http://localhost:8000/res/base/root">
    <dcterms:updates rdf:resource="http://localhost:8000/res/base/res-a"/>
    <dcterms:references rdf:resource="http://localhost:8000/res/other/res-b"/>
    <rdf:seeAlso rdf:resource="http://localhost:8000/somewhere/else"/>
  </bibo:Document>
</rdf:RDF>"""    
        
    def test_relate(self):
        # the helper methods are called separately. this test only
        # makes sure they are all called:
        self.repo.relate_triples = Mock()
        self.repo.relate_dependencies = Mock()
        self.repo.relate_fulltext = Mock()
        self.repo.config.force = True  # otherwise smart dependency tracking kicks in
        self.repo.relate("123/a")
        self.assertTrue(self.repo.relate_triples.called)
        self.assertTrue(self.repo.relate_dependencies.called)
        self.assertTrue(self.repo.relate_fulltext.called)
    
            
    def test_relate_triples(self):
        # dump known triples as rdf/xml (want) to self.repo.store.distilled_path
        with self.repo.store.open_distilled('root', 'wb') as fp:
            fp.write(self.test_rdf_xml)

        import ferenda.documentrepository
        assert ferenda.documentrepository
        # We mock out TripleStore to avoid creating an actual triplestore
        with patch('ferenda.documentrepository.TripleStore.connect') as mock:
            self.repo.relate_triples("root")
            self.assertTrue(mock.called)  # ie a TripleStore class has been instantiated
            # add_serialized is a new MagicMock object
            add_serialized = self.repo._triplestore.add_serialized
            self.assertTrue(add_serialized.called)
            got = add_serialized.call_args[0][0]
            format = add_serialized.call_args[1]['format']

        self.assertEqual(self.test_rdf_xml,
                         got)
        self.assertEqual("xml", format)

    def test_relate_dependencies(self):
        # 1. create two docrepos A (self.repo?) and B
        class OtherRepo(DocumentRepository):
            alias = "other"
        # 2.  create distilled for basefile 'root' in repo A that refers to
        #  2.1.  one resource res-a in repo A, and
        #  2.2. another resource res-b in repo B
        with self.repo.store.open_distilled('root', 'wb') as fp:
            fp.write(self.test_rdf_xml)
        
        # 3. relate_dependencies on repo A for basefile root
        otherrepo = OtherRepo(datadir=self.datadir)
        repos = [self.repo,otherrepo]
        self.repo.relate_dependencies("root", repos)

        # 3.1 do it again (to test adding to existing files)
        self.repo.relate_dependencies("root", repos)

        # 4. Assert that
        #  4.1 self.repo.store.dependencies_path contains parsed_path('root')
        dependencyfile = self.repo.store.parsed_path('root') + os.linesep
        self.assertEqual(util.readfile(self.repo.store.dependencies_path("res-a")),
                         dependencyfile)

        #  4.2 otherrepo.store.dependencies_path contains parsed_path('root')
        self.assertEqual(util.readfile(otherrepo.store.dependencies_path("res-b")),
                         dependencyfile)
        #  4.3 no other deps files exists in datadir
        self.assertEqual(2,
                         len(list(util.list_dirs(self.datadir, '.txt'))))

        # 5. Finally, create a basefile with a complicated name
        # (KFD-normalized latin-1 name,which yields characters outside
        # of latin-1) that relates to res-a
        bf = unicodedata.normalize("NFD", "räksmörgås")
        with self.repo.store.open_distilled(bf, 'wb') as fp:
            fp.write(self.test_rdf_xml)
        # 5.1 relate it
        self.repo.relate_dependencies(bf, repos)
        
        # 5.2 assert that it has been recorded
        dependencyline = self.repo.store.parsed_path(bf) + os.linesep
        self.assertIn(dependencyline,
                      util.readfile(self.repo.store.dependencies_path("res-a")))
        self.assertIn(dependencyline,
                      util.readfile(otherrepo.store.dependencies_path("res-b")))
        self.assertEqual(2,
                         len(list(util.list_dirs(self.datadir, '.txt'))))


    def test_status(self):
        want  = """
Status for document repository 'base' (ferenda.documentrepository.DocumentRepository)
 download: None.
 parse: None.
 generated: None.
""".strip()
        builtins = "__builtin__" if six.PY2 else "builtins"
        with patch(builtins+".print") as printmock:
            self.repo.status()
        got = "\n".join([x[1][0] for x in printmock.mock_calls])
        self.assertEqual(want,got)

        # test both status and get_status in one swoop.
        for basefile in range(1,13):
            util.writefile(self.repo.store.downloaded_path(str(basefile)),
                           "downloaded %s" % basefile)
        for basefile in range(1,9):
            util.writefile(self.repo.store.parsed_path(str(basefile)),
                           "parsed %s" % basefile)
        for basefile in range(1,5):
            util.writefile(self.repo.store.generated_path(str(basefile)),
                           "generated %s" % basefile)

        want  = """
Status for document repository 'base' (ferenda.documentrepository.DocumentRepository)
 download: 12, 11, 10... (9 more)
 parse: 8, 7, 6... (5 more) Todo: 12, 11, 10... (1 more)
 generated: 4, 3, 2... (1 more) Todo: 8, 7, 6... (1 more)
""".strip()
        builtins = "__builtin__" if six.PY2 else "builtins"
        with patch(builtins+".print") as printmock:
            self.repo.status()
        got = "\n".join([x[1][0] for x in printmock.mock_calls])
        self.assertEqual(want,got)

    def test_tabs(self):
        # base test - if using rdftype of foaf:Document, in that case
        # we'll use .alias
        self.assertEqual(self.repo.tabs(),
                         [("base", "http://localhost:8000/dataset/base")])
        self.repo.rdf_type = rdflib.Namespace("http://example.org/vocab#Report")
        self.assertEqual(self.repo.tabs(),
                         [("Report", "http://localhost:8000/dataset/base")])
        

class RelateFulltext(RepoTester):
    # FIXME: Move assertEqualCalls and put_files_in_place to
    # RepoTester once debugged
    def assertEqualCalls(self, want_calls, got_calls):
        """Replacement for Mock.assert_has_calls that provides more helpful
        messages as to where exactly a list of calls differ.

        """
        self.assertEqual(len(want_calls), len(got_calls),
                         "Number of calls differ (want %s, got %s)" %
                         (len(want_calls), len(got_calls)))
        for callidx, want_call in enumerate(want_calls):
            # maybe use zip + enumerate rather than array indexing?
            got_call = got_calls[callidx]

            # compare name
            self.assertEqual(want_call[0], got_call[0],
                             "Call #%s has wrong name" % callidx)

            # compare each positional argument
            self.assertEqual(want_call[1], got_call[1],
                             "Positional args of call #%s differ" % callidx)

            # compare keyword argument dicts
            self.assertEqual(want_call[2], got_call[2],
                             "Keyword args of call %s differ" % callidx)

    def put_files_in_place(self, repo, prefix, basefiles,
                           distill=True, convert=False):
        for basefile in basefiles:
            source = "%s/parsed/%s.xhtml" % (prefix, basefile)
            dest = repo.store.parsed_path(basefile)
            util.ensure_dir(dest)
            shutil.copy2(source, dest)
            util.ensure_dir(repo.store.distilled_path(basefile))
            if distill:
                g = rdflib.Graph()

                with codecs.open(dest, encoding="utf-8") as fp:  # unicode
                    g.parse(data=fp.read(), format="rdfa",
                                          publicID=repo.canonical_uri(basefile))
                g.bind("dc", rdflib.URIRef("http://purl.org/dc/elements/1.1/"))
                g.bind("dcterms", rdflib.URIRef("http://example.org/this-prefix-should-not-be-used"))
                with open(repo.store.distilled_path(basefile), "wb") as fp:
                    g.serialize(fp, format="pretty-xml")
            elif convert:
                g = rdflib.Graph()
                ttlsource = "%s/distilled/%s.ttl" % (prefix, basefile)
                with codecs.open(ttlsource ,encoding="utf-8") as fp:
                    g.parse(fp, format="turtle")
                with open(repo.store.distilled_path(basefile),"wb") as fp:
                    g.serialize(fp,"pretty-xml")                

    # A bunch of testcases that excercise the fulltext indexing
    # logic. Handles indexing of all types, missing data, unexpected
    # datatypes and so on.
    def test_basic(self):
        d = DocumentRepository(datadir=self.datadir,
                               indexlocation=self.datadir+os.sep+"index")
        self.put_files_in_place(d, "test/files/base", ["123/a"],
                                distill=False,
                                convert=True)

        with patch.object(WhooshIndex,'update') as mock_method:
            d.relate_fulltext("123/a", [d])
            want = [call(basefile='123/a',
                         uri='http://example.org/base/123/a', repo='base',
                         text='This is part of the main document, but not of any sub-resource. This is the tail end of the main document',
                         rdf_type='http://purl.org/ontology/bibo/Standard',
                         dcterms_title='Example',
                         dcterms_identifier='123(A)',
                         dcterms_issued=date(2014,1,4),
                         dcterms_publisher={'iri':'http://example.org/publisher/A',
                                            'label':'http://example.org/publisher/A'}),
                    call(dcterms_title='Introduction',
                         rdf_type='http://purl.org/ontology/bibo/DocumentPart',
                         basefile='123/a',
                         uri='http://example.org/base/123/a#S1', repo='base',
                         text='This is part of document-part section 1',
                         dcterms_identifier='123(A)\xb61'),  # \xb6 = Pilcrow 
                    call(dcterms_title='Requirements Language',
                         rdf_type='http://purl.org/ontology/bibo/DocumentPart',
                         basefile='123/a',
                         uri='http://example.org/base/123/a#S1.1', repo='base',
                         text='This is the text in subsection 1.1',
                         dcterms_identifier='123(A)\xb61.1'),
                    call(dcterms_title='Definitions and Abbreviations',
                         rdf_type='http://purl.org/ontology/bibo/DocumentPart',
                         basefile='123/a',
                         uri='http://example.org/base/123/a#S2', repo='base',
                         text='This is the second main document part',
                         dcterms_identifier='123(A)\xb62')]
            got = mock_method.mock_calls
            self.assertEqualCalls(want, got)
            # do a little extra assertion since equality tests of call
            # objects think that 'foo' and b'foo' are equal (at least
            # under py2)
            self.assertIsInstance(got[0][2]['rdf_type'], str)

            
            
    # this tests DocRepo2, which has test data for all commonly
    # indexed datatypes
    def test_types(self):
        repo = DocRepo2(datadir=self.datadir,
                        indexlocation=self.datadir+os.sep+"index")
        self.put_files_in_place(repo, "test/files/testrepos/repo2", ["a"])
        with patch.object(WhooshIndex,'update') as mock_method:
            repo.relate_fulltext("a", [repo])

        want = [call(aprilfools=True,
                     basefile = 'a',
                     repo = 'repo2',
                     uri = 'http://example.org/repo2/a',
                     text = 'This is part of the main document, but not of any sub-resource.',
                     dc_subject = ['green', 'yellow'],
                     dcterms_issued = date(2012, 4, 1),
                     dcterms_publisher = {'iri': 'http://example.org/vocab/publ1',
                                          'label': 'Publishing & sons'},
                     dcterms_title = 'A doc with all datatypes',
                     rdf_type = 'http://example.org/vocab/MainType',
                     schema_free =  True)]
        got = mock_method.mock_calls
        self.assertEqualCalls(want, got)

    def test_unexpected_type(self):
        repo = DocRepo3(datadir=self.datadir,
                        indexlocation=self.datadir+os.sep+"index")
        self.put_files_in_place(repo, "test/files/testrepos/repo3", ["b"])
        with patch.object(WhooshIndex,'update') as mock_method:
            repo.relate_fulltext("b", [repo])

        want = [call(basefile='b',
                     repo='repo3',
                     uri='http://example.org/repo3/b',
                     text="A document with common properties, but unusual data types for those properties.",
                     dc_creator='Fred Bloggs',
                     dcterms_identifier='3 stroke B',
                     # dcterms_issued='June 10th, 2014', # removed non-standard DCTERMS.issued property
                     dcterms_rightsHolder= [{'iri': 'http://example.org/vocab/company1',
                                             'label': 'Comp Inc'},
                                            {'iri': 'http://example.org/vocab/company2',
                                             'label': 'Another company'}],
                     dcterms_title='A doc with unusual metadata'
                 ),
                call(basefile='b',
                     dcterms_identifier='3/B (1)',
                     repo='repo3',
                     uri='http://example.org/repo3/b#S1',
                     text="This is part of a subdocument, that has some unique properties",
                     dc_creator=date(2012,4,1))]
        got = mock_method.mock_calls
        self.assertEqualCalls(want, got)


    def test_missing(self):
        repo = DocRepo3(datadir=self.datadir,
                        indexlocation=self.datadir+os.sep+"index")
        self.put_files_in_place(repo, "test/files/testrepos/repo3", ["c"])
        with patch.object(WhooshIndex,'update') as mock_method:
            repo.relate_fulltext("c", [repo])

        want = [call(basefile = 'c',
                     repo = 'repo3',
                     uri = 'http://example.org/repo3/c',
                     text = "This document lacks all extra metadata that it's repo's Facet expects to be there.")]
        got = mock_method.mock_calls
        self.assertEqualCalls(want, got)


#     def test_synthetic(self):
#         # test the ability to index synthetic data, ie data that is
#         # processed in some way from how it's present in the XHTML
#         # data (eg. a date like 2014-04-01 transformed into a boolean
#         # specifying whether it's 1st of April or not).
#         #
#         # Use something in repo2 for this (has a is_april_fools selector)
#         pass

class Generate(RepoTester):

    class TestRepo(DocumentRepository):
        alias = "test"
        
        def canonical_uri(self,basefile):
            return "http://example.org/repo/a/%s" % basefile

    repoclass = TestRepo
            
    def setUp(self):
        super(Generate, self).setUp() # sets up self.repo, self.datadir
        resources = self.datadir+os.sep+"rsrc"+os.sep+"resources.xml"
        util.ensure_dir(resources)
        shutil.copy2("%s/files/base/rsrc/resources.xml"%os.path.dirname(__file__),
                     resources)

    def test_graph_to_annotation_file(self):
        testgraph = rdflib.Graph()
        testgraph.parse(
            data=util.readfile("test/files/datasets/annotations_b1.ttl"),
            format="turtle")
        testgraph.bind("a", rdflib.Namespace("http://example.org/repo/a/"))
        testgraph.bind("b", rdflib.Namespace("http://example.org/repo/b/"))
        testgraph.bind("dcterms", rdflib.Namespace("http://purl.org/dc/terms/"))
        annotations = self.repo.graph_to_annotation_file(testgraph)
        self.maxDiff = None
        want = """<graph xmlns:dcterms="http://purl.org/dc/terms/"
       xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
       xmlns:b="http://example.org/repo/b/"
       xmlns:a="http://example.org/repo/a/">
  <resource uri="http://example.org/repo/b/1">
    <a><b:BarDoc/></a>
    <dcterms:identifier>B1</dcterms:identifier>
    <dcterms:isReferencedBy ref="http://example.org/repo/b/1part"/>
    <dcterms:references ref="http://example.org/repo/a/1"/>
    <dcterms:title>The title of Document B 1</dcterms:title>
  </resource>
  <resource uri="http://example.org/repo/b/1part">
    <a><a:DocumentPart/></a>
    <dcterms:identifier>B1(part)</dcterms:identifier>
    <dcterms:isPartOf ref="http://example.org/repo/b/1"/>
    <dcterms:references ref="http://example.org/repo/a/1"/>
  </resource>
</graph>"""
        self.assertEqualXML(want,annotations)

    def test_generated(self):
        with self.repo.store.open_parsed("1", "w") as fp:
            fp.write("""<?xml version='1.0' encoding='utf-8'?>
<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML+RDFa 1.0//EN" "http://www.w3.org/MarkUp/DTD/xhtml-rdfa-1.dtd">
<html xmlns:a="http://example.org/repo/a/" xmlns:b="http://example.org/repo/b/"  xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns:xsd="http://www.w3.org/2001/XMLSchema#" xmlns:dcterms="http://purl.org/dc/terms/" xmlns="http://www.w3.org/1999/xhtml">
  <head about="http://example.org/repo/a/1">
    <link href="http://example.org/repo/a/FooDoc" rel="rdf:type"/>
    <meta content="A1" property="dcterms:identifier"/>
    <title property="dcterms:title" xml:lang="">The title of Document A 1</title>
  </head>
  <body about="http://example.org/repo/a/1">
      <div><p>Main document text</p></div>
      <div content="A1(part)" about="http://example.org/repo/a/1part" property="dcterms:identfier" typeof="a:DocumentPart">
        <p>Document part text</p>
      </div>
  </body>
</html>""")
        self.assertEqual("http://example.org/repo/a/1",
                         self.repo.canonical_uri("1"))
        g = rdflib.Graph()
        g.parse(data=util.readfile("test/files/datasets/annotations_a1.ttl"),
                format="turtle")
        # Semi-advanced patching: Make sure that the staticmethod
        # TripleStore.connect returns a mock object, whose construct
        # method returns our graph
        config = {'connect.return_value': Mock(**{'construct.return_value': g})}
        with patch('ferenda.documentrepository.TripleStore', **config):
            self.repo.generate("1")
        
        t = etree.parse(self.repo.store.generated_path("1"))
        # find top node .annotations,
        anode = t.find(".//aside[@class='annotations']")
        annotations = anode.findall("a")
        # confirm that exactly a:2, a:2#part1, b:1, b:1#part is there
        self.assertEqual(4, len(annotations))
        labels = set([a.text    for a in annotations])
        self.assertEqual(set(['B1(part)',
                              'A2(part1)',
                              'B1',
                              'A2']),
                         labels)
        refs   = set([a.get('href') for a in annotations])
        self.assertEqual(set(['http://example.org/repo/b/1',
                              'http://example.org/repo/a/2',
                              'http://example.org/repo/b/1part',
                              'http://example.org/repo/a/2part1']),
                         refs)
        anode = t.find(".//div[@about='http://example.org/repo/a/1part']/aside")
        annotations = anode.findall("a")
        self.assertEqual(1, len(annotations))
        self.assertEqual('http://example.org/repo/a/2part2',
                         annotations[0].get('href'))
        self.assertEqual('A2(part2)',
                         annotations[0].text)

    def _generate_complex(self, xsl=None, sparql=None, staticsite=False):
        # Helper func for other tests -- this uses a single
        # semi-complex source doc, runs it through the generic.xsl
        # stylesheet, and then the tests using this helper confirm
        # various aspects of the transformed document
        if staticsite:
            self.repo.config.staticsite = True
        if xsl is not None:
            self.repo.xslt_template = xsl

        if sparql is not None:
            self.repo.sparql_annotations = sparql

        test = """<?xml version='1.0' encoding='utf-8'?>
<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML+RDFa 1.0//EN" "http://www.w3.org/MarkUp/DTD/xhtml-rdfa-1.dtd">
<html xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns:bibo="http://purl.org/ontology/bibo/" xmlns:xsd="http://www.w3.org/2001/XMLSchema#" xmlns:dcterms="http://purl.org/dc/terms/" xmlns="http://www.w3.org/1999/xhtml" xml:lang="en">
  <head about="http://localhost:8000/res/w3c/hr-time">
    <meta property="dcterms:editor" content="Jatinder Mann" xml:lang=""/>
    <meta property="dcterms:identifier" content="hr-time" xml:lang=""/>
    <meta property="dcterms:issued" content="2012-12-17" datatype="xsd:date"/>
    <title property="dcterms:title">High Resolution Time</title>
    <link href="http://purl.org/ontology/bibo/Standard" rel="rdf:type"/>
  </head>
  <body about="http://localhost:8000/res/w3c/hr-time">
    <div about="http://localhost:8000/res/w3c/hr-time#PS1"
        typeof="bibo:DocumentPart"
        class="preamblesection"
        property="dcterms:title"
        content="Abstract">
      <p>Lorem ipsum dolor sit amet</p>
      <p><a href="http://localhost:8000/res/test/something-else">external</a></p>
      <p><a href="http://localhost:8000/dataset/test">dataset</a></p>
      <p><a href="http://localhost:8000/dataset/test?title=a">parametrized</a></p>
      <p><a href="http://localhost:8000/">root</a></p>
    </div>
    <div about="http://localhost:8000/res/w3c/hr-time#PS2"
        typeof="bibo:DocumentPart"
        class="preamblesection"
        property="dcterms:title"
        content="Status of this document">
      <p>Consectetur adipiscing elit.</p>
      <p>Mauris elit purus, blandit quis ante non</p>
    </div>
    <div about="http://localhost:8000/res/w3c/hr-time#S1"
        typeof="bibo:DocumentPart"
        class="section"
        property="dcterms:title"
        content="Introduction">
      <span property="bibo:chapter" content="1" xml:lang=""/>
      <p>Molestie aliquam nibh.</p>
      <div class="example">
	Vestibulum dapibus mollis massa, sed pulvinar eros gravida sit amet.
      </div>
    </div>
    <div about="http://localhost:8000/res/w3c/hr-time#S4"
        typeof="bibo:DocumentPart"
        class="section"
        property="dcterms:title" 
        content="High Resolution Time">
      <span property="bibo:chapter" content="4" xml:lang=""/>
      <div about="http://localhost:8000/res/w3c/hr-time#S4.1"
        typeof="bibo:DocumentPart"
        class="subsection" 
        property="dcterms:title"
        content="Introduction">
        <span property="bibo:chapter" content="4.1" xml:lang=""/>
        <p>Nullam semper orci justo</p>
        <div about="http://localhost:8000/res/w3c/hr-time#S4.1.1"
          typeof="bibo:DocumentPart"
          class="subsubsection" 
          property="dcterms:title"
          content="Background">
          <span property="bibo:chapter" content="4.1.1" xml:lang=""/>
          <p>Sed tempor, ipsum vel iaculis gravida</p>
        </div>
      </div>
      <div about="http://localhost:8000/res/w3c/hr-time#S4.2"
        typeof="bibo:DocumentPart"
        class="subsection"
        property="dcterms:title"
        content="The DOMHighResTimeStamp Type">
        <span property="bibo:chapter" content="4.2" xml:lang=""/>
        <div class="note">
          <div class="noteHeader">Note</div>
          <p>Non malesuada nisl sagittis et.</p>
        </div>
      </div>
    </div>
  </body>
</html>
        """
        with self.repo.store.open_parsed("a", mode="w") as fp:
            fp.write(test)

        with patch('ferenda.documentrepository.TripleStore'):
            self.repo.generate("a")
        return etree.parse(self.repo.store.generated_path("a"))

    def test_rdfa_removal(self):
        tree = self._generate_complex()
        # assert that no typeof/class attributes from the XHTML has been trasnformed into HTML
        self.assertEqual([], tree.xpath(".//*[contains(text(), 'bibo:chapter')]"))
        self.assertEqual([], tree.xpath(".//*[contains(text(), 'noteHeaderNote')]"))
        self.assertEqual([], tree.findall(".//span"))

    def test_headers(self):
        tree = self._generate_complex()
        # assert that numbered headers use them and headers
        # without (preamblesections) have not. also that header levels
        # are correct
        h2s = tree.findall(".//div/section/h2")
        self.assertEqual(4, len(h2s))
        self.assertEqual("Abstract", h2s[0].text)
        self.assertEqual("Status of this document", h2s[1].text)
        self.assertEqual("1. Introduction", h2s[2].text)
        self.assertEqual("4. High Resolution Time", h2s[3].text)
        h3s = tree.findall(".//div/section/h3")
        self.assertEqual(2, len(h3s))
        self.assertEqual("4.1. Introduction", h3s[0].text)
        self.assertEqual("4.2. The DOMHighResTimeStamp Type", h3s[1].text)
        h4s = tree.findall(".//div/section/h4")
        self.assertEqual(1, len(h4s))
        self.assertEqual("4.1.1. Background", h4s[0].text)

    def test_toc(self):
        # assert that a toc has been created and that it looks ok (inc preamblesections)
        tree = self._generate_complex()
        toc = tree.find(".//nav[@id='toc']")
        h2lis = toc.findall("ul/li")
        self.assertEqual(4, len(h2lis))
        self.assertEqual("a", h2lis[0][0].tag)
        self.assertEqual("#PS1", h2lis[0][0].get('href'))
        self.assertEqual("Abstract", h2lis[0][0].text)
        self.assertEqual("#S4", h2lis[3][0].get('href'))
        self.assertEqual("4. High Resolution Time", h2lis[3][0].text)

        subul = h2lis[3][1]
        self.assertEqual("ul", subul.tag)
        self.assertEqual(2, len(subul))
        self.assertEqual("li", subul[0].tag)
        self.assertEqual("a", subul[0][0].tag)
        self.assertEqual("#S4.1", subul[0][0].get("href"))
        self.assertEqual("4.1. Introduction", subul[0][0].text)
        
        subsubul = subul[0][1]
        self.assertEqual("ul", subsubul.tag)
        self.assertEqual(1, len(subsubul))
        self.assertEqual("#S4.1.1", subsubul[0][0].get("href"))
        self.assertEqual("4.1.1. Background", subsubul[0][0].text)

    def test_flatten(self):
        # just make sure that the XSLT generation flattens out our
        # nested structure so that every URI-named section is enclosed
        # in a <div> just beneath the <article>
        tree = self._generate_complex()
        self.assertEqual(7, len(tree.findall(".//article/div/section")))
        
    def test_ids(self):
        # make sure every URI-named <section> has the correct page-internal id attribute
        tree = self._generate_complex()
        secs = tree.findall(".//article/div/section")
        self.assertEqual("PS1", secs[0].get('id'))
        self.assertEqual("PS2", secs[1].get('id'))
        self.assertEqual("S1", secs[2].get('id'))
        self.assertEqual("S4", secs[3].get('id'))
        self.assertEqual("S4.1", secs[4].get('id'))
        self.assertEqual("S4.1.1", secs[5].get('id'))
        self.assertEqual("S4.2", secs[6].get('id'))

    def test_custom_sparql(self):
        # test with a custom SPARQL CONSTRUCT query in the current
        # directory. construct_annotations should use that one
        queryfile = self.datadir + os.sep + "myquery.rq"
        shutil.copy2("ferenda/res/sparql/annotations.rq", queryfile)
        # should go OK, ie no boom
        tree = self._generate_complex(sparql=queryfile)
        os.unlink(self.repo.store.generated_path("a"))
        # but try it with a non-existing file and it should go boom
        with self.assertRaises(ValueError):
            tree = self._generate_complex(sparql="nonexistent.rq")
            
        
        
    def test_custom_xsl(self):
        # test with a custom xslt in the current
        # directory. setup_transform_templates should copy this over
        # all the stuff in res/xsl to a temp directory, then do stuff.
        xslfile = self.datadir + os.sep + "mystyle.xsl"
        with open(xslfile, "w") as fp:
            # note that mystyle.xsl must depend on the systemwide base.xsl
            fp.write("""<?xml version="1.0" encoding="utf-8"?>
<xsl:stylesheet version="1.0"
		xmlns:xhtml="http://www.w3.org/1999/xhtml"
		xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
		xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
		xmlns:dcterms="http://purl.org/dc/terms/"
		exclude-result-prefixes="xhtml rdf">

  <xsl:include href="base.xsl"/>

  <!-- Implementations of templates called by base.xsl -->
  <xsl:template name="headtitle"><xsl:value-of select="//xhtml:title"/> | <xsl:value-of select="$configuration/sitename"/></xsl:template>
  <xsl:template name="metarobots"/>
  <xsl:template name="linkalternate"/>
  <xsl:template name="headmetadata"/>
  <xsl:template name="bodyclass">generic</xsl:template>
  <xsl:template name="pagetitle">
    <h1><xsl:value-of select="../xhtml:head/xhtml:title"/></h1>
  </xsl:template>

  <xsl:template match="xhtml:body/xhtml:div">
     <p class="div">This is not a div</p>            
  </xsl:template>

  <!-- default template: translate everything from whatever namespace
       it's in (usually the XHTML1.1 NS) into the default namespace
       NOTE: It removes any attributes not accounted for otherwise
       -->
  <xsl:template match="*">
    <xsl:element name="{local-name(.)}"><xsl:apply-templates select="node()"/></xsl:element>
  </xsl:template>

  <!-- toc handling (do nothing) -->
  <xsl:template match="@*|node()" mode="toc"/>
  
</xsl:stylesheet>""")
        tree = self._generate_complex(xslfile)
        divs = tree.findall(".//p[@class='div']")
        self.assertEqual(4,len(divs))
        
    def test_staticsite_url(self):
        tree = self._generate_complex(staticsite=True)
        link = tree.xpath(".//a[text()='external']")[0]
        self.assertEqual("something-else.html", link.get("href"))

        link = tree.xpath(".//a[text()='dataset']")[0]
        self.assertEqual("../toc/index.html", link.get("href"))

        link = tree.xpath(".//a[text()='parametrized']")[0]
        self.assertEqual("../toc/title/a.html", link.get("href"))

        link = tree.xpath(".//a[text()='root']")[0]
        self.assertEqual("../../index.html", link.get("href"))

    def test_dependency_mgmt(self):
        with self.repo.store.open_dependencies("a", "w") as fp:
            fp.write("""data/base/parsed/other.xhtml
data/base/parsed/foo.xhtml
""")
        # even though no dependency file actually existed, they should
        # have been loaded up in dependencies
        tree = self._generate_complex()

        # but this time the generated file should be newer than all
        # dependencies, trigging a skip.
        tree = self._generate_complex()

        # FIXME: we don't actually verify the that dependencies are
        # read or skipping is performed.

class Faceting(RepoTester):

    def test_query(self):
        # NOTE: this is also tested by a doctest
        want = """PREFIX dcterms: <http://purl.org/dc/terms/>
PREFIX foaf: <http://xmlns.com/foaf/0.1/>
PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>

SELECT DISTINCT ?uri ?rdf_type ?dcterms_title ?dcterms_publisher ?dcterms_identifier ?dcterms_issued
FROM <http://example.org/ctx/base>
WHERE {
    ?uri rdf:type foaf:Document .
    OPTIONAL { ?uri rdf:type ?rdf_type . }
    OPTIONAL { ?uri dcterms:title ?dcterms_title . }
    OPTIONAL { ?uri dcterms:publisher ?dcterms_publisher . }
    OPTIONAL { ?uri dcterms:identifier ?dcterms_identifier . }
    OPTIONAL { ?uri dcterms:issued ?dcterms_issued . }

}"""
        self.assertEqual(want,
                         self.repo.facet_query("http://example.org/ctx/base"))

    def test_facets(self):
        # tests that all expected facets are created and have the
        # expected properties
        facets = self.repo.facets()
        self.assertEqual(facets[0].rdftype, rdflib.RDF.type)
        # and more ...


    def test_year(self):
        self.assertEqual('2014',
                         Facet.year({'dcterms_issued': '2014-06-05T12:00:00'}))
        self.assertEqual('2014',
                         Facet.year({'dcterms_issued': '2014-06-05'}))
        self.assertEqual('2014',
                         Facet.year({'dcterms_issued': '2014-06'}))
        with self.assertRaises(Exception):
            Facet.year({'dcterms_issued': 'This is clearly an invalid date'})
        with self.assertRaises(Exception):
            Facet.year({'dcterms_issued': '2014-14-99'})
        
class Storage(RepoTester):


    def test_list_basefiles_file(self):
        files = ["base/downloaded/123/a.html",
                 "base/downloaded/123/b.html",
                 "base/downloaded/124/a.html",
                 "base/downloaded/124/b.html"]
        basefiles = ["124/b", "124/a", "123/b", "123/a"]
        for f in files:
            util.writefile(self.p(f),"Nonempty")
        self.assertEqual(list(self.repo.store.list_basefiles_for("parse")),
                         basefiles)

    def test_list_basefiles_dir(self):
        files = ["base/downloaded/123/a/index.html",
                 "base/downloaded/123/b/index.html",
                 "base/downloaded/124/a/index.html",
                 "base/downloaded/124/b/index.html"]
        basefiles = ["124/b", "124/a", "123/b", "123/a"]

        self.repo.storage_policy = "dir"
        self.repo.store.storage_policy = "dir"
        
        for f in files:
            util.writefile(self.p(f),"nonempty")
        self.assertEqual(list(self.repo.store.list_basefiles_for("parse")),
                         basefiles)


class Archive(RepoTester):
    url_location = None
    
    def test_archive(self):
        # create an existing thing
        util.writefile(self.repo.store.downloaded_path("123/a"),
                       "This is the original document, downloaded")
        util.writefile(self.repo.store.parsed_path("123/a"),
                       "This is the original document, parsed")
        util.writefile(self.repo.store.distilled_path("123/a"),
                       "This is the original document, distilled")
        util.writefile(self.repo.store.generated_path("123/a"),
                       "This is the original document, generated")
        # archive it
        version = self.repo.get_archive_version("123/a")
        self.assertEqual(version, "1") # what algorithm do the default use? len(self.archived_versions)?

        self.repo.store.archive("123/a",version)

        eq = self.assertEqual
        # make sure archived files ended up in the right places
        eq(util.readfile(self.repo.store.downloaded_path("123/a", version="1")),
                         "This is the original document, downloaded")
        eq(util.readfile(self.repo.store.parsed_path("123/a", version="1")),
                         "This is the original document, parsed")
        eq(util.readfile(self.repo.store.distilled_path("123/a", version="1")),
                         "This is the original document, distilled")
        eq(util.readfile(self.repo.store.generated_path("123/a", version="1")),
                         "This is the original document, generated")
        # and that no files exists in the current directories
        self.assertFalse(os.path.exists(self.repo.store.downloaded_path("123/a")))
        self.assertFalse(os.path.exists(self.repo.store.parsed_path("123/a")))
        self.assertFalse(os.path.exists(self.repo.store.distilled_path("123/a")))
        self.assertFalse(os.path.exists(self.repo.store.generated_path("123/a")))

        # Then do it again (with the same version id) and verify that
        # we can't archive twice to the same id
        with self.assertRaises(ArchivingError):
            util.writefile(self.repo.store.downloaded_path("123/a"),
                           "This is the original document, downloaded")
            util.writefile(self.repo.store.parsed_path("123/a"),
                           "This is the original document, parsed")
            util.writefile(self.repo.store.distilled_path("123/a"),
                           "This is the original document, distilled")
            util.writefile(self.repo.store.generated_path("123/a"),
                           "This is the original document, generated")
            self.repo.store.archive("123/a",version)
  


    def test_archive_dir(self):
        self.repo.store.storage_policy = "dir"
        self.test_archive()

    def test_download_and_archive(self):
        # print("test_download_and_archive: cwd", os.getcwd())
        def my_get(url,**kwargs):
            res = Mock()
            with open(self.url_location,"rb") as fp:
                res.content = fp.read()
            res.headers = collections.defaultdict(lambda:None)
            res.headers['X-These-Headers-Are'] = 'Faked'
            res.status_code = 200
            return res

        with patch('requests.get',side_effect = my_get) as mock_get:
            self.url_location = "test/files/base/downloaded/123/a-version1.htm"
            self.assertTrue(self.repo.download_single("123/a")) 
            self.url_location = "test/files/base/downloaded/123/a-version2.htm"
            self.assertTrue(self.repo.download_single("123/a"))
        eq = self.assertEqual
        eq(util.readfile(self.p("base/downloaded/123/a.html")),
           util.readfile("test/files/base/downloaded/123/a-version2.htm"))
        eq(util.readfile(self.p("base/archive/downloaded/123/a/1.html")),
           util.readfile("test/files/base/downloaded/123/a-version1.htm"))


    def test_list_versions_complex(self):
        util.writefile(self.repo.store.downloaded_path("123/a"),
                       "This is the first version")
        util.writefile(self.repo.store.parsed_path("123/a"),
                       "This is the first version (parsed)")
        util.writefile(self.repo.store.generated_path("123/a"),
                       "This is the first version (generated)")
        version = self.repo.get_archive_version("123/a")
        self.repo.store.archive("123/a",version)
        self.assertEqual(version, "1") 
        util.writefile(self.repo.store.downloaded_path("123/a"),
                       "This is the second version")
        util.writefile(self.repo.store.parsed_path("123/a"),
                       "This is the second version (parsed)")
        version = self.repo.get_archive_version("123/a")
        self.repo.store.archive("123/a",version)
        self.assertEqual(version, "2")
        util.writefile(self.repo.store.downloaded_path("123/a"),
                       "This is the third version")
        version = self.repo.get_archive_version("123/a")
        self.repo.store.archive("123/a",version)
        self.assertEqual(version, "3")
        util.writefile(self.repo.store.generated_path("123/a"),
                       "This is the fourth version (generated ONLY)")
        version = self.repo.get_archive_version("123/a")
        self.repo.store.archive("123/a",version)
        self.assertEqual(version, "4")
        self.assertEqual(sorted(os.listdir(self.p("base/archive/downloaded/123/a/"))),
                         ['1.html', '2.html', '3.html'])
        self.assertEqual(sorted(os.listdir(self.p("base/archive/parsed/123/a/"))),
                         ['1.xhtml', '2.xhtml'])
        self.assertEqual(sorted(os.listdir(self.p("/base/archive/generated/123/a/"))),
                         ['1.html', '4.html'])
        self.assertEqual(list(self.repo.store.list_versions("123/a")),
                         ['1','2','3', '4'])

        
        util.writefile(self.repo.store.downloaded_path("123"),
                       "This is the first version")

        version = self.repo.get_archive_version("123")
        self.repo.store.archive("123", version)
        self.assertEqual(version, "1")
        self.assertEqual(list(self.repo.store.list_versions("123")),
                         ['1'])
        self.assertEqual(list(self.repo.store.list_versions("123/a")),
                         ['1','2','3', '4'])

class Patch(RepoTester):
    sourcedoc = """<body>
  <h1>Basic document</h1>
  <p>
    This is some unchanged text.
    1: And some more again
    2: And some more again
    3: And some more again
    4: And some more again
    (to make sure we use two separate hunks)
    This is text that will be changed.
  </p>
  </body>
"""
    targetdoc = """<body>
  <h1>Patched document</h1>
  <p>
    This is some unchanged text.
    1: And some more again
    2: And some more again
    3: And some more again
    4: And some more again
    (to make sure we use two separate hunks)
    This is text that has changed.
  </p>
  </body>
"""

    # contains non-ascii characters, but only those that fit in a
    # non-utf8-encoding (latin-1 to be precise)
    targetdoc2 = """<body>
  <h1>Räksmörgås</h1>
  <p>
    This is some unchanged text.
    1: And some more again
    2: And some more again
    3: And some more again
    4: And some more again
    (to make sure we use two separate hunks)
    This is text thåt has chänged
  </p>
  </body>
"""
    
    def setUp(self):
        super(Patch, self).setUp()
        self.repo.config.patchdir = self.datadir
        self.patchstore = self.repo.documentstore_class(self.repo.config.patchdir + os.sep + self.repo.alias)

    def test_successful_patch(self):
        # Note that this patch's "fromfile" and "tofile" fields
        # doesn't match any actual file (and that there really isn't
        # any file stored on disk)
        patchpath = self.patchstore.path("123/a", "patches", ".patch")
        util.ensure_dir(patchpath)
        with open(patchpath, "w") as fp:
            fp.write("""--- basic.txt	2013-06-13 09:16:37.000000000 +0200
+++ changed.txt	2013-06-13 09:16:39.000000000 +0200
@@ -1,5 +1,5 @@ Editorial edit
 <body>
-  <h1>Basic document</h1>
+  <h1>Patched document</h1>
   <p>
     This is some unchanged text.
     1: And some more again
@@ -7,6 +7,6 @@
     3: And some more again
     4: And some more again
     (to make sure we use two separate hunks)
-    This is text that will be changed.
+    This is text that has changed.
   </p>
   </body>
""")
        result, desc = self.repo.patch_if_needed("123/a", self.sourcedoc)
        self.assertEqual("Editorial edit", desc)
        self.assertEqual(self.targetdoc, result)

    def test_successful_patch_with_desc(self):
        patchpath = self.patchstore.path("123/a", "patches", ".patch")
        util.ensure_dir(patchpath)
        with open(patchpath, "w") as fp:
            fp.write("""--- basic.txt	2013-06-13 09:16:37.000000000 +0200
+++ changed.txt	2013-06-13 09:16:39.000000000 +0200
@@ -1,5 +1,5 @@
 <body>
-  <h1>Basic document</h1>
+  <h1>Patched document</h1>
   <p>
     This is some unchanged text.
     1: And some more again
""")
        descpath = self.patchstore.path("123/a", "patches", ".desc")
        patchdesc = """This is a longer patch description.

It can span several lines."""
        with open(descpath, "wb") as fp:
            fp.write(patchdesc.encode())           

        result, desc = self.repo.patch_if_needed("123/a", self.sourcedoc)
        self.assertEqual(patchdesc, desc)

        # and again, now w/o any description
        os.unlink(descpath)
        result, desc = self.repo.patch_if_needed("123/a", self.sourcedoc)
        self.assertEqual("(No patch description available)", desc)
        
    def test_failed_patch(self):
        with self.patchstore.open("123/a", "patches", ".patch", "w") as fp:
            fp.write("""--- basic.txt	2013-06-13 09:16:37.000000000 +0200
+++ changed.txt	2013-06-13 09:16:39.000000000 +0200
@@ -1,5 +1,5 @@ This patch assumes that sourcedoc looks different
 <body>
-  <h1>Unpatched document</h1>
+  <h1>Patched document</h1>
   <p>
     This is some unchanged text.
     1: And some more again
@@ -7,6 +7,6 @@
     3: And some more again
     4: And some more again
     (to make sure we use two separate hunks)
-    This is text that will be changed.
+    This is text that has changed.
   </p>
   </body>
""")
        with self.assertRaises(PatchError):
            result, desc = self.repo.patch_if_needed("123/a", self.sourcedoc)

    def test_invalid_patch(self):
        with self.patchstore.open("123/a", "patches", ".patch", "w") as fp:
            fp.write("This is not a valid patch file")
        with self.assertRaises(PatchError):
            result, desc = self.repo.patch_if_needed("123/a", self.sourcedoc)

    def test_no_patch(self):
        result, desc = self.repo.patch_if_needed("123/a", self.sourcedoc)
        self.assertEqual(None, desc)
        self.assertEqual(self.sourcedoc, result)

    def test_unicode_patch(self):
        patchpath = self.patchstore.path("123/a", "patches", ".patch")
        util.ensure_dir(patchpath)
        with codecs.open(patchpath, "w", encoding="utf-8") as fp:
            fp.write("""--- basic.txt	2013-06-13 09:16:37.000000000 +0200
+++ changed.txt	2013-06-13 09:16:39.000000000 +0200
@@ -1,5 +1,5 @@ Editöriål edit
 <body>
-  <h1>Basic document</h1>
+  <h1>Räksmörgås</h1>
   <p>
     This is some unchanged text.
     1: And some more again
@@ -7,6 +7,6 @@
     3: And some more again
     4: And some more again
     (to make sure we use two separate hunks)
-    This is text that will be changed.
+    This is text thåt has chänged
   </p>
   </body>
""")
        result, desc = self.repo.patch_if_needed("123/a", self.sourcedoc)
        self.assertEqual("Editöriål edit", desc)
        self.assertEqual(self.targetdoc2, result)

    def test_encoded_patch(self):
        # Note that this patch's "fromfile" and "tofile" fields
        # doesn't match any actual file (and that there really isn't
        # any file stored on disk)
        patchpath = self.patchstore.path("123/a", "patches", ".patch")
        util.ensure_dir(patchpath)
        with codecs.open(patchpath, "w", encoding="latin-1") as fp:
            fp.write("""--- basic.txt	2013-06-13 09:16:37.000000000 +0200
+++ changed.txt	2013-06-13 09:16:39.000000000 +0200
@@ -1,5 +1,5 @@ Editöriål edit
 <body>
-  <h1>Basic document</h1>
+  <h1>Räksmörgås</h1>
   <p>
     This is some unchanged text.
     1: And some more again
@@ -7,6 +7,6 @@
     3: And some more again
     4: And some more again
     (to make sure we use two separate hunks)
-    This is text that will be changed.
+    This is text thåt has chänged
   </p>
   </body>
""")
        self.repo.source_encoding = "latin-1"
        result, desc = self.repo.patch_if_needed("123/a", self.sourcedoc)
        self.assertEqual("Editöriål edit", desc)
        self.assertEqual(self.targetdoc2, result)
        


# Add doctests in the module
from ferenda import documentrepository
from ferenda.testutil import Py23DocChecker
def load_tests(loader,tests,ignore):
    tests.addTests(doctest.DocTestSuite(documentrepository, checker=Py23DocChecker()))
    return tests
