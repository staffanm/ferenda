#!/usr/bin/env python
# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import sys, os
if sys.version_info < (2,7,0):
    import unittest2 as unittest
else:
    import unittest
if os.getcwd() not in sys.path: sys.path.insert(0,os.getcwd())

from ferenda.manager import setup_logger; setup_logger('CRITICAL')

from datetime import datetime,timedelta
from operator import attrgetter
import codecs
import collections
import shutil
import tempfile
import time
import calendar

import lxml.etree as etree
from lxml.etree import XSLT
from lxml.builder import ElementMaker
import rdflib

# import six
try:
    # assume we're on py3.3 and fall back if not
    from unittest.mock import Mock, MagicMock, patch, call
except ImportError:
    from mock import Mock, patch, call
# from requests.exceptions import HTTPError
from bs4 import BeautifulSoup
import doctest

from ferenda import DocumentEntry, TocPageset, TocPage, \
    TocCriteria, Describer, LayeredConfig, TripleStore, FulltextIndex
from ferenda.errors import *

# The main system under test (SUT)
from ferenda import DocumentRepository
from ferenda.testutil import RepoTester


# various utility functions which occasionally needs patching out
from ferenda import util
from ferenda.elements import serialize, Link

class Repo(RepoTester):

    # TODO: Many parts of this class could be divided into subclasses
    # (like Generate, Toc, News, Storage and Archive already has)

    # class Repo(RepoTester)
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
        d = DocumentRepository(loglevel='CRITICAL',datadir=self.datadir)

        d.start_url = "http://localhost/fake/url"
        d.download_single = Mock()
        d.download_single.return_value = True
        d.log = Mock()
        
        # test1: run download, make sure download_single is hit the
        # right amount of times, make sure d.log.error is called once,
        # and ensure lastdownload is set
        mockresponse = Mock()
        with open("%s/files/base/downloaded/index.htm" % os.path.dirname(__file__)) as fp:
            mockresponse.text = fp.read()
        with patch('requests.get',return_value=mockresponse):
            self.assertTrue(d.download())
        
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

        def my_get(url,headers):
            # observes the scoped variables "last_modified" (should
            # contain a formatted date string according to HTTP rules)
            # and "etag" (opaque string).
            resp = Mock()
            resp.status_code=200
            if "If-modified-since" in headers:
                if not expect_if_modified_since:
                    resp.status_code = 400
                    return resp
                if (util.parse_rfc822_date(headers["If-modified-since"]) > 
                    util.parse_rfc822_date(last_modified)):
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
                with open(url_location,"rb") as fp:
                    content = fp.read()
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
        
        # test4: file and etag exists, we use if-none-match, we recieve a 304
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
        expect_if_modified_since = False
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


    def test_remote_url(self):
        d = DocumentRepository()
        d.config = LayeredConfig(defaults=d.get_default_options(),inifile="ferenda.ini",cascade=True)
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
        d.config = LayeredConfig(defaults=d.get_default_options(),inifile="ferenda.ini",cascade=True)
        path = d.store.downloaded_path("123/a")
        # print("test_parse: d.store.downloaded_path('123/a') is %s" % path)
        util.ensure_dir(path)
        shutil.copy2("test/files/base/downloaded/123/a-version1.htm",path)
        ret = d.parse("123/a")

        g = rdflib.Graph()
        uri = d.canonical_uri("123/a")
        desc = Describer(g,uri)
        g.parse(d.store.distilled_path("123/a"))
        
        self.assertEqual(len(g),3)
        self.assertEqual(desc.getvalue(d.ns['dct'].identifier), "123/a")
        self.assertEqual(len(desc.getvalues(d.ns['dct'].title)),0)

        t = etree.parse(d.store.parsed_path("123/a"))
        # util.indent_et(t.getroot())

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
        self.assertEqual(desc.getvalue(d.ns['dct'].identifier), "123/a")
        self.assertEqual(desc.getvalue(d.ns['dct'].title), "A document")

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
        parser = "lxml" if sys.version_info < (3,3) else "html.parser"
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
        soup = BeautifulSoup(testdoc,parser)
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

    # class RenderXHTML(RepoTester) # maybe
    def _test_render_xhtml(self, body, want):
        doc = self.repo.make_document('basefile')
        doc.body = body
        outfile = self.datadir + "/test.xhtml"
        self.repo.render_xhtml(doc, outfile)
        self.assertEqualXML(want, util.readfile(outfile, "rb"))
        
    def test_render_xhtml_simple(self):
        # Test 1: Simple document using our own element objects
        from ferenda import elements as el
        body = el.Body([el.Heading(['Toplevel heading'], level=1),
                        el.Paragraph(['Introductory preamble']),
                        el.Section([el.Paragraph(['Some text']),
                                    el.Subsection([el.Paragraph(['More text'])],
                                                  ordinal='1.1',
                                                  title="First subsection")],
                                   ordinal='1', title='First section'),
                        el.Section([el.Paragraph(['Even more text'])],
                                   ordinal='2', title='Second section')])
        want = """<html xmlns="http://www.w3.org/1999/xhtml"
                        xmlns:bibo="http://purl.org/ontology/bibo/"
                        xmlns:dct="http://purl.org/dc/terms/">
  <head about="http://localhost:8000/res/base/basefile"/>
  <body about="http://localhost:8000/res/base/basefile">
    <h1>Toplevel heading</h1>
    <p>Introductory preamble</p>
    <div content="First section"
         about="http://localhost:8000/res/base/basefile#S1"
         property="dct:title"
         typeof="bibo:DocumentPart"
         class="section">
      <span content="1" about="http://localhost:8000/res/base/basefile#S1"
            property="bibo:chapter"/>
      <p>Some text</p>
      <div content="First subsection"
           about="http://localhost:8000/res/base/basefile#S1.1"
           property="dct:title"
           typeof="bibo:DocumentPart"
           class="subsection">
        <span content="1.1" about="http://localhost:8000/res/base/basefile#S1.1"
              property="bibo:chapter"/>
        <p>More text</p>
      </div>
    </div>
    <div content="Second section"
         about="http://localhost:8000/res/base/basefile#S2"
         property="dct:title"
         typeof="bibo:DocumentPart"
         class="section">
      <span content="2" about="http://localhost:8000/res/base/basefile#S2"
            property="bibo:chapter"/>
      <p>Even more text</p>
    </div>
  </body>
</html>"""
        self._test_render_xhtml(body, want)

    def test_render_xhtml_html(self):
        # test 2: use element.html elements only, to make a similar
        # document (although without metadata about
        # sections/subsection and classses). Uses some HTML5 elements
        # that are converted to divs when rendering as XHTML 1.1
        from ferenda.elements import html
        body = html.Body([html.H1(['Toplevel heading']),
                          html.Summary(['Introductory preamble']),
                          html.Section([html.H2(['First section']),
                                        html.P(['Some text']),
                                        html.Section([
                                            html.H3(['First subsection']),
                                            html.P(['More text'])])]),
                          html.Section([html.H2(['Second section']),
                                        html.P(['Even more text'])])])
        want = """<html xmlns="http://www.w3.org/1999/xhtml"
                        xmlns:bibo="http://purl.org/ontology/bibo/"
                        xmlns:dct="http://purl.org/dc/terms/">
  <head about="http://localhost:8000/res/base/basefile"/>
  <body about="http://localhost:8000/res/base/basefile">
    <h1>Toplevel heading</h1>
    <div class="summary">Introductory preamble</div>
    <div class="section">
      <h2>First section</h2>
      <p>Some text</p>
      <div class="section">
        <h3>First subsection</h3>
        <p>More text</p>
      </div>
    </div>
    <div class="section">
      <h2>Second section</h2>
      <p>Even more text</p>
    </div>
  </body>
</html>
"""
        self._test_render_xhtml(body, want)

    def test_render_xhtml_meta(self):
        from ferenda import elements as el
        from ferenda.elements import html
        # test 3: use a mix of our own elements and html elements,
        # with meta + uri attached to some nodes
        g1 = rdflib.Graph().parse(format='n3', data="""
@prefix bibo: <http://purl.org/ontology/bibo/> .
@prefix dct: <http://purl.org/dc/terms/> .

<http://localhost:8000/res/base/basefile#S1> a bibo:DocumentPart;
        dct:title "First section";
        bibo:chapter "1" .
        """)
        g2 = rdflib.Graph().parse(format='n3', data="""
@prefix bibo: <http://purl.org/ontology/bibo/> .
@prefix dct: <http://purl.org/dc/terms/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

<http://localhost:8000/res/base/basefile#S2> a bibo:DocumentPart;
        dct:title "Second section";
        bibo:chapter "2";
        dct:creator "Fred Bloggs"@en-GB;
        dct:issued "2013-05-10"^^xsd:date;
        owl:sameAs <http://example.org/s2> .
        """)
        
        body = el.Body([el.Heading(['Toplevel heading'], level=1),
                        html.P(['Introductory preamble']),
                        html.Div([html.P(['Some text']),
                                  el.Subsection([el.Paragraph(['More text'])],
                                                ordinal='1.1',
                                                title="First subsection")],
                                 uri = 'http://localhost:8000/res/base/basefile#S1',
                                 meta = g1),
                        el.Section([el.Paragraph(['Even more text'])],
                                   uri = 'http://localhost:8000/res/base/basefile#S2',
                                   meta = g2)])
        want  = """<html xmlns="http://www.w3.org/1999/xhtml"
                        xmlns:bibo="http://purl.org/ontology/bibo/"
                        xmlns:owl="http://www.w3.org/2002/07/owl#"
                        xmlns:dct="http://purl.org/dc/terms/">
  <head about="http://localhost:8000/res/base/basefile"/>
  <body about="http://localhost:8000/res/base/basefile">
    <h1>Toplevel heading</h1>
    <p>Introductory preamble</p>
    <div about="http://localhost:8000/res/base/basefile#S1"
         content="First section"
         property="dct:title"
         typeof="bibo:DocumentPart">
      <span content="1"
            property="bibo:chapter"
            xml:lang=""/>
      <p>Some text</p>
      <div about="http://localhost:8000/res/base/basefile#S1.1"
           content="First subsection"
           property="dct:title"
           typeof="bibo:DocumentPart"
           class="subsection">
        <span about="http://localhost:8000/res/base/basefile#S1.1"
              content="1.1"
              property="bibo:chapter"/>
        <p>More text</p>
      </div>
    </div>
    <div about="http://localhost:8000/res/base/basefile#S2"
        class="section"
        content="Second section"
        property="dct:title"
        typeof="bibo:DocumentPart">
      <span rel="owl:sameAs"
            href="http://example.org/s2"/>
      <span content="2"
            property="bibo:chapter"
            xml:lang=""/>
      <span content="2013-05-10"
            property="dct:issued"
            datatype="xsd:date"/>
      <span content="Fred Bloggs"
            property="dct:creator"
            xml:lang="en-GB"/>
      <p>Even more text</p>
    </div>
  </body>
</html>"""
        self._test_render_xhtml(body, want)

    def test_render_xhtml_custom(self):
        # test 4: define a CompoundElement subclass and override
        # as_xhtml
        from ferenda import elements as el
        class Preamble(el.CompoundElement):
            tagname = "div"
            classname = "preamble"
            
            def as_xhtml(self, uri):
                # a fairly complicated custom serialization that
                # inserts a new child node where before there was only
                # text, and so that text has to be moved from the
                # parent.text to child.tail
                E = ElementMaker(namespace="http://www.w3.org/1999/xhtml")
                element = super(Preamble, self).as_xhtml(uri)
                note  = E('span', {'class': 'preamble-note'},
                          self.note + ": ")
                note.tail = element.text
                element.text = None
                element.insert(0, note)
                return element
        body = el.Body([el.Heading(['Toplevel heading'], level=1),
                        Preamble(['Introductory preamble'],
                                 note='Read this first'),
                        el.Section([el.Paragraph(['Some text'])],
                                   ordinal='1', title='First section')])

        want = """<html xmlns="http://www.w3.org/1999/xhtml"
                        xmlns:bibo="http://purl.org/ontology/bibo/"
                        xmlns:dct="http://purl.org/dc/terms/">
  <head about="http://localhost:8000/res/base/basefile"/>
  <body about="http://localhost:8000/res/base/basefile">
    <h1>Toplevel heading</h1>
    <div class="preamble"><span class="preamble-note">Read this first: </span>Introductory preamble</div>
    <div content="First section"
         about="http://localhost:8000/res/base/basefile#S1"
         property="dct:title"
         typeof="bibo:DocumentPart"
         class="section">
      <span content="1" about="http://localhost:8000/res/base/basefile#S1"
            property="bibo:chapter"/>
      <p>Some text</p>
    </div>
  </body>
</html>
"""
        self._test_render_xhtml(body,want)

    def test_render_xhtml_malformed(self):
        # Test 5: Illegal indata (raw ESC character in string)
        from ferenda import elements as el
        body = el.Body(['Toplevel\x1b heading'])
        want = """<html xmlns="http://www.w3.org/1999/xhtml"
                        xmlns:bibo="http://purl.org/ontology/bibo/"
                        xmlns:dct="http://purl.org/dc/terms/">
  <head about="http://localhost:8000/res/base/basefile"/>
  <body about="http://localhost:8000/res/base/basefile">Toplevel heading</body>
</html>"""
        self._test_render_xhtml(body, want)


    # FIXME: Move this test to a new test case file (testElements.py or even testElementsHtml.py)
    # class Elements(RepoTester)
    def test_elements_from_soup(self):
        from ferenda.elements import html
        # see comment in documentrepository.soup_from_basefile
        parser = "lxml" if sys.version_info < (3,3) else "html.parser"
        soup = BeautifulSoup("""<body>
<h1>Sample</h1>
<div class="main">
<img src="xyz.png"/>
<p>Some <b>text</b></p>
<dl>
<dt>Term 1</dt>
<dd>Definition 1</dd>
</dl>
</div>
<div id="foot">
<hr/>
<a href="/">home</a> - <a href="/about">about</a>
</div>
</body>""",parser)
        body = html.elements_from_soup(soup.body)
        # print("Body: \n%s" % serialize(body))
        result = html.Body([html.H1(["Sample"]),
                            html.Div([html.Img(src="xyz.png"),
                                      html.P(["Some ",
                                              html.B(["text"])]),
                                      html.DL([html.DT(["Term 1"]),
                                               html.DD(["Definition 1"])])
                                ],**{"class":"main"}),
                            html.Div([html.HR(),
                                html.A(["home"],href="/"),
                                " - ",
                                html.A(["about"],href="/about")
                        ],id="foot")])
        self.maxDiff = 4096
        self.assertEqual(serialize(body),serialize(result))

    # Move to Generate?
    def test_transform_html(self):
        base = self.datadir+os.sep
        with open(base+"style.xslt","w") as fp:
            fp.write("""<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">
    <xsl:param name="value"/>
    <xsl:param name="file"/>
    <xsl:variable name="content" select="document($file)/root/*"/>
    <xsl:template match="/">
        <output>
            <paramvalue><xsl:value-of select="$value"/></paramvalue>
            <paramfile><xsl:copy-of select="$content"/></paramfile>
            <infile><xsl:value-of select="/doc/title"/></infile>
        </output>
    </xsl:template>
</xsl:stylesheet>
""")
        with open(base+"paramfile.xml","w") as fp:
            fp.write("""<root><node key='value'><subnode>textnode</subnode></node></root>""")

        with open(base+"infile.xml","w") as fp:
            fp.write("""<doc><title>Document title</title></doc>""")

        d = DocumentRepository()
        parampath = base+"paramfile.xml"
        d.transform_html(base+"style.xslt",
                         base+"infile.xml",
                         base+"outfile.xml",
                         {'value':XSLT.strparam('blahonga'),
                          'file' :XSLT.strparam(parampath.replace(os.sep,"/"))})

        self.assertEqualXML(util.readfile(base+"outfile.xml"),"""
        <output>
            <paramvalue>blahonga</paramvalue>
            <paramfile><node key='value'><subnode>textnode</subnode></node></paramfile>
            <infile>Document title</infile>
        </output>""")
        
    # class Relate(RepoTester)
    def test_relate_fulltext(self):
        d = DocumentRepository(datadir=self.datadir,
                               indexlocation=self.datadir+os.sep+"index") # FIXME: derive from datadir
        # prepare test document
        util.ensure_dir(d.store.parsed_path("123/a"))
        util.ensure_dir(d.store.distilled_path("123/a"))
        shutil.copy2("%s/files/base/parsed/123/a.xhtml" %
                     os.path.dirname(__file__),
                     d.store.parsed_path("123/a"))

        g = rdflib.Graph()
        with codecs.open("%s/files/base/distilled/123/a.ttl" %
                         os.path.dirname(__file__),encoding="utf-8") as fp:
            g.parse(fp,  format="turtle")
        with open(d.store.distilled_path("123/a"),"wb") as fp:
            g.serialize(fp,"pretty-xml")

        with patch.object(FulltextIndex,'update') as mock_method:
            d.relate_fulltext("123/a")
            calls = [call(title='Example', basefile='123/a',
                          uri='http://example.org/base/123/a', repo='base',
                          text='This is part of the main document, but not part of any sub-resource. This is the tail end of the main document ',
                          identifier='123(A)'),
                     call(title='Introduction', basefile='123/a',
                          uri='http://example.org/base/123/a#S1', repo='base',
                          text='This is part of document-part section 1 ',
                          identifier='123(A)\xb61'),  # \xb6 = Pilcrow 
                     call(title='Requirements Language', basefile='123/a',
                          uri='http://example.org/base/123/a#S1.1', repo='base',
                          text='This is the text in subsection 1.1 ',
                          identifier='123(A)\xb61.1'),
                     call(title='Definitions and Abbreviations', basefile='123/a',
                          uri='http://example.org/base/123/a#S2', repo='base',
                          text='This is the second main document part ',
                          identifier='123(A)\xb62')]
            mock_method.assert_has_calls(calls)

    test_rdf_xml = """<?xml version="1.0" encoding="utf-8"?>
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
            
    def test_relate_triples(self):
        # dump known triples as rdf/xml (want) to self.repo.store.distilled_path
        with self.repo.store.open_distilled('root', 'w') as fp:
            fp.write(self.test_rdf_xml)

        import ferenda.documentrepository
        assert ferenda.documentrepository
        # We mock out TripleStore to avoid creating an actual triplestore
        with patch('ferenda.documentrepository.TripleStore') as mock:
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
        with self.repo.store.open_distilled('root', 'w') as fp:
            fp.write(self.test_rdf_xml)
        
        # 3. relate_dependencies on repo A for basefile root
        otherrepo = OtherRepo(datadir=self.datadir)
        repos = [self.repo,otherrepo]
        self.repo.relate_dependencies("root", repos)
        # 4. Assert that
        #  4.1 self.repo.store.dependencies_path contains parsed_path('root')
        dependencyfile = self.repo.store.parsed_path('root') + "\n"
        self.assertEqual(util.readfile(self.repo.store.dependencies_path("res-a")),
                         dependencyfile)

        #  4.2 otherrepo.store.dependencies_path contains parsed_path('root')
        self.assertEqual(util.readfile(otherrepo.store.dependencies_path("res-b")),
                         dependencyfile)
        #  4.3 no other deps files exists in datadir
        self.assertEqual(2,
                         len(list(util.list_dirs(self.datadir, '.txt'))))

class Generate(RepoTester):
    repo_a = """
@prefix dct: <http://purl.org/dc/terms/> .
@prefix : <http://example.org/repo/a/> .

:1 a :FooDoc;
   dct:title "The title of Document A 1";
   dct:identifier "A1" .

:1part a :DocumentPart;
   dct:isPartOf :1;
   dct:identifier "A1(part)" .

:2 a :FooDoc;
   dct:title "The title of Document A 2";
   dct:identifier "A2";
   dct:references :1 . 

:2part1 a :DocumentPart;
   dct:isPartOf :2;
   dct:identifier "A2(part1)";
   dct:references :1 . 

:2part2 a :DocumentPart;
   dct:isPartOf :2;
   dct:identifier "A2(part2)";
   dct:references <http://example.org/repo/a/1part> .

:3 a :FooDoc;
   dct:title "The title of Document A 3";
   dct:identifier "A3" .
"""
    repo_b = """
@prefix dct: <http://purl.org/dc/terms/> .
@prefix a: <http://example.org/repo/a/> .
@prefix : <http://example.org/repo/b/> .

:1 a :BarDoc;
   dct:title "The title of Document B 1";
   dct:identifier "B1";
   dct:references a:1 . 

:1part a a:DocumentPart;
   dct:isPartOf :1;
   dct:identifier "B1(part)";
   dct:references a:1 . 

:2 a :BarDoc;
   dct:title "The title of Document B 2";
   dct:identifier "B2" .
"""
    # this is the graph we expect when querying for
    # http://example.org/repo/a/1
    annotations_a1 = """
@prefix dct: <http://purl.org/dc/terms/> .
@prefix : <http://example.org/repo/a/> .
@prefix b: <http://example.org/repo/b/> .

:1 a :FooDoc;
   dct:title "The title of Document A 1";
   dct:identifier "A1" ;
   dct:isReferencedBy :2,
                      :2part1,
                      b:1,
                      b:1part .

:1part a :DocumentPart;
    dct:isPartOf :1;
    dct:identifier "A1(part)";
    dct:isReferencedBy :2part2 .

:2 a :FooDoc;
    dct:references :1;
    dct:title "The title of Document A 2";
    dct:identifier "A2" .

:2part1 a :DocumentPart;
    dct:references :1;
    dct:isPartOf :2;
    dct:identifier "A2(part1)" .

:2part2 a :DocumentPart;
    dct:references :1part;
    dct:isPartOf :2;
    dct:identifier "A2(part2)" .

b:1 a b:BarDoc;
    dct:references :1;
    dct:title "The title of Document B 1";
    dct:identifier "B1" . 

b:1part a :DocumentPart;
    dct:isPartOf b:1;
    dct:references :1;
    dct:identifier "B1(part)" .
"""

    annotations_b1 = """
@prefix dct: <http://purl.org/dc/terms/> .
@prefix a: <http://example.org/repo/a/> .
@prefix : <http://example.org/repo/b/> .

:1 a :BarDoc;
   dct:isReferencedBy :1part;
   dct:title "The title of Document B 1";
   dct:identifier "B1";
   dct:references a:1 . 

:1part a a:DocumentPart;
   dct:isPartOf :1;
   dct:identifier "B1(part)";
   dct:references a:1 . 
"""

    class TestRepo(DocumentRepository):
        alias = "test"
        
        def canonical_uri(self,basefile):
            return "http://example.org/repo/a/%s" % basefile
            
    
    def setUp(self):
        self.datadir = tempfile.mkdtemp()
        self.storetype = None
        resources = self.datadir+os.sep+"rsrc"+os.sep+"resources.xml"
        util.ensure_dir(resources)
        shutil.copy2("%s/files/base/rsrc/resources.xml"%os.path.dirname(__file__),
                     resources)

    def tearDown(self):
        if self.storetype:
            store = TripleStore(storetype=self.repo.config.storetype,
                                location=self.repo.config.storelocation,
                                repository=self.repo.config.storerepository)
            store.clear()
            if self.repo.config.storetype == "SLEEPYCAT":
                store.graph.close()
        shutil.rmtree(self.datadir)
        
    def _load_store(self, repo):
        store = TripleStore(storetype=repo.config.storetype,
                            location=repo.config.storelocation,
                            repository=repo.config.storerepository)
        store.add_serialized(self.repo_a, format="turtle")
        store.add_serialized(self.repo_b, format="turtle")
        if repo.config.storetype == "SLEEPYCAT":
            store.graph.close()
        # return store
        
    def _test_construct_annotations(self, repo):
        want = rdflib.Graph()
        want.parse(data=self.annotations_a1,format="turtle")
        got = repo.construct_annotations("http://example.org/repo/a/1")
        self.assertEqualGraphs(want, got, exact=True)

    def _get_repo(self, storetype=None):
        params = {'storetype':storetype,
                  'datadir':self.datadir,
                  'storerepository':'ferenda'}
        self.storetype = None
        if storetype == 'SQLITE':
            params['storelocation'] = self.datadir+"/ferenda.sqlite"
        elif storetype == 'SLEEPYCAT':
            params['storelocation'] = self.datadir+"/ferenda.db"
        elif storetype == 'FUSEKI':
            params['storelocation'] = 'http://localhost:3030/'
            params['storerepository'] = 'ds'
        elif storetype == 'SESAME':
            params['storelocation'] = 'http://localhost:8080/openrdf-sesame'
        elif storetype == None:
            del params['storetype']
            del params['storerepository']
            params['storelocation'] = None
        else:
            self.fail("Storetype %s not valid" % storetype)
        return self.TestRepo(**params)
            
    def test_construct_annotations_sqlite(self):
        self.repo = self._get_repo('SQLITE')
        self._load_store(self.repo)
        self._test_construct_annotations(self.repo)

    @unittest.skipIf('SKIP_SLEEPYCAT_TESTS' in os.environ,
                     "Skipping Sleepycat tests")    
    def test_construct_annotations_sleepycat(self):
        self.repo = self._get_repo('SLEEPYCAT')
        self._load_store(self.repo)
        self._test_construct_annotations(self.repo)

    @unittest.skipIf('SKIP_FUSEKI_TESTS' in os.environ,
                     "Skipping Fuseki tests")    
    def test_construct_annotations_fuseki(self):
        self.repo = self._get_repo('FUSEKI')
        self._load_store(self.repo)
        self._test_construct_annotations(self.repo)

    @unittest.skipIf('SKIP_SESAME_TESTS' in os.environ,
                     "Skipping Sesame tests")    
    def test_construct_annotations_sesame(self):
        self.repo = self._get_repo('SESAME')
        self._load_store(self.repo)
        self._test_construct_annotations(self.repo)

    def test_graph_to_annotation_file(self):
        testgraph = rdflib.Graph()
        testgraph.parse(data=self.annotations_b1,format="turtle")
        testgraph.bind("a", rdflib.Namespace("http://example.org/repo/a/"))
        testgraph.bind("b", rdflib.Namespace("http://example.org/repo/b/"))
        testgraph.bind("dct", rdflib.Namespace("http://purl.org/dc/terms/"))
        self.repo = self._get_repo()
        annotations = self.repo.graph_to_annotation_file(testgraph)
        self.maxDiff = None
        want = """<graph xmlns:dct="http://purl.org/dc/terms/"
       xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
       xmlns:b="http://example.org/repo/b/"
       xmlns:a="http://example.org/repo/a/">
  <resource uri="http://example.org/repo/b/1">
    <a><b:BarDoc/></a>
    <dct:identifier>B1</dct:identifier>
    <dct:isReferencedBy ref="http://example.org/repo/b/1part"/>
    <dct:references ref="http://example.org/repo/a/1"/>
    <dct:title>The title of Document B 1</dct:title>
  </resource>
  <resource uri="http://example.org/repo/b/1part">
    <a><a:DocumentPart/></a>
    <dct:identifier>B1(part)</dct:identifier>
    <dct:isPartOf ref="http://example.org/repo/b/1"/>
    <dct:references ref="http://example.org/repo/a/1"/>
  </resource>
</graph>"""
        self.assertEqualXML(want,annotations)

    def _test_generated(self):
        with self.repo.store.open_parsed("1", "w") as fp:
            fp.write("""<?xml version='1.0' encoding='utf-8'?>
<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML+RDFa 1.0//EN" "http://www.w3.org/MarkUp/DTD/xhtml-rdfa-1.dtd">
<html xmlns:a="http://example.org/repo/a/" xmlns:b="http://example.org/repo/b/"  xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns:xsd="http://www.w3.org/2001/XMLSchema#" xmlns:dct="http://purl.org/dc/terms/" xmlns="http://www.w3.org/1999/xhtml">
  <head about="http://example.org/repo/a/1">
    <link href="http://example.org/repo/a/FooDoc" rel="rdf:type"/>
    <meta content="A1" property="dct:identifier"/>
    <title property="dct:title" xml:lang="">The title of Document A 1</title>
  </head>
  <body about="http://example.org/repo/a/1">
      <div><p>Main document text</p></div>
      <div content="A1(part)" about="http://example.org/repo/a/1part" property="dct:identfier" typeof="a:DocumentPart">
        <p>Document part text</p>
      </div>
  </body>
</html>""")
        self.assertEqual("http://example.org/repo/a/1",
                         self.repo.canonical_uri("1"))
        self.repo.generate("1")
        
        # print("-----------------ANNOTATIONS--------------")
        # with self.repo.store.open_annotation("1") as fp:
        #     print(fp.read())
        # print("-----------------GENERATED RESULT--------------")
        # with self.repo.store.open_generated("1") as fp:
        #     print(fp.read())
        
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

    @unittest.skipIf('SKIP_FUSEKI_TESTS' in os.environ,
                     "Skipping Fuseki tests")    
    def test_generate_fuseki(self):
        self.repo = self._get_repo('FUSEKI')
        self.store = self._load_store(self.repo)
        self._test_generated()

    @unittest.skipIf('SKIP_SESAME_TESTS' in os.environ,
                     "Skipping Sesame tests")    
    def test_generate_sesame(self):
        self.repo = self._get_repo('SESAME')
        self.store = self._load_store(self.repo)
        self._test_generated()

    @unittest.skipIf('SKIP_SLEEPYCAT_TESTS' in os.environ,
                     "Skipping Sleepycat tests")    
    def test_generate_sleepycat(self):
        self.repo = self._get_repo('SLEEPYCAT')
        self.store = self._load_store(self.repo)
        self._test_generated()

    def test_generate_sqlite(self):
        self.repo = self._get_repo('SQLITE')
        self.store = self._load_store(self.repo)
        self._test_generated()

    def _generate_complex(self):
        # Helper func for other tests -- this uses a single
        # semi-complex source doc, runs it through the generic.xsl
        # stylesheet, and then the tests using this helper confirm
        # various aspects of the transformed document
        self.repo = self._get_repo()
        test = """<?xml version='1.0' encoding='utf-8'?>
<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML+RDFa 1.0//EN" "http://www.w3.org/MarkUp/DTD/xhtml-rdfa-1.dtd">
<html xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns:bibo="http://purl.org/ontology/bibo/" xmlns:xsd="http://www.w3.org/2001/XMLSchema#" xmlns:dct="http://purl.org/dc/terms/" xmlns="http://www.w3.org/1999/xhtml" xml:lang="en">
  <head about="http://localhost:8000/res/w3c/hr-time">
    <meta property="dct:editor" content="Jatinder Mann " xml:lang=""/>
    <meta property="dct:identifier" content="hr-time" xml:lang=""/>
    <meta property="dct:issued" content="2012-12-17" datatype="xsd:date"/>
    <title property="dct:title">High Resolution Time</title>
    <link href="http://purl.org/ontology/bibo/Standard" rel="rdf:type"/>
  </head>
  <body about="http://localhost:8000/res/w3c/hr-time">
    <div about="http://localhost:8000/res/w3c/hr-time#PS1"
        typeof="bibo:DocumentPart"
        class="preamblesection"
        property="dct:title"
        content="Abstract">
      <p>Lorem ipsum dolor sit amet</p>
    </div>
    <div about="http://localhost:8000/res/w3c/hr-time#PS2"
        typeof="bibo:DocumentPart"
        class="preamblesection"
        property="dct:title"
        content="Status of this document">
      <p>Consectetur adipiscing elit.</p>
      <p>Mauris elit purus, blandit quis ante non</p>
    </div>
    <div about="http://localhost:8000/res/w3c/hr-time#S1"
        typeof="bibo:DocumentPart"
        class="section"
        property="dct:title"
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
        property="dct:title" 
        content="High Resolution Time">
      <span property="bibo:chapter" content="4" xml:lang=""/>
      <div about="http://localhost:8000/res/w3c/hr-time#S4.1"
        typeof="bibo:DocumentPart"
        class="subsection" 
        property="dct:title"
        content="Introduction">
        <span property="bibo:chapter" content="4.1" xml:lang=""/>
        <p>Nullam semper orci justo</p>
        <div about="http://localhost:8000/res/w3c/hr-time#S4.1.1"
          typeof="bibo:DocumentPart"
          class="subsubsection" 
          property="dct:title"
          content="Background">
          <span property="bibo:chapter" content="4.1.1" xml:lang=""/>
          <p>Sed tempor, ipsum vel iaculis gravida</p>
        </div>
      </div>
      <div about="http://localhost:8000/res/w3c/hr-time#S4.2"
        typeof="bibo:DocumentPart"
        class="subsection"
        property="dct:title"
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

        
class TOC(RepoTester):
    # General datasets being reused in tests
    books = """
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix dct: <http://purl.org/dc/terms/> .
@prefix bibo: <http://purl.org/ontology/bibo/> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
@prefix ex: <http://example.org/books/> .

# From http://en.wikipedia.org/wiki/List_of_best-selling_books

ex:A_Tale_of_Two_Cities a bibo:Book;
    dct:title "A Tale of Two Cities";
    dct:creator "Charles Dickens";
    dct:issued "1859-04-30"^^xsd:date;
    dct:publisher "Chapman & Hall" .

ex:The_Lord_of_the_Rings a bibo:Book;
    dct:title "The Lord of the Rings";
    dct:creator "J. R. R. Tolkien";
    dct:issued "1954-07-29"^^xsd:date;
    dct:publisher "George Allen & Unwin" .

ex:The_Little_Prince a bibo:Book;
    dct:title "The Little Prince";
    dct:creator "Antoine de Saint-Exup\xe9ry";
    dct:issued "1943-01-01"^^xsd:date;
    dct:publisher "Reynal & Hitchcock" .

ex:The_Hobbit a bibo:Book;
    dct:title "The Hobbit";
    dct:creator "J. R. R. Tolkien";
    dct:issued "1937-09-21"^^xsd:date;
    dct:publisher "George Allen & Unwin" .

ex:Dream_of_the_Red_Chamber a bibo:Book;
    dct:title "Dream of the Red Chamber";
    dct:creator "Cao Xueqin";
    dct:issued "1791-01-01"^^xsd:date;
    dct:publisher "Cheng Weiyuan & Gao E" .

ex:And_Then_There_Were_None a bibo:Book;
    dct:title "And Then There Were None";
    dct:creator "Agatha Christie";
    dct:issued "1939-11-06"^^xsd:date;
    dct:publisher "Collins Crime Club" .
"""

    articles = """
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix dct: <http://purl.org/dc/terms/> .
@prefix bibo: <http://purl.org/ontology/bibo/> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
@prefix ex: <http://example.org/articles/> .

# http://www.the-scientist.com/?articles.view/articleNo/9678/title/The-4-Most-Cited-Papers--Magic-In-These-Methods/

ex:pm14907713 a bibo:AcademicArticle;
    dct:title "Protein measurement with the Folin phenol reagent";
    dct:creator "Oliver H. Lowry",
                "Nira J. Rosenbrough",
                "A. Lewis Farr",
                "R.J. Randall";
    dct:issued "1951-11-01"^^xsd:date;
    dct:publisher "Journal of Biological Chemistry" .
    
ex:pm5432063 a bibo:AcademicArticle;
    dct:title "Cleavage of structural proteins during the assembly of the head of bacteriophage T4";
    dct:creator "Ulrich Karl Laemmli";
    dct:issued "1970-08-15"^^xsd:date;
    dct:publisher "Nature" .

ex:pm5806584 a bibo:AcademicArticle;
    dct:title "Reliability of molecular weight determinations by dodecyl sulfate-polyacrylamide gel electrophoresis";
    dct:creator "K. Weber",

    "M. Osborn";
    dct:issued "1969-08-25"^^xsd:date;
    dct:publisher "Journal of Biological Chemistry" .

ex:pm942051 a bibo:AcademicArticle;
    dct:title "A rapid and sensitive method for the quantitation of microgram quantities of protein utilizing the principle of protein dye-binding";
    dct:creator "Marion M. Bradford";
    dct:issued "1976-05-07"^^xsd:date;
    dct:publisher "Analytical Biochemistry" .
""" 

    results1 = [{'uri':'http://example.org/books/A_Tale_of_Two_Cities',
                 'title': 'A Tale of Two Cities',
                 'issued': '1859-04-30'},
                {'uri':'http://example.org/books/The_Lord_of_the_Rings',
                 'title': 'The Lord of the Rings',
                 'issued': '1954-07-29'},
                {'uri':'http://example.org/books/The_Little_Prince',
                 'title': 'The Little Prince',
                 'issued': '1943-01-01'},
                {'uri':'http://example.org/books/The_Hobbit',
                 'title': 'The Hobbit',
                 'issued': '1937-09-21'},
                {'uri':'http://example.org/books/Dream_of_the_Red_Chamber',
                 'title': 'Dream of the Red Chamber',
                 'issued': '1791-01-01'},
                {'uri':'http://example.org/books/And_Then_There_Were_None',
                 'title': 'And Then There Were None',
                 'issued': '1939-11-06'}]
    results2 = [{'uri':'http://example.org/articles/pm14907713',
                 'title': 'Protein measurement with the Folin phenol reagent',
                 'issued': '1951-11-01'},
                {'uri':'http://example.org/articles/pm5432063',
                 'title': 'Cleavage of structural proteins during the assembly of the head of bacteriophage T4',
                 'issued': '1970-08-15'},
                {'uri':'http://example.org/articles/pm5806584',
                 'title': 'Reliability of molecular weight determinations by dodecyl sulfate-polyacrylamide gel electrophoresis',
                 'issued': '1969-08-25'},
                {'uri':'http://example.org/articles/pm942051',
                 'title': 'A rapid and sensitive method for the quantitation of microgram quantities of protein utilizing the principle of protein dye-binding',
                 'issued': '1976-05-07'}]
    
    pagesets = [TocPageset('Sorted by title',[
                TocPage('a','Documents starting with "a"','title', 'a'),
                TocPage('d','Documents starting with "d"','title', 'd'),
                TocPage('h','Documents starting with "h"','title', 'h'),
                TocPage('l','Documents starting with "l"','title', 'l')
                ]),
                TocPageset('Sorted by publication year',[
                TocPage('1791','Documents published in 1791','issued', '1791'),
                TocPage('1859','Documents published in 1859','issued', '1859'),
                TocPage('1937','Documents published in 1937','issued', '1937'),
                TocPage('1939','Documents published in 1939','issued', '1939'),
                TocPage('1943','Documents published in 1943','issued', '1943'),
                TocPage('1954','Documents published in 1954','issued', '1954')
                ])]
    
    documentlists = {
        ('issued', '1791'): [[Link("Dream of the Red Chamber",uri='http://example.org/books/Dream_of_the_Red_Chamber')]],
        ('issued', '1859'): [[Link("A Tale of Two Cities",uri='http://example.org/books/A_Tale_of_Two_Cities')]],
        ('issued', '1937'): [[Link("The Hobbit",uri='http://example.org/books/The_Hobbit')]],
        ('issued', '1939'): [[Link("And Then There Were None",uri='http://example.org/books/And_Then_There_Were_None')]], 
        ('issued', '1943'): [[Link("The Little Prince",uri='http://example.org/books/The_Little_Prince')]],
        ('issued', '1954'): [[Link("The Lord of the Rings",uri='http://example.org/books/The_Lord_of_the_Rings')]],
        ('title', 'a'): [[Link("And Then There Were None",uri='http://example.org/books/And_Then_There_Were_None')],
                    [Link("A Tale of Two Cities",uri='http://example.org/books/A_Tale_of_Two_Cities')]],
        ('title', 'd'): [[Link("Dream of the Red Chamber",uri='http://example.org/books/Dream_of_the_Red_Chamber')]],
        ('title', 'h'): [[Link("The Hobbit",uri='http://example.org/books/The_Hobbit')]],
        ('title', 'l'): [[Link("The Little Prince",uri='http://example.org/books/The_Little_Prince')],
                    [Link("The Lord of the Rings",uri='http://example.org/books/The_Lord_of_the_Rings')]]
        }


    criteria = [TocCriteria(binding='title',
                            label='Sorted by title',
                            pagetitle='Documents starting with "%s"',
                            selector = lambda x: x['title'][4].lower() if x['title'].lower().startswith("the ") else x['title'][0].lower(),
                            key = lambda x: "".join((x['title'][4:] if x['title'].lower().startswith("the ") else x['title']).lower().split())),
                TocCriteria(binding='issued',
                            label='Sorted by publication year',
                            pagetitle='Documents published in %s',
                            selector=lambda x: x['issued'][:4],
                            key=lambda x: x['issued'][:4])]
    def setUp(self):
        super(TOC, self).setUp()
        resources = self.datadir+os.sep+"rsrc"+os.sep+"resources.xml"
        util.ensure_dir(resources)
        shutil.copy2("%s/files/base/rsrc/resources.xml"%os.path.dirname(__file__),
                     resources)
        
        # (set up a triple store) and fill it with appropriate data
        d = DocumentRepository()
        defaults = d.get_default_options()
        # FIXME: We really need to subclass at least the toc_select
        # test to handle the four different possible storetypes. For
        # now we go with the default type (SQLITE, guaranteed to
        # always work) but the non-rdflib backends use different code
        # paths.
        self.store = TripleStore(storetype=defaults['storetype'],
                                 location=self.datadir+os.sep+"test.sqlite",
                                 repository=defaults['storerepository'])
        self.store.clear()
        self.store.context = "http://example.org/ctx/base"
        self.store.add_serialized(self.books,format="turtle")
        self.store.context = "http://example.org/ctx/other"
        self.store.add_serialized(self.articles,format="turtle")


    def tearDown(self):
        # clear triplestore
        self.store.context = None
        self.store.clear()
        del self.store
        super(TOC, self).tearDown()
        

    def test_toc_select(self):
        d = DocumentRepository(datadir=self.datadir,
                               loglevel='CRITICAL',
                               storelocation=self.datadir+os.sep+"test.sqlite")
        # make sure only one named graph, not entire store, gets searched
        got = d.toc_select("http://example.org/ctx/base")
        self.assertEqual(len(got),6)
        want = self.results1
        for row in want:
            self.assertIn(row, got)

        got = d.toc_select("http://example.org/ctx/other")
        self.assertEqual(len(got),4)
        want2 = self.results2
        for row in want2:
            self.assertIn(row, got)
    
        got = d.toc_select()
        self.assertEqual(len(got),10)
        want3 = want+want2
        for row in want3:
            self.assertIn(row, got)
    # toc_query is tested by test_toc_select
            
    def test_toc_criteria(self):
        d = DocumentRepository(datadir=self.datadir,
                               loglevel='CRITICAL')
        dct = d.ns['dct']
        want = self.criteria
        got = d.toc_criteria([dct.title, dct.issued])
        
        self.assertEqual(len(want), len(got))
        self.assertEqual(want[0].binding, got[0].binding)
        self.assertEqual(want[0].label, got[0].label)
        self.assertEqual(want[0].pagetitle, got[0].pagetitle)
        testdict = {'title': 'The data'}
        self.assertEqual(want[0].selector(testdict), got[0].selector(testdict))
        self.assertEqual('d', got[0].selector(testdict))
        self.assertEqual(want[1].binding, got[1].binding)
        self.assertEqual(want[1].label, got[1].label)
        self.assertEqual(want[1].pagetitle, got[1].pagetitle)
        testdict = {'issued': '2009-01-01'}
        self.assertEqual(want[1].selector(testdict), got[1].selector(testdict))
        
    # toc_selector is tested by test_toc_criteria
    
    def test_toc_pagesets(self):
        d = DocumentRepository(datadir=self.datadir,
                               loglevel='CRITICAL')
        data = self.results1

        got = d.toc_pagesets(data, self.criteria)
        want = self.pagesets
        self.assertEqual(len(got), 2)
        self.assertEqual(got[0].label, want[0].label)
        self.assertEqual(got[0].pages[0], want[0].pages[0])
        self.assertEqual(got[0], want[0])
        self.assertEqual(got[1], want[1])

    def test_select_for_pages(self):
        d = DocumentRepository(datadir=self.datadir,
                               loglevel='CRITICAL')
        got = d.toc_select_for_pages(self.results1, self.pagesets, self.criteria)
        want = self.documentlists
        self.maxDiff = None
        self.assertEqual(got, want)

    def test_generate_page(self):
        d = DocumentRepository(datadir=self.datadir,
                               loglevel='CRITICAL')
        path = d.toc_generate_page('title','a', self.documentlists[('title','a')], self.pagesets)

        # 1. first, test intermediate XHTML file
        intermediate = path.replace(".html",".xhtml")
        self.assertTrue(os.path.exists(intermediate))
        #with open(intermediate) as fp:
        #    print(fp.read().decode('utf-8'))
        #print("=" * 60)
        t = etree.parse(intermediate)
        xhtmlns = "{http://www.w3.org/1999/xhtml}"

        # 1.1 Correct page title?
        self.assertEqual(t.findtext(".//"+xhtmlns+"title"),
                         'Documents starting with "a"')

        # 1.2 Correct navigation?
        # @id='nav' -> @role='navigation' ?
        navlinks = t.findall(".//"+xhtmlns+"ul[@role='navigation']//"+xhtmlns+"a")
        self.assertEqual(len(navlinks), 9) # 10 pages in total, but current page isn't linked
        self.assertEqual(navlinks[0].text, 'd')
        self.assertEqual(navlinks[0].get("href"), 'http://localhost:8000/dataset/base?title=d')
        self.assertEqual(navlinks[3].get("href"), 'http://localhost:8000/dataset/base?issued=1791')

        # 1.3 Correct document list?
        # @id='documentlist' => @role='main'
        docs = t.findall(".//"+xhtmlns+"ul[@role='main']/"+xhtmlns+"li/"+xhtmlns+"a")
        self.assertEqual(len(docs),2)
        # "And..." should go before "A Tale..."
        self.assertEqual(docs[0].text, 'And Then There Were None')
        self.assertEqual(docs[0].attrib['href'], 'http://example.org/books/And_Then_There_Were_None')

        # 2. secondly, test resulting HTML file
        self.assertTrue(os.path.exists(path))
        t = etree.parse(path)
        
        #with open(path) as fp:
        #    print(fp.read().decode('utf-8'))

        # Various other tests on a.html
        # 2.1 CSS links, relativized correctly?
        css = t.findall("head/link[@rel='stylesheet']")
        self.assertEqual(len(css),4) # normalize, main, ferenda, and fonts.googleapis.com
        
        if sys.version_info < (3, 2, 0): # renamed method in 3.2
            self.assertRegexpMatches(css[0].get('href'), '^../../../rsrc/css')
        else:
            self.assertRegex(css[0].get('href'), '^../../../rsrc/css')
        
        # 2.2 JS links, relativized correctly?
        js = t.findall("head/script")
        self.assertEqual(len(js),3) # jquery, modernizr and ferenda
        if sys.version_info < (3, 2, 0): # renamed method in 3.2
            self.assertRegexpMatches(js[0].get('src'), '^../../../rsrc/js')
        else:
            self.assertRegex(js[0].get('src'), '^../../../rsrc/js')
        # 2.3 <nav id="toc"> correct (c.f 1.2)
        navlinks = t.findall(".//nav[@id='toc']//li/a")
        self.assertEqual(len(navlinks),9)

        self.assertEqual(navlinks[0].get("href"), 'http://localhost:8000/dataset/base?title=d')
        self.assertEqual(navlinks[3].get("href"), 'http://localhost:8000/dataset/base?issued=1791')
        
        # 2.4 div[@class='main-container']/article (c.f 1.3)
        docs = t.findall(".//ul[@role='main']/li/a")
        self.assertEqual(len(docs),2)
        # "And..." should go before "A Tale..."
        self.assertEqual(docs[0].text, 'And Then There Were None')
        self.assertEqual(docs[0].attrib['href'], 'http://example.org/books/And_Then_There_Were_None')
        
        # 2.5 <h1 class="title"> correct?
        header = t.find(".//header/h1")
        self.assertEqual(header.text, 'testsite')
       
        # 2.6 div[@class='main-container']/h1 correct?
        header = t.find(".//div[@class='main-container']//h1")
        self.assertEqual(header.text, 'Documents starting with "a"')

    def test_generate_pages(self):
        d = DocumentRepository(datadir=self.datadir,
                               loglevel='CRITICAL')
        paths = d.toc_generate_pages(self.documentlists,self.pagesets)
        self.assertEqual(len(paths), 10)
        #print("=============%s====================" % paths[0])
        #with open(paths[0]) as fp:
        #    print(fp.read())
        for path in paths:
            self.assertTrue(os.path.exists(path))

    def test_generate_first_page(self):
        d = DocumentRepository(datadir=self.datadir,
                               loglevel='CRITICAL')
        path = d.toc_generate_first_page(self.documentlists,self.pagesets)
        self.assertEqual(path, self.p("base/toc/index.html"))
        self.assertTrue(os.path.exists(path))
        tree = etree.parse(path)
        # check content of path, particularly that css/js refs
        # and pageset links are correct. Also, that the selected
        # indexpage is indeed the first (eg. issued/1791) 
        self.assertEqual("http://localhost:8000/dataset/base?title=a",
                         tree.find(".//nav[@id='toc']").findall(".//a")[0].get("href"))
        self.assertEqual("../../rsrc/css/normalize.css",
                         tree.find(".//link").get("href"))
                         
        self.assertEqual("Documents published in 1791",
                         tree.find(".//article/h1").text)
                         

class News(RepoTester):
    def setUp(self):
        super(News, self).setUp()
        # create a bunch of DocumentEntry objects and save them
        basetime = datetime(2013,1,1,12,0)
        for basefile in range(25):
            v = {'id':self.repo.canonical_uri(basefile),
                 'title':"Doc #%s" % basefile}
            de = DocumentEntry()
            de.orig_created = basetime + timedelta(hours=basefile)
            de.orig_updated = basetime + timedelta(hours=basefile,minutes=10)
            de.orig_checked = basetime + timedelta(hours=basefile,minutes=20)
            de.published    = basetime + timedelta(hours=basefile,minutes=30)
            de.updated      = basetime + timedelta(hours=basefile,minutes=40)
            de.orig_url     = "http://source.example.org/doc/%s" % basefile
            de.save(self.repo.store.documententry_path(str(basefile)))
            g = rdflib.Graph()
            desc = Describer(g,self.repo.canonical_uri(basefile))
            dct = self.repo.ns['dct']
            desc.value(dct.title,v['title'])
            #if basefile % 10 == 0:
            #    desc.value(dct.abstract,"This is a longer summary of document %s" % basefile)
                
            util.ensure_dir(self.repo.store.distilled_path(str(basefile)))
            with open(self.repo.store.distilled_path(str(basefile)), "wb") as fp:
                g.serialize(fp, format="pretty-xml")
            
            util.ensure_dir(self.repo.store.parsed_path(str(basefile)))
            with open(self.repo.store.parsed_path(str(basefile)), "w") as fp:
                fp.write("""<?xml version='1.0' encoding='utf-8'?>
<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML+RDFa 1.0//EN" "http://www.w3.org/MarkUp/DTD/xhtml-rdfa-1.dtd">
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="en">
  <head about="%(id)s">
    <title>%(title)s</title>
  </head>
  <body about="%(id)s">
    <h1>%(title)s</h1>
  </body>
</html>""" % v)

            util.ensure_dir(self.repo.store.generated_path(str(basefile)))
            with open(self.repo.store.generated_path(str(basefile)), "w") as fp:
                fp.write("""<!DOCTYPE html>
<html>
  <head>
    <title>%(title)s</title>
  </head>
  <body>
    <h1>%(title)s</h1>
  </body>
</html>""" % v)

            
    def test_criteria(self):
        criteria = self.repo.news_criteria()
        self.assertEqual(len(criteria),1)
        self.assertEqual(criteria[0].basefile, "main")
        self.assertEqual(criteria[0].feedtitle, "New and updated documents")
        fakeentry = Mock()
        fakeentry.updated = datetime(2013,3,12,11,52)
        self.assertEqual(criteria[0].key(fakeentry), datetime(2013,3,12,11,52))
        self.assertTrue(criteria[0].selector(fakeentry))

    def test_entries(self):
        unsorted_entries = self.repo.news_entries() # not guaranteed particular order
        # sort so that most recently updated first
        entries = sorted(list(unsorted_entries),
                         key=attrgetter('updated'), reverse=True)
        self.assertEqual(len(entries),25)
        self.assertEqual(entries[0].title, "Doc #24")
        self.assertEqual(entries[-1].title, "Doc #0")

    def test_write_atom(self):
        self.maxDiff = None
        unsorted_entries = self.repo.news_entries() # not guaranteed
        # particular order sort so that most recently updated first
        # (simplified ver of what news() does)
        entries = sorted(list(unsorted_entries),
                         key=attrgetter('updated'), reverse=True)

        paths = self.repo.news_write_atom(entries, 'New and updated documents', 'main',
                                  archivesize=6)
        d = self.datadir
        want = [self.p('%s/base/feed/main.atom'%d,False),
                self.p('%s/base/feed/main-archive-1.atom'%d,False),
                self.p('%s/base/feed/main-archive-2.atom'%d,False),
                self.p('%s/base/feed/main-archive-3.atom'%d, False)]
        self.assertEqual(paths, want)
        tree = etree.parse('%s/base/feed/main.atom'%d)
        NS = "{http://www.w3.org/2005/Atom}"
        # main-archive-1 0-5
        # main-archive-2 6-11
        # main-archive-3 12-17
        # main           18-24
        
        # assert that prev-archive points to main-archive-3.atom
        prev_archive = tree.find(NS+"link[@rel='prev-archive']")
        self.assertEqual(prev_archive.get("href"), "main-archive-3.atom")

        # assert that title is 'New and updated documents'
        self.assertEqual(tree.find(NS+"title").text, "New and updated documents")
        # assert that entries 18-24 is in main feed
        entries = tree.findall(NS+"entry")
        self.assertEqual(len(entries),7)

        basedate = datetime(2013,1,1,12,0)
        # assert that first entry is doc #24, has correct <id>,
        # <updated>, <published>, <title>, <content src> <link href>
        self._check_entry(entries[0],
                          entryid="http://localhost:8000/res/base/24",
                          published=basedate + timedelta(hours=24,minutes=30),
                          updated=basedate + timedelta(hours=24,minutes=40),
                          title='Doc #24',
                          contentsrc='../parsed/24.xhtml',
                          linksrc='../distilled/24.rdf')

        # same for last entry (doc #18)
        self._check_entry(entries[-1],
                          entryid="http://localhost:8000/res/base/18",
                          published=basedate + timedelta(hours=18,minutes=30),
                          updated=basedate + timedelta(hours=18,minutes=40),
                          title='Doc #18',
                          contentsrc='../parsed/18.xhtml',
                          linksrc='../distilled/18.rdf')

        # open archive-3, assert 6 entries,
        # prev-archive=main-archive-2, next-archive=main.atom
        tree = etree.parse('%s/base/feed/main-archive-3.atom'%d)
        self.assertEqual(len(tree.findall(NS+"entry")),6)
        self.assertEqual(tree.find(NS+"link[@rel='prev-archive']").get("href"),
                         "main-archive-2.atom")
        self.assertEqual(tree.find(NS+"link[@rel='next-archive']").get("href"),
                         "main.atom")

        # open archive-2, assert 6 entries,
        # prev-archive=main-archive-1, next-archive=main-archive-3
        tree = etree.parse('%s/base/feed/main-archive-2.atom'%d)
        self.assertEqual(len(tree.findall(NS+"entry")),6)
        self.assertEqual(tree.find(NS+"link[@rel='prev-archive']").get("href"),
                         "main-archive-1.atom")
        self.assertEqual(tree.find(NS+"link[@rel='next-archive']").get("href"),
                         "main-archive-3.atom")

        # open archive-1, assert 6 entries, no
        # prev-archive, next-archive=main-archive-2
        tree = etree.parse('%s/base/feed/main-archive-1.atom'%d)
        self.assertEqual(len(tree.findall(NS+"entry")),6)
        self.assertIsNone(tree.find(NS+"link[@rel='prev-archive']"))
        self.assertEqual(tree.find(NS+"link[@rel='next-archive']").get("href"),
                         "main-archive-2.atom")


    def _check_entry(self, entry, entryid, title, published, updated, contentsrc, linksrc):
        NS = "{http://www.w3.org/2005/Atom}"

        self.assertEqual(entry.find(NS+"id").text,entryid)
        self.assertEqual(entry.find(NS+"title").text,title)
        self.assertEqual(entry.find(NS+"published").text,
                         util.rfc_3339_timestamp(published))
        self.assertEqual(entry.find(NS+"updated").text,
                         util.rfc_3339_timestamp(updated))

        content = entry.find(NS+"content")
        self.assertEqual(content.get("src"), contentsrc)
        self.assertEqual(content.get("type"), 'application/html+xml')
        link = entry.find(NS+"link[@rel='alternate']")
        self.assertEqual(link.get("href"), linksrc)
        self.assertEqual(link.get("type"),'application/rdf+xml')


class Storage(RepoTester):


    def test_list_basefiles_file(self):
        files = ["base/downloaded/123/a.html",
                 "base/downloaded/123/b.html",
                 "base/downloaded/124/a.html",
                 "base/downloaded/124/b.html"]
        basefiles = ["124/b", "124/a", "123/b", "123/a"]
        for f in files:
            util.writefile(self.p(f),"Nonempty")
        self.assertEqual(list(self.repo.list_basefiles_for("parse")),
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
        self.assertEqual(list(self.repo.list_basefiles_for("parse")),
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
        self.repo.store.archive("123/a",version)
        self.assertEqual(version, "1") # what algorithm do the default use? len(self.archived_versions)?

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


    def test_no_patch(self):
        result, desc = self.repo.patch_if_needed("123/a", self.sourcedoc)
        self.assertEqual(None, desc)
        self.assertEqual(self.sourcedoc, result)



# Add doctests in the module
from ferenda import documentrepository
def load_tests(loader,tests,ignore):
    tests.addTests(doctest.DocTestSuite(documentrepository))
    return tests
