# -*- coding: utf-8 -*-
from __future__ import unicode_literals

# NOTE: This unittest requires that the pdftohtml binary is available
# and calls that, making this not a pure unittest.

import sys, os, tempfile, shutil
from lxml import etree
from ferenda.compat import unittest
if os.getcwd() not in sys.path: sys.path.insert(0,os.getcwd())

from ferenda import errors
# SUT
from ferenda import PDFReader

class Read(unittest.TestCase):
    def setUp(self):
        self.maxDiff = None
        self.datadir = tempfile.mkdtemp()
        self.reader = PDFReader()
        
    def tearDown(self):
        shutil.rmtree(self.datadir)

    def test_basic(self):
        try:
            self.reader.read("test/files/pdfreader/sample.pdf",
                             self.datadir)
        except errors.ExternalCommandError:
            self._copy_files()
            self.reader.read("test/files/pdfreader/sample.pdf",
                             self.datadir)

        # a temporary copy of the pdf file should not be lying around in workdir
        # print("Checking if %s has been unlinked" % (self.datadir + os.sep + "sample.pdf"))
        self.assertFalse(os.path.exists(self.datadir + os.sep + "sample.pdf"))
        # but the XML file should be stored for subsequent parses
        self.assertTrue(os.path.exists(self.datadir + os.sep + "sample.xml"))
        
        self.assertEqual(len(self.reader), 1)
        # first page, first box
        title = str(self.reader[0][0])
        self.assertEqual("Document title ", title)

        self.assertEqual(318, self.reader.median_box_width())

        page = self.reader[0]
        self.assertEqual("Page 1 (892 x 1263): 'Document title  This is a simple documen...'", str(page))

        # an uncropped doc should have two textboxes
        self.assertEqual(2, len(list(page.boundingbox())))

        # a smaller bounding box yields just one
        self.assertEqual(1,
                         len(list(page.boundingbox(190, 130, 230, 460))))

        # cropping it with the same dimensions
        page.crop(190, 130, 230, 460)

        # should also result in just one box -- the bottom one
        boxes = list(page.boundingbox())
        self.assertEqual(1, len(boxes))

        box = boxes[0]

        self.assertEqual("This is a simple document in PDF format. ", str(box))
        self.assertEqual({'color': '#000000',
                          'size': '16',
                          'id': '1',
                          'family': 'Cambria'}, box.getfont())
                         

        # this box should have four text elements
        self.assertEqual(4, len(box))
        self.assertEqual(None, box[0].tag)
        self.assertEqual("i", box[1].tag)
        self.assertEqual("ib", box[2].tag)
        self.assertEqual(None, box[3].tag)
        
    def test_dontkeep(self):
        self.assertFalse(os.path.exists(self.datadir + os.sep + "sample.xml.bz2"))
        try:
            self.reader.read("test/files/pdfreader/sample.pdf",
                             self.datadir,
                             keep_xml=False)
        except errors.ExternalCommandError:
            self._copy_sample()
            self.reader.read("test/files/pdfreader/sample.pdf",
                             self.datadir,
                             keep_xml=False)

        # No XML file should exist
        self.assertFalse(os.path.exists(self.datadir + os.sep + "sample.xml"))
        self.assertFalse(os.path.exists(self.datadir + os.sep + "sample.xml.bz2"))

    def _copy_sample(self):
        for fname in os.listdir("test/files/pdfreader/intermediate"):
            to = fname.replace("index", "sample")
            shutil.copy("test/files/pdfreader/intermediate/%s" % fname,
                         self.datadir + os.sep + to)


    def test_bz2(self):
        try:
            self.reader.read("test/files/pdfreader/sample.pdf",
                             self.datadir,
                             keep_xml="bz2")
        except errors.ExternalCommandError:
            self._copy_sample()
            # need to bzip2 here
            self.reader.read("test/files/pdfreader/sample.pdf",
                             self.datadir)

        # a temporary copy of the pdf file should not be lying around in workdir
        self.assertFalse(os.path.exists(self.datadir + os.sep + "sample.pdf"))
        # but the XML file (only in bz2 format) should be stored
        self.assertTrue(os.path.exists(self.datadir + os.sep + "sample.xml.bz2"))
        self.assertFalse(os.path.exists(self.datadir + os.sep + "sample.xml"))

        # first page, first box
        self.assertEqual("Document title ", str(self.reader[0][0]))

        # parsing again should reuse the existing sample.xml.bz2
        self.reader.read("test/files/pdfreader/sample.pdf",
                         self.datadir,
                         keep_xml="bz2")
        
