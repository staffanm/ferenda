# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import sys, os
from ferenda.compat import unittest
if os.getcwd() not in sys.path: sys.path.insert(0,os.getcwd())

import tempfile
import shutil
import time


#SUT
from ferenda import DocumentStore
from ferenda import util
from ferenda.errors import *

class Store(unittest.TestCase):
    def setUp(self):
        self.datadir = tempfile.mkdtemp()
        self.store = DocumentStore(self.datadir)

    def tearDown(self):
        shutil.rmtree(self.datadir)

    def p(self,path):
        path = self.datadir+"/"+path
        return path.replace('/', '\\') if os.sep == '\\' else path


    def test_open(self):
        wanted_filename = self.store.path("basefile", "maindir", ".suffix")
        with self.store.open("basefile", "maindir", ".suffix", "w") as fp:
            self.assertNotEqual(fp.name, wanted_filename)
            self.assertEqual(fp.realname, wanted_filename)
            fp.write("This is the data")
        self.assertEqual(util.readfile(wanted_filename),
                         "This is the data")
        mtime = os.stat(wanted_filename).st_mtime

        # make sure writing identical content does not actually write
        # a new file
        time.sleep(.1) # just to get a different mtime
        with self.store.open("basefile", "maindir", ".suffix", "w") as fp:
            fp.write("This is the data")
        self.assertEqual(os.stat(wanted_filename).st_mtime,
                         mtime)

    
    def test_path(self):
        self.assertEqual(self.store.path("123","foo", ".bar"),
                         self.p("foo/123.bar"))
        self.assertEqual(self.store.path("123/a","foo", ".bar"),
                         self.p("foo/123/a.bar"))
        self.assertEqual(self.store.path("123:a","foo", ".bar"),
                         self.p("foo/123/%3Aa.bar"))
        realsep  = os.sep
        try:
            os.sep = "\\"
            self.assertEqual(self.store.path("123", "foo", ".bar"),
                             self.datadir.replace("/", os.sep) + "\\foo\\123.bar")
        finally:
            os.sep = realsep


    def test_path_version(self):
        eq = self.assertEqual
        eq(self.store.path("123","foo", ".bar", version="42"),
           self.p("archive/foo/123/42.bar"))
        eq(self.store.path("123/a","foo", ".bar", version="42"),
           self.p("archive/foo/123/a/42.bar"))
        eq(self.store.path("123:a","foo", ".bar", version="42"),
           self.p("archive/foo/123/%3Aa/42.bar"))
        eq(self.store.path("123:a","foo", ".bar", version="42:1"),
           self.p("archive/foo/123/%3Aa/42/%3A1.bar"))
        self.store.storage_policy = "dir"
        eq(self.store.path("123","foo", ".bar", version="42"),
           self.p("archive/foo/123/42/index.bar"))
        eq(self.store.path("123/a","foo", ".bar", version="42"),
           self.p("archive/foo/123/a/42/index.bar"))
        eq(self.store.path("123:a","foo", ".bar", version="42"),
           self.p("archive/foo/123/%3Aa/42/index.bar"))
        eq(self.store.path("123:a","foo", ".bar", version="42:1"),
           self.p("archive/foo/123/%3Aa/42/%3A1/index.bar"))
            

    def test_path_attachment(self):
        eq = self.assertEqual
        repo = self.store # to shorten lines < 80 chars
        repo.storage_policy = "dir" # attachments require this
        eq(repo.path("123","foo", None, attachment="external.foo"),
           self.p("foo/123/external.foo"))
        eq(repo.path("123/a","foo", None, attachment="external.foo"),
           self.p("foo/123/a/external.foo"))
        eq(repo.path("123:a","foo", None, attachment="external.foo"),
           self.p("foo/123/%3Aa/external.foo"))
        
        with self.assertRaises(AttachmentNameError):
            repo.path("123:a","foo", None,
                              attachment="invalid:attachment")

        with self.assertRaises(AttachmentNameError):
           repo.path("123:a","foo", None,
                             attachment="invalid/attachment"), 

        repo.storage_policy = "file"
        with self.assertRaises(AttachmentPolicyError):
           repo.path("123:a","foo", None,
                             attachment="external.foo"), 

    def test_path_version_attachment(self):
        eq = self.assertEqual
        self.store.storage_policy = "dir"
        eq(self.store.path("123","foo", None,
                                  version="42", attachment="external.foo"),
           self.p("archive/foo/123/42/external.foo"))
        eq(self.store.path("123/a","foo", None,
                                  version="42", attachment="external.foo"),
           self.p("archive/foo/123/a/42/external.foo"))

        eq(self.store.path("123:a","foo", None,
                                  version="42", attachment="external.foo"),
           self.p("archive/foo/123/%3Aa/42/external.foo"))
        
        
    def test_specific_path_methods(self):
        self.assertEqual(self.store.downloaded_path('123/a'),
                         self.p("downloaded/123/a.html"))
        self.assertEqual(self.store.downloaded_path('123/a', version="1"),
                         self.p("archive/downloaded/123/a/1.html"))
        self.assertEqual(self.store.parsed_path('123/a', version="1"),
                         self.p("archive/parsed/123/a/1.xhtml"))
        self.assertEqual(self.store.generated_path('123/a', version="1"),
                         self.p("archive/generated/123/a/1.html"))
        self.store.storage_policy = "dir"
        self.assertEqual(self.store.downloaded_path('123/a'),
                         self.p("downloaded/123/a/index.html"))
        self.assertEqual(self.store.downloaded_path('123/a', version="1"),
                         self.p("archive/downloaded/123/a/1/index.html"))
        self.assertEqual(self.store.parsed_path('123/a', version="1"),
                         self.p("archive/parsed/123/a/1/index.xhtml"))
        self.assertEqual(self.store.generated_path('123/a', version="1"),
                         self.p("archive/generated/123/a/1/index.html"))

           
    def test_basefile_to_pathfrag(self):
        self.assertEqual(self.store.basefile_to_pathfrag("123-a"), "123-a")
        self.assertEqual(self.store.basefile_to_pathfrag("123/a"), "123/a")
        self.assertEqual(self.store.basefile_to_pathfrag("123:a"), "123"+os.sep+"%3Aa")

    def test_pathfrag_to_basefile(self):
        self.assertEqual(self.store.pathfrag_to_basefile("123-a"), "123-a")
        self.assertEqual(self.store.pathfrag_to_basefile("123/a"), "123/a")
        self.assertEqual(self.store.pathfrag_to_basefile("123/%3Aa"), "123:a")

        try:
            # make sure the pathfrag method works as expected even when os.sep is not "/"
            realsep = os.sep
            os.sep = "\\"
            self.assertEqual(self.store.pathfrag_to_basefile("123\\a"), "123/a")
        finally:
            os.sep = realsep

    def test_list_basefiles_file(self):
        files = ["downloaded/123/a.html",
                 "downloaded/123/b.html",
                 "downloaded/124/a.html",
                 "downloaded/124/b.html"]
        basefiles = ["124/b", "124/a", "123/b", "123/a"]
        for f in files:
            util.writefile(self.p(f),"Nonempty")
        self.assertEqual(list(self.store.list_basefiles_for("parse")),
                         basefiles)

    def test_list_basefiles_parse_dir(self):
        files = ["downloaded/123/a/index.html",
                 "downloaded/123/b/index.html",
                 "downloaded/124/a/index.html",
                 "downloaded/124/b/index.html"]
        basefiles = ["124/b", "124/a", "123/b", "123/a"]

        self.store.storage_policy = "dir"
        for f in files:
            util.writefile(self.p(f),"nonempty")
        self.assertEqual(list(self.store.list_basefiles_for("parse")),
                         basefiles)

    def test_list_basefiles_generate_dir(self):
        files = ["parsed/123/a/index.xhtml",
                 "parsed/123/b/index.xhtml",
                 "parsed/124/a/index.xhtml",
                 "parsed/124/b/index.xhtml"]
        basefiles = ["124/b", "124/a", "123/b", "123/a"]

        self.store.storage_policy = "dir"
        for f in files:
            util.writefile(self.p(f),"nonempty")
        self.assertEqual(list(self.store.list_basefiles_for("generate")),
                         basefiles)

    def test_list_basefiles_postgenerate_file(self):
        files = ["generated/123/a.html",
                 "generated/123/b.html",
                 "generated/124/a.html",
                 "generated/124/b.html"]
        basefiles = ["124/b", "124/a", "123/b", "123/a"]
        for f in files:
            util.writefile(self.p(f),"nonempty")
        self.assertEqual(list(self.store.list_basefiles_for("_postgenerate")),
                         basefiles)

    def test_list_basefiles_invalid(self):
        with self.assertRaises(ValueError):
            list(self.store.list_basefiles_for("invalid_action"))

    def test_list_versions_file(self):
        files = ["archive/downloaded/123/a/1.html",
                 "archive/downloaded/123/a/2.html",
                 "archive/downloaded/123/a/2bis.html",
                 "archive/downloaded/123/a/10.html"]
        versions = ["1","2", "2bis", "10"]
        for f in files:
            util.writefile(self.p(f),"nonempty")
            # list_versions(action, basefile)
        self.assertEqual(list(self.store.list_versions("123/a","downloaded")),
                         versions)

    def test_list_versions_dir(self):
        files = ["archive/downloaded/123/a/1/index.html",
                 "archive/downloaded/123/a/2/index.html",
                 "archive/downloaded/123/a/2bis/index.html",
                 "archive/downloaded/123/a/10/index.html"]
        basefiles = ['123/a']
        versions = ["1","2", "2bis", "10"]
        for f in files:
            util.writefile(self.p(f),"nonempty")
        self.store.storage_policy = "dir"
        self.assertEqual(list(self.store.list_versions("123/a", "downloaded")),
                         versions)

    def test_list_attachments(self):
        files = ["downloaded/123/a/index.html",
                 "downloaded/123/a/attachment.html",
                 "downloaded/123/a/appendix.pdf",
                 "downloaded/123/a/other.txt"]
        basefiles = ['123/a']
        attachments = ['appendix.pdf', 'attachment.html', 'other.txt']
        for f in files:
            util.writefile(self.p(f),"nonempty")
            # list_attachments(action, basefile, version=None)
        self.assertEqual(list(self.store.list_attachments("123/a", "downloaded")),
                         attachments)
        
    def test_list_attachments_version(self):
        files = ["archive/downloaded/123/a/1/index.html",
                 "archive/downloaded/123/a/1/attachment.txt",
                 "archive/downloaded/123/a/2/index.html",
                 "archive/downloaded/123/a/2/attachment.txt",
                 "archive/downloaded/123/a/2/other.txt"]
        basefiles = ['123/a']
        versions = ['1','2']
        attachments_1 = ['attachment.txt']
        attachments_2 = ['attachment.txt', 'other.txt']
        for f in files:
            util.writefile(self.p(f),"nonempty")

        self.assertEqual(list(self.store.list_attachments("123/a","downloaded",
                                                         "1")),
                         attachments_1)
        self.assertEqual(list(self.store.list_attachments("123/a","downloaded",
                                                         "2")),
                         attachments_2)


import doctest
from ferenda import documentstore
def load_tests(loader,tests,ignore):
    tests.addTests(doctest.DocTestSuite(documentstore))
    return tests
