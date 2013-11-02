# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import sys, os
from ferenda.compat import unittest
if os.getcwd() not in sys.path: sys.path.insert(0,os.getcwd())

import tempfile
import shutil
import os
from datetime import datetime

import six

from ferenda import DocumentRepository, util

# SUT
from ferenda import DocumentEntry

class DocEntry(unittest.TestCase):
    basic_json = """{
  "basefile": null, 
  "content": {
    "hash": null, 
    "markup": null, 
    "src": null, 
    "type": null
  }, 
  "id": "http://example.org/123/a", 
  "link": {
    "hash": null, 
    "href": null, 
    "length": null, 
    "type": null
  }, 
  "orig_checked": "2013-03-27T20:46:37", 
  "orig_updated": null, 
  "orig_url": "http://source.example.org/doc/123/a", 
  "published": null, 
  "summary": null, 
  "title": null, 
  "updated": null, 
  "url": null
}"""
    modified_json = """{
  "basefile": null, 
  "content": {
    "hash": null, 
    "markup": "<div>xhtml fragment</div>", 
    "src": null, 
    "type": "xhtml"
  }, 
  "id": "http://example.org/123/a", 
  "link": {
    "hash": null, 
    "href": null, 
    "length": null, 
    "type": null
  }, 
  "orig_checked": "2013-03-27T20:46:37", 
  "orig_updated": "2013-03-27T20:59:42.325067", 
  "orig_url": "http://source.example.org/doc/123/a", 
  "published": null, 
  "summary": null, 
  "title": null, 
  "updated": null, 
  "url": null
}"""

    def setUp(self):
        self.maxDiff = None
        self.datadir = tempfile.mkdtemp()
        self.repo = DocumentRepository(datadir=self.datadir)

    def tearDown(self):
        shutil.rmtree(self.datadir)

    def d2u(self, s):
        return s.replace("\r\n", "\n")
        
    def test_init(self):
        d = DocumentEntry()
        self.assertIsNone(d.id) # same for .updated, .published,
                                # .title, .summary, .url and .content
        self.assertEqual(d.content, {})
        self.assertEqual(d.link,   {})

        path = self.repo.store.documententry_path("123/b")
        d = DocumentEntry(path=path)
        self.assertIsNone(d.id) # same for .updated, .published,
                                # .title, .summary, .url and .content
        self.assertEqual(d.content, {})
        self.assertEqual(d.link,   {})


    def test_load(self):
        path = self.repo.store.documententry_path("123/a")
        util.ensure_dir(path)
        with open(path, "w") as fp:
            fp.write(self.basic_json)
        d = DocumentEntry(path=path)
        self.assertEqual(d.orig_checked, datetime(2013,3,27,20,46,37))
        self.assertIsNone(d.orig_updated)
        self.assertEqual(d.orig_url,'http://source.example.org/doc/123/a')
        self.assertEqual(d.id,'http://example.org/123/a')
        self.assertEqual('<DocumentEntry id=http://example.org/123/a>', repr(d))
 
    def test_save(self):
        path = self.repo.store.documententry_path("123/a")
        d = DocumentEntry()
        d.orig_checked = datetime(2013,3,27,20,46,37)
        d.orig_url = 'http://source.example.org/doc/123/a'
        d.save(path=path)

        self.maxDiff = None
        self.assertEqual(self.d2u(util.readfile(path)), self.basic_json)

    def test_save(self):
        path = self.repo.store.documententry_path("123/x")
        d = DocumentEntry()
        d.title = six.StringIO("A file-like object, not a string")
        with self.assertRaises(TypeError):
            d.save(path=path)


    def test_modify(self):
        path = self.repo.store.documententry_path("123/a")
        util.ensure_dir(path)
        with open(path, "w") as fp:
            fp.write(self.basic_json)

        d = DocumentEntry(path=path)
        d.orig_updated = datetime(2013, 3, 27, 20, 59, 42, 325067)
        d.id = "http://example.org/123/a"
        # do this in setUp?
        with open(self.datadir+"/xhtml","w") as f:
            f.write("<div>xhtml fragment</div>")

        d.set_content(self.datadir+"/xhtml", "http://example.org/test",
                      mimetype="xhtml", inline=True)
        d.save()
        self.assertEqual(self.d2u(util.readfile(path)), self.modified_json)

    def test_set_content(self):
        t = tempfile.mktemp()
        with open(t,"w") as f:
             f.write("<div>xhtml fragment</div>")

        d = DocumentEntry()
        d.set_content(t, "http://example.org/test", mimetype="xhtml", inline=True)
        # type must be either "text", "html",  "xhtml" or a MIME media type (RFC 4287, 4.1.3.1)
        self.assertEqual(d.content['type'],"xhtml")
        self.assertEqual(d.content['markup'],"<div>xhtml fragment</div>")
        self.assertIsNone(d.content['src'])

        d = DocumentEntry()
        d.set_content(t, "http://example.org/test", mimetype="xhtml")
        self.assertEqual(d.content['type'],"xhtml")
        self.assertIsNone(d.content['markup'])
        self.assertEqual(d.content['src'], "http://example.org/test")
        self.assertEqual(d.content['hash'], "md5:ca8d87b5cf6edbbe88f51d45926c9a8d")

        os.unlink(t)
        
        t = tempfile.mktemp()
        with open(t+".pdf","w") as f:
             f.write("This is not a real PDF file")
        
        d = DocumentEntry()
        d.set_content(t+".pdf", "http://example.org/test")
        self.assertEqual(d.content['type'],"application/pdf")
        self.assertIsNone(d.content['markup'])
        self.assertEqual(d.content['src'], "http://example.org/test")
        self.assertEqual(d.content['hash'], "md5:0a461f0621ede53f1ea8471e34796b6f")

        d = DocumentEntry()
        with self.assertRaises(AssertionError):
            d.set_content(t+".pdf", "http://example.org/test", inline=True)

        os.unlink(t+".pdf")

    def test_set_link(self):
        t = tempfile.mktemp()
        with open(t+".html","w") as f:
             f.write("<div>xhtml fragment</div>")

        d = DocumentEntry()
        d.set_link(t+".html", "http://example.org/test")
        self.assertEqual(d.link['href'],"http://example.org/test")
        self.assertEqual(d.link['type'], "text/html")
        self.assertEqual(d.link['length'],25)
        self.assertEqual(d.link['hash'],"md5:ca8d87b5cf6edbbe88f51d45926c9a8d")

    def test_guess_type(self):
        d = DocumentEntry()
        self.assertEqual(d.guess_type("test.pdf"),  "application/pdf")
        self.assertEqual(d.guess_type("test.rdf"),  "application/rdf+xml")
        self.assertEqual(d.guess_type("test.html"), "text/html")
        self.assertEqual(d.guess_type("test.xhtml"),"application/html+xml")
        self.assertEqual(d.guess_type("test.bin"),  "application/octet-stream")

