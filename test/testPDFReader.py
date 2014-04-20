# -*- coding: utf-8 -*-
from __future__ import unicode_literals

# NOTE: This unittest requires that the pdftohtml binary is available
# and calls that, making this not a pure unittest.

import sys, os, tempfile, shutil
from lxml import etree
from ferenda.compat import unittest
if os.getcwd() not in sys.path: sys.path.insert(0,os.getcwd())
from bz2 import BZ2File
from ferenda import errors, util
from six import text_type as str
# SUT
from ferenda import PDFReader

class Read(unittest.TestCase):
    def setUp(self):
        self.maxDiff = None
        self.datadir = tempfile.mkdtemp()
        self.reader = PDFReader()
        
    def tearDown(self):
        shutil.rmtree(self.datadir)


    def _copy_sample(self):
        for fname in os.listdir("test/files/pdfreader/intermediate"):
            # to = fname.replace("index", "sample") # why?
            shutil.copy("test/files/pdfreader/intermediate/%s" % fname,
                         self.datadir + os.sep + fname)

    def test_basic(self):
        try:
            self.reader.read("test/files/pdfreader/sample.pdf",
                             self.datadir)
        except errors.ExternalCommandError:
            self._copy_sample()
            self.reader.read("test/files/pdfreader/sample.pdf",
                             self.datadir)

        # a temporary copy of the pdf file should not be lying around in workdir
        # print("Checking if %s has been unlinked" % (self.datadir + os.sep + "sample.pdf"))
        self.assertFalse(os.path.exists(self.datadir + os.sep + "sample.pdf"))
        # but the XML file should be stored for subsequent parses
        self.assertTrue(os.path.exists(self.datadir + os.sep + "sample.xml"))

        # The PDF contained actual textboxes
        self.assertFalse(self.reader.is_empty())

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

    def test_bz2(self):
        try:
            self.reader.read("test/files/pdfreader/sample.pdf",
                             self.datadir,
                             keep_xml="bz2")
        except errors.ExternalCommandError:
            self._copy_sample()
            # bzip2 our canned sample.xml
            with open(self.datadir + os.sep + "sample.xml", "rb") as rfp:
                wfp = BZ2File(self.datadir + os.sep + "sample.xml.bz2", "wb")
                wfp.write(rfp.read())
                wfp.close()
            os.unlink(self.datadir + os.sep + "sample.xml")
            self.reader.read("test/files/pdfreader/sample.pdf",
                             self.datadir,
                             keep_xml="bz2")

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

    def test_convert(self):
        # how to test this when soffice isnt available and on $PATH?
        pass

    def test_ocr(self):
        try:
            if not os.environ.get("FERENDA_TEST_TESSERACT"):
                raise errors.ExternalCommandError
            self.reader.read("test/files/pdfreader/scanned.pdf",
                             self.datadir,
                             ocr_lang="swe")
        except errors.ExternalCommandError:
            self._copy_sample()
            self.reader.read("test/files/pdfreader/scanned.pdf",
                             self.datadir,
                             ocr_lang="swe")

        # assert that a hOCR file has been created
        self.assertTrue(os.path.exists(self.datadir + os.sep + "scanned.hocr.html"))

        # assert that we have two pages
        self.assertEqual(2, len(self.reader))

        # assert that first element in the first textbox in the first
        # page corresponds to the first bbox
        self.assertEqual("Regeringens ", str(self.reader[0][0][0]))
        self.assertEqual(159, self.reader[0][0][0].top)
        self.assertEqual(129, self.reader[0][0][0].left)
        self.assertEqual(72, self.reader[0][0][0].height)
        self.assertEqual(400, self.reader[0][0][0].width)

        # assert that the third textbox (which has mostly normal text)
        # is rendered correctly (note that we have a couple of OCR errors).
        self.assertEqual("Regeringen föreslår riksdagen att anta de förslag som har tagits. upp i bifogade utdrag ur regeringsprotokollet den 31 oktober l99l.", util.normalize_space(str(self.reader[0][3])))

    def test_fallback_ocr(self):
        try:
            # actually running tesseract takes ages -- for day-to-day
            # testing we can just as well use the canned hocr.html
            # files that _copy_sample fixes for us.
            if not os.environ.get("FERENDA_TEST_TESSERACT"):
                raise errors.ExternalCommandError
            self.reader.read("test/files/pdfreader/scanned-ecma-99.pdf",
                             self.datadir,
                             images=False)
        except errors.ExternalCommandError:
            self._copy_sample()
            self.reader.read("test/files/pdfreader/scanned-ecma-99.pdf",
                             self.datadir,
                             images=False)

        self.assertTrue(self.reader.is_empty())
        self.reader.read("test/files/pdfreader/scanned-ecma-99.pdf",
                         self.datadir,
                         ocr_lang="eng")
        self.assertFalse(self.reader.is_empty())
        self.assertEqual(2, len(self.reader))
        self.assertEqual("EUROPEAN COMPUTER MANUFACTURERS ASSOCIATION",
                         util.normalize_space(str(self.reader[0][1])))
        

        
