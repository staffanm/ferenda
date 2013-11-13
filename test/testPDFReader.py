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
            for fname in os.listdir("test/files/pdfreader/intermediate"):
                to = fname.replace("index", "sample")
                shutil.copy("test/files/pdfreader/intermediate/%s" % fname,
                             self.datadir + os.sep + to)
            self.reader.read("test/files/pdfreader/sample.pdf",
                             self.datadir)
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
                          'family': 'Times'}, box.getfont())
                         

        # this box should have four text elements
        self.assertEqual(4, len(box))
        self.assertEqual(None, box[0].tag)
        self.assertEqual("i", box[1].tag)
        self.assertEqual("ib", box[2].tag)
        self.assertEqual(None, box[3].tag)
        
        
