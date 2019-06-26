# -*- coding: utf-8 -*-
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
from builtins import *

# NOTE: This unittest requires that the antiword binary is available
# and calls that, making this not a pure unittest (it also
# reads word files from disk) but that is just the way it is.

import os
import tempfile
import shutil

from lxml import etree
from ferenda.compat import unittest
from ferenda.errors import ExternalCommandError

from quiet import silence

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
        try:
            with open(path, "wb") as fp:
                filetype = self.reader.read("test/files/wordreader/sample.doc",
                                            fp)
            self.assertEqual(filetype, "doc")
            self.assertTrue(os.path.exists(path))
            tree = etree.parse(path)
            self.assertEqual("book", tree.getroot().tag)
            xpath = '//*[contains(text(), "simple document in .doc format")]'
            self.assertTrue(tree.getroot().xpath(xpath))

            # test that spaces in filename work (requires more cmdline quoting)
            os.unlink(path)
            with open(path, "wb") as fp:
                filetype = self.reader.read("test/files/wordreader/spaces in filename.doc",
                                            fp)
            self.assertEqual(filetype, "doc")
        except ExternalCommandError as e:
            raise unittest.SkipTest("Antiword does not seem to be installed")
        
        

    def test_docx(self):
        path = self.datadir + os.sep + "out.xml"
        with open(path, "wb") as fp:
            filetype = self.reader.read("test/files/wordreader/sample.docx",
                                        fp)
        self.assertEqual(filetype, "docx")
        self.assertTrue(os.path.exists(path))
        tree = etree.parse(path)
        self.assertEqual("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}document",
                         tree.getroot().tag)
        xpath = '//*[contains(text(), "simple document in OOXML (.docx) format")]'
        self.assertTrue(tree.getroot().xpath(xpath))
            
    def test_mislabeled(self):
        path = self.datadir + os.sep + "out.xml"
        try:
            with silence():
                with open(path, "wb") as fp:
                    filetype = self.reader.read("test/files/wordreader/mislabeled.doc",
                                                fp)
            self.assertEqual(filetype, "docx")
            self.assertTrue(os.path.exists(path))
            tree = etree.parse(path)
            self.assertEqual("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}document",
                             tree.getroot().tag)
            xpath = '//*[contains(text(), "mis-labeled as a .doc file")]'
            self.assertTrue(tree.getroot().xpath(xpath))
        except ExternalCommandError as e:
            raise unittest.SkipTest("Antiword does not seem to be installed")
             

        
