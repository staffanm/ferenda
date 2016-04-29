# -*- coding: utf-8 -*-
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
from builtins import *

# NOTE: This unittest requires that the pdftohtml and related binaries
# are available for calling, making this not a pure unittest. If they
# are not, the tests fall back to using canned result files.

from bz2 import BZ2File
import os
import shutil
import tempfile

from lxml import etree

from ferenda.compat import unittest
from ferenda import errors, util
from ferenda.testutil import FerendaTestCase
from ferenda.elements import LinkSubject

# SUT
from ferenda import PDFReader
from ferenda.pdfreader import Textbox, Textelement

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
        # page corresponds to the first bbox, scaled by the
        # pixel/point scaling factor.
        self.assertEqual("Regeringens ", str(reader[0][0][0]))
        self.assertEqual(38, reader[0][0][0].top)
        self.assertEqual(31, reader[0][0][0].left)
        self.assertEqual(18, reader[0][0][0].height)
        self.assertEqual(96, reader[0][0][0].width)

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


    def test_custom_encoding(self):
        try:
            reader = PDFReader(filename="test/files/pdfreader/custom-encoding.pdf",
                               workdir=self.datadir)
        except errors.ExternalCommandError as e:
            print("test_custom_encoding got ExternalCommandError %s, copying sample and retrying" % e)
            self._copy_sample()
            reader = PDFReader(filename="test/files/pdfreader/custom-encoding.pdf",
                               workdir=self.datadir)
        # textbox 5 and 6 uses a font with a custom encoding, make
        # sure that this is properly decoded.
        tbs = list(reader.textboxes())
        self.assertEqual("Göran Persson", str(tbs[5]))
        self.assertEqual("Bosse Ringholm", str(tbs[6]))
        self.assertEqual("(Finansdepartementet)", str(tbs[7]))


class AsXHTML(unittest.TestCase, FerendaTestCase):

    def _test_asxhtml(self, want, body):
        body._fontspec = {0: {'family': 'Times'},
                          1: {'family': 'Comic sans', 'encoding': 'Custom'}}
        got = etree.tostring(body.as_xhtml(None), pretty_print=True)
        self.assertEqualXML(want, got)

    def test_basic(self):
        body = Textbox([Textelement("test", tag=None)],
                       top=0, left=0, width=100, height=100, fontid=0)
        want = """
<p xmlns="http://www.w3.org/1999/xhtml" class="textbox" style="top: 0px, left: 0px, height: 100px, width: 100px">test</p>
"""
        self._test_asxhtml(want, body)

    def test_elements_with_tags(self):
        body = Textbox([Textelement("normal", tag=None),
                        Textelement("bold", tag="b"),
                        Textelement("italic", tag="i"),
                        Textelement("both", tag="bi")
        ], top=0, left=0, width=100, height=100, fontid=0)
        want = """
<p xmlns="http://www.w3.org/1999/xhtml" class="textbox" style="top: 0px, left: 0px, height: 100px, width: 100px">normal<b>bold</b><i>italic</i><b><i>both</i></b></p>
"""
        self._test_asxhtml(want, body)


    def test_leading_tag(self):
        body = Textbox([Textelement("bold", tag="b"),
                        Textelement("normal", tag=None),
        ], top=0, left=0, width=100, height=100, fontid=0)
        want = """
<p xmlns="http://www.w3.org/1999/xhtml" class="textbox" style="top: 0px, left: 0px, height: 100px, width: 100px"><b>bold</b>normal</p>
"""
        self._test_asxhtml(want, body)
                        
    def test_tag_merge(self):
        body = Textbox([Textelement("identical ", tag=None),
                        Textelement("tags ", tag=None),
                        Textelement("should ", tag="b"),
                        Textelement("merge", tag="b"),
        ], top=0, left=0, width=100, height=100, fontid=0)
        want = """
<p xmlns="http://www.w3.org/1999/xhtml" class="textbox" style="top: 0px, left: 0px, height: 100px, width: 100px">identical tags <b>should merge</b></p>
"""
        self._test_asxhtml(want, body)
                        
    def test_other_elements(self):
        body = Textbox([Textelement("plaintext ", tag=None),
                        LinkSubject("link", uri="http://example.org/",
                                    predicate="dcterms:references"),
                        " raw string"
        ], top=0, left=0, width=100, height=100, fontid=0)
        want = """
<p xmlns="http://www.w3.org/1999/xhtml" class="textbox" style="top: 0px, left: 0px, height: 100px, width: 100px">plaintext <a href="http://example.org/" rel="dcterms:references">link</a> raw string</p>
"""
        self._test_asxhtml(want, body)

        # remove the last str so that the linksubject becomes the last item
        body[:] = body[:-1]
        want = """
<p xmlns="http://www.w3.org/1999/xhtml" class="textbox" style="top: 0px, left: 0px, height: 100px, width: 100px">plaintext <a href="http://example.org/" rel="dcterms:references">link</a></p>
"""
        self._test_asxhtml(want, body)


    def test_encoded_element(self):
        body = Textbox([Textelement("*|UDQ\x033HUVVRQ", tag=None)
        ], top=0, left=0, width=100, height=100, fontid=1, pdf=PDFReader())
        want = """
<p xmlns="http://www.w3.org/1999/xhtml" class="textbox" style="top: 0px, left: 0px, height: 100px, width: 100px">Göran Persson</p>
"""
        self._test_asxhtml(want, body)

        body = Textbox([Textelement("Ansvarig: ", tag=None),
                        Textelement("*|UDQ\x033HUVVRQ", tag="i")
        ], top=0, left=0, width=100, height=100, fontid=1)
        want = """
<p xmlns="http://www.w3.org/1999/xhtml" class="textbox" style="top: 0px, left: 0px, height: 100px, width: 100px">Ansvarig: <i>Göran Persson</i></p>
"""
