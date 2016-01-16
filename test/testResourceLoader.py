# -*- coding: utf-8 -*-
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
from builtins import *

import tempfile
import shutil
import os

from ferenda.compat import unittest
from ferenda.errors import ResourceNotFound
from ferenda import DocumentEntry  # just used for test_loadpath
from ferenda import util

# SUT
from ferenda import ResourceLoader


# this class mainly exists so that we can try out make_loadpath
class SubTestCase(unittest.TestCase):
    pass


class Main(SubTestCase, DocumentEntry):
    def setUp(self):
        self.tempdir = tempfile.mkdtemp()
        loadpath = [self.tempdir + "/primary", self.tempdir + "/secondary"]
        util.writefile(loadpath[0]+os.sep+"primaryresource.txt", "Hello")
        util.writefile(loadpath[1]+os.sep+"secondaryresource.txt", "World")
        self.resourceloader = ResourceLoader(*loadpath)
    
    def tearDown(self):
        shutil.rmtree(self.tempdir)  
    
    def test_loadpath(self):
        self.assertEqual(ResourceLoader.make_loadpath(self),
                         ["test/res",  # from test.testResourceLoader.SubTestCase
                          "ferenda/res" # from ferenda.compat.unittest.TestCase
                          ])

    def test_exists(self):
        self.assertTrue(self.resourceloader.exists("primaryresource.txt"))
        self.assertTrue(self.resourceloader.exists("secondaryresource.txt"))
        self.assertTrue(self.resourceloader.exists("robots.txt"))
        self.assertFalse(self.resourceloader.exists("nonexistent.txt"))

    def test_open(self):
        with self.resourceloader.open("primaryresource.txt") as fp:
            self.assertEqual("Hello", fp.read())
        with self.resourceloader.open("secondaryresource.txt") as fp:
            self.assertEqual("World", fp.read())
        # should be available through the pkg_resources API
        with self.resourceloader.open("robots.txt") as fp:
            self.assertIn("# robotstxt.org/", fp.read())
        with self.assertRaises(ResourceNotFound):
            with self.resourceloader.open("nonexistent.txt") as fp:
                fp.read()

    def test_openfp(self):
        fp = self.resourceloader.openfp("primaryresource.txt")
        self.assertEqual("Hello", fp.read())
        fp.close()

        fp = self.resourceloader.openfp("secondaryresource.txt")
        self.assertEqual("World", fp.read())
        fp.close()
        
        fp = self.resourceloader.openfp("robots.txt")
        self.assertIn("# robotstxt.org/", fp.read())
        fp.close()

        with self.assertRaises(ResourceNotFound):
            fp = self.resourceloader.openfp("nonexistent.txt")

    def test_read(self):
        self.assertEqual("Hello",
                         self.resourceloader.load("primaryresource.txt"))
        self.assertEqual("World",
                         self.resourceloader.load("secondaryresource.txt"))
        self.assertIn("# robotstxt.org/",
                      self.resourceloader.load("robots.txt"))
        with self.assertRaises(ResourceNotFound):
            self.resourceloader.load("nonexistent.txt")
            
    def test_filename(self):
        self.assertEqual(self.tempdir + "/primary/primaryresource.txt",
                         self.resourceloader.filename("primaryresource.txt"))
        self.assertEqual(self.tempdir + "/secondary/secondaryresource.txt",
                         self.resourceloader.filename("secondaryresource.txt"))
        self.assertEqual("ferenda/res/robots.txt",
                         self.resourceloader.filename("robots.txt"))
        with self.assertRaises(ResourceNotFound):
            self.resourceloader.filename("nonexistent.txt")

    def test_extractdir(self):
        dest = self.tempdir + os.sep + "dest"
        os.mkdir(dest)
        self.resourceloader.extractdir(None, dest)
        self.assertEqual(set(os.listdir(dest)),
                         set(["primaryresource.txt", "secondaryresource.txt",
                              "robots.txt", "humans.txt"]))
