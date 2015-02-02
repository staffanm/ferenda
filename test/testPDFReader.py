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

    def tearDown(self):
        shutil.rmtree(self.datadir)

    def _copy_sample(self):
        for fname in os.listdir("test/files/pdfreader/intermediate"):
            shutil.copy("test/files/pdfreader/intermediate/%s" % fname,
                         self.datadir + os.sep + fname)

    def test_basic(self):
        try:
            reader = PDFReader(filename="test/files/pdfreader/sample.pdf",
                               workdir=self.datadir)
        except errors.ExternalCommandError:
            self._copy_sample()
            reader = PDFReader(filename="test/files/pdfreader/sample.pdf",
                               workdir=self.datadir)

        # a temporary copy of the pdf file should not be lying around
        # in workdir
        # print("Checking if %s has been unlinked" % (self.datadir +
        # os.sep + "sample.pdf"))
        self.assertFalse(os.path.exists(self.datadir + os.sep + "sample.pdf"))
        # but the XML file should be stored for subsequent parses
        self.assertTrue(os.path.exists(self.datadir + os.sep + "sample.xml"))

        # The PDF contained actual textboxes
        self.assertFalse(reader.is_empty())

        self.assertEqual(len(reader), 1)
        # first page, first box
        title = str(reader[0][0])
        self.assertEqual("Document title ", title)

        self.assertEqual(570, reader.median_box_width())

        page = reader[0]
        self.assertEqual("Page 1 (892 x 1263): 'Document title  This is a simple documen...'", str(page))

        
        # an uncropped doc should have nine nonempty textboxes
        self.assertEqual(9, len(list(page.boundingbox())))

        # a smaller bounding box yields just one
        self.assertEqual(1,
                         len(list(page.boundingbox(190, 130, 230, 460))))

        # cropping it with the same dimensions
        # NOTE: This will fail if convert (from imagemagick) isn't installed)
        try:
            page.crop(190, 130, 230, 460)
        except errors.ExternalCommandError:
            # the rest of the tests cannot succeed now. FIXME: We
            # should try to find a way to run them anyway
            return

        # should also result in just one box -- the bottom one
        boxes = list(page.boundingbox())
        self.assertEqual(1, len(boxes))

        box = boxes[0]

        self.assertEqual("This is a simple document in PDF format. ", str(box))
        self.assertEqual('#000000', box.font.color)
        self.assertEqual(16, box.font.size)
        self.assertEqual('1', box.font.id)
        self.assertEqual('Cambria', box.font.family)
                         

        # this box should have four text elements
        self.assertEqual(4, len(box))
        self.assertEqual(None, box[0].tag)
        self.assertEqual("i", box[1].tag)
        self.assertEqual("ib", box[2].tag)
        self.assertEqual(None, box[3].tag)
        
    def test_dontkeep(self):
        self.assertFalse(os.path.exists(self.datadir + os.sep + "sample.xml.bz2"))
        try:
            reader = PDFReader(filename="test/files/pdfreader/sample.pdf",
                               workdir=self.datadir,
                               keep_xml=False)
        except errors.ExternalCommandError:
            self._copy_sample()
            reader = PDFReader(filename="test/files/pdfreader/sample.pdf",
                               workdir=self.datadir,
                               keep_xml=False)

        # No XML file should exist
        self.assertFalse(os.path.exists(self.datadir + os.sep + "sample.xml"))
        self.assertFalse(os.path.exists(self.datadir + os.sep + "sample.xml.bz2"))

    def test_bz2(self):
        try:
            reader = PDFReader(filename="test/files/pdfreader/sample.pdf",
                               workdir=self.datadir,
                               keep_xml="bz2")
        except errors.ExternalCommandError:
            self._copy_sample()
            # bzip2 our canned sample.xml
            with open(self.datadir + os.sep + "sample.xml", "rb") as rfp:
                wfp = BZ2File(self.datadir + os.sep + "sample.xml.bz2", "wb")
                wfp.write(rfp.read())
                wfp.close()
            os.unlink(self.datadir + os.sep + "sample.xml")
            reader = PDFReader(filename="test/files/pdfreader/sample.pdf",
                               workdir=self.datadir,
                               keep_xml="bz2")

        # a temporary copy of the pdf file should not be lying around in workdir
        self.assertFalse(os.path.exists(self.datadir + os.sep + "sample.pdf"))
        # but the XML file (only in bz2 format) should be stored
        self.assertTrue(os.path.exists(self.datadir + os.sep + "sample.xml.bz2"))
        self.assertFalse(os.path.exists(self.datadir + os.sep + "sample.xml"))

        # first page, first box
        self.assertEqual("Document title ", str(reader[0][0]))

        # parsing again should reuse the existing sample.xml.bz2
        reader = PDFReader(filename="test/files/pdfreader/sample.pdf",
                           workdir=self.datadir,
                           keep_xml="bz2")

    def test_convert(self):
        # how to test this when soffice isnt available and on $PATH?
        pass

    def test_ocr(self):
        try:
            if not os.environ.get("FERENDA_TEST_TESSERACT"):
                raise errors.ExternalCommandError
            reader = PDFReader(filename="test/files/pdfreader/scanned.pdf",
                               workdir=self.datadir,
                               ocr_lang="swe")
        except errors.ExternalCommandError:
            self._copy_sample()
            reader = PDFReader(filename="test/files/pdfreader/scanned.pdf",
                               workdir=self.datadir,
                               ocr_lang="swe")

        # assert that a hOCR file has been created
        self.assertTrue(os.path.exists(self.datadir + os.sep + "scanned.hocr.html"))

        # assert that we have two pages
        self.assertEqual(2, len(reader))

        # assert that first element in the first textbox in the first
        # page corresponds to the first bbox
        self.assertEqual("Regeringens ", str(reader[0][0][0]))
        self.assertEqual(159, reader[0][0][0].top)
        self.assertEqual(129, reader[0][0][0].left)
        self.assertEqual(72, reader[0][0][0].height)
        self.assertEqual(400, reader[0][0][0].width)

        # assert that the <s>third</s>fifth textbox (which has mostly
        # normal text) is rendered correctly (note that we have a
        # couple of OCR errors).
        # self.assertEqual("Regeringen föreslår riksdagen att anta de förslag som har tagits. upp i bifogade utdrag ur regeringsprotokollet den 31 oktober l99l.", util.normalize_space(str(reader[0][3])))
        self.assertEqual("Regeringen föreslår riksdagen att anta de förslag som har tagits. upp i", util.normalize_space(str(reader[0][5])))
        

    def test_fallback_ocr(self):
        try:
            # actually running tesseract takes ages -- for day-to-day
            # testing we can just as well use the canned hocr.html
            # files that _copy_sample fixes for us.
            if not os.environ.get("FERENDA_TEST_TESSERACT"):
                raise errors.ExternalCommandError
            reader = PDFReader(filename="test/files/pdfreader/scanned-ecma-99.pdf",
                               workdir=self.datadir,
                               images=False)
        except errors.ExternalCommandError:
            self._copy_sample()
            reader = PDFReader(filename="test/files/pdfreader/scanned-ecma-99.pdf",
                               workdir=self.datadir,
                               images=False)

        self.assertTrue(reader.is_empty())
        reader = PDFReader(filename="test/files/pdfreader/scanned-ecma-99.pdf",
                           workdir=self.datadir,
                           ocr_lang="eng")
        self.assertFalse(reader.is_empty())
        self.assertEqual(2, len(reader))
        self.assertEqual("EUROPEAN COMPUTER MANUFACTURERS ASSOCIATION",
                         util.normalize_space(str(reader[0][1])))

    # should test that defaultglue works as expected and that it
    # doesn't fuck up the raw textboxes
    def test_textboxes(self):
        try:
            reader = PDFReader(filename="test/files/pdfreader/sample.pdf",
                               workdir=self.datadir)
        except errors.ExternalCommandError:
            self._copy_sample()
            reader = PDFReader(filename="test/files/pdfreader/sample.pdf",
                               workdir=self.datadir)
        # There should be nine raw (nonempty) textboxes
        self.assertEqual(9, len(reader[0]))
        self.assertEqual("This is a paragraph that spans three lines. These "
                         "should be treated as three ", str(reader[0][2]))
        # But only five logical paragraphs, according to the default
        # glue function.
        tbs = list(reader.textboxes())
        self.assertEqual(5, len(tbs))
        self.assertEqual(63, tbs[2].height)  # lineheight is 21, three
                                             # lines == 63
        self.assertEqual(602, tbs[2].width)  # max width of the three
                                             # lines is 602

        # the <b> tag will be present even though the font used for
        # this paragraph is Cambria-Bold
        self.assertEqual("This is a paragraph that is set entirely in bold. "
                         "Pdftohtml will create a <b> element for the text in "
                         "this paragraph. ", str(tbs[3]))
        self.assertEqual(1, len(tbs[3]))  # should only contain a
                                          # single TextElement, not a
                                          # leading empty TE
        self.assertEqual('b', tbs[3][0].tag)

        # the final paragraph should contain only two textelements
        self.assertEqual('b', tbs[4][0].tag)
        self.assertEqual(None, tbs[4][1].tag)
        self.assertEqual(2, len(tbs[4]))
        
        
        # make sure no actual textboxes were harmed in the process
        self.assertEqual("This is a paragraph that spans three lines. These "
                         "should be treated as three ", str(reader[0][2]))
        self.assertEqual(9, len(reader[0]))
