# -*- coding: utf-8 -*-
from __future__ import unicode_literals

# NOTE: This unittest requires that the antiword binary is available
# and calls that, making this not a pure unittest (it also
# reads word files from disk) but that is just the way it is.

import sys, os, tempfile, shutil
from lxml import etree
from ferenda.compat import unittest
if os.getcwd() not in sys.path: sys.path.insert(0,os.getcwd())

# SUT
from ferenda import WordReader

class Read(unittest.TestCase):
    def setUp(self):
        self.maxDiff = None
        self.datadir = tempfile.mkdtemp()
        self.reader = WordReader()
        
    def tearDown(self):
        shutil.rmtree(self.datadir)

    def test_doc(self):
        path = self.datadir + os.sep + "out.xml"
        out, type = self.reader.read("test/files/wordreader/sample.doc",
                                     path)
        self.assertEqual(out, path)
        self.assertEqual(type, "doc")
        self.assertTrue(os.path.exists(path))
        tree = etree.parse(path)
        self.assertEqual("book", tree.getroot().tag)
        xpath = '//*[contains(text(), "simple document in .doc format")]'
        self.assertTrue(tree.getroot().xpath(xpath))

        # test that spaces in filename work (requires more cmdline quoting)
        os.unlink(path)
        out, type = self.reader.read("test/files/wordreader/spaces in filename.doc",
                                     path)
        self.assertEqual(out, path)
        self.assertEqual(type, "doc")
        

    def test_docx(self):
        path = self.datadir + os.sep + "out.xml"
        out, type = self.reader.read("test/files/wordreader/sample.docx",
                                     path)
        self.assertEqual(out, path)
        self.assertEqual(type, "docx")
        self.assertTrue(os.path.exists(path))
        tree = etree.parse(path)
        self.assertEqual("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}document",
                         tree.getroot().tag)
        xpath = '//*[contains(text(), "simple document in OOXML (.docx) format")]'
        self.assertTrue(tree.getroot().xpath(xpath))
            
    def test_mislabeled(self):
        path = self.datadir + os.sep + "out.xml"
        out, type = self.reader.read("test/files/wordreader/mislabeled.doc",
                                     path)
        self.assertEqual(out, path)
        self.assertEqual(type, "docx")
        self.assertTrue(os.path.exists(path))
        tree = etree.parse(path)
        self.assertEqual("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}document",
                         tree.getroot().tag)
        xpath = '//*[contains(text(), "mis-labeled as a .doc file")]'
        self.assertTrue(tree.getroot().xpath(xpath))
            

        
