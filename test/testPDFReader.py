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
from io import BytesIO

from lxml import etree

from ferenda.compat import unittest
from ferenda import errors, util
from ferenda.testutil import FerendaTestCase
from ferenda.elements import serialize, LinkSubject

# SUT
from ferenda import PDFReader
from ferenda.pdfreader import Textbox, Textelement, BaseTextDecoder, LinkedTextelement

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
        self.assertEqual(47, reader[0][0][0].top)
        self.assertEqual(38, reader[0][0][0].left)
        self.assertEqual(21, reader[0][0][0].height)
        self.assertEqual(118, reader[0][0][0].width)

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


class Decoding(unittest.TestCase):

    def setUp(self):
        self.maxDiff = None
        self.datadir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.datadir)

    def _copy_sample(self):
        for fname in os.listdir("test/files/pdfreader/intermediate"):
            shutil.copy("test/files/pdfreader/intermediate/%s" % fname,
                         self.datadir + os.sep + fname)

    def test_1d_encoding(self):
        # since version 0.57.0, pdftohtml silently discards some
        # invalid characters from output. Problem is, we have a font
        # with custom encoding, in wich 0x03 (ctrl-c) represent
        # space. pdftohtml 0.57+ drops this character before we get a
        # chance to read and decode it. Until we can do something
        # about recent pdftohtml versions, we make sure that this test
        # uses pre-generated output from an older version.
        from ferenda.sources.legal.se.decoders import OffsetDecoder1d
        #try:
        #    from pudb import set_trace; set_trace()
        #    reader = PDFReader(filename="test/files/pdfreader/custom-encoding.pdf",
        #                       workdir=self.datadir,
        #                       textdecoder=OffsetDecoder1d())
        #except errors.ExternalCommandError as e:
        #    print("test_custom_encoding got ExternalCommandError %s, copying sample and retrying" % e)
        self._copy_sample()
        reader = PDFReader(filename="test/files/pdfreader/custom-encoding.pdf",
                           workdir=self.datadir,
                           textdecoder=OffsetDecoder1d())
        # textbox 5 and 6 uses a font with a custom encoding, make
        # sure that this is properly decoded.
        tbs = list(reader.textboxes())
        self.assertEqual("Göran Persson", str(tbs[5]))
        self.assertEqual("Bosse Ringholm", str(tbs[6]))
        self.assertEqual("(Finansdepartementet)", str(tbs[7]))


    def test_20_encoding(self):
        # for this file, we don't even have a real PDF file, just some
        # copypasted excerpts from an intermediate XML file
        from ferenda.sources.legal.se.decoders import OffsetDecoder20
        self._copy_sample()
        reader = PDFReader(filename="test/files/pdfreader/prop_1997_98_44.pdf",
                           workdir=self.datadir,
                           textdecoder=OffsetDecoder20(kommittenamn="Datalagskommittén"))
        page = reader[0]
        self.assertEqual("Personuppgiftslag", str(page[0]))     # unencoded
        self.assertEqual("Laila Freivalds", str(page[1]))       # basic encoding
        self.assertEqual("Pierre Schori", str(page[2]))         # basic encoding
        self.assertEqual("Härigenom föreskrivs1 följande.", str(page[3])) # footnote glueing
        self.assertEqual(241, page[3].width)
        self.assertEqual(326, page[3].right)
        self.assertEqual("Härigenom föreskrivs", page[3][0])
        self.assertEqual("1", page[3][1])
        self.assertEqual("sup", page[3][1].tag)
        self.assertEqual(" följande.", page[3][2])
        self.assertEqual("Allmänna bestämmelser", str(page[4])) # basic encoding, 
        self.assertEqual("Times.New.Roman.Fet0100", page[4].font.family) # font should stay
        self.assertEqual(None, page[4][0].tag)                  # no tag (font family tells it's bold)
        self.assertEqual("Syftet med lagen", str(page[5]))      # basic encoding, 
        self.assertEqual("Times-Roman", page[5].font.family)    # font should be changed to default
        self.assertEqual("i", page[5][0].tag)                   # since this element is <i>, the main font family should not be an italic
        self.assertEqual("1 § Syftet med denna lag är att skydda människor mot att deras personli-", str(page[6])) # non-marked up bold-then-normal textelement
        self.assertEqual("Times-Roman", page[6].font.family)
        self.assertEqual("1 §", page[6][0])
        self.assertEqual("b", page[6][0].tag)
        self.assertEqual(None, page[6][1].tag)

        self.assertEqual("Personuppgiftsansvarig Den som ensam eller tillsammans med andra", str(page[8])) # marked up italic/encoded textelement followed by normal/nonencoded
        self.assertEqual("Personuppgiftsansvarig ", page[8][0])
        self.assertEqual("i", page[8][0].tag)
        self.assertEqual(None, page[8][1].tag)


        self.assertEqual("Regeringens bedömning: En lagstiftning som reglerar själva hante-", str(page[14])) # non-marked up bold-then-normal textelement, fixed string
        self.assertEqual("Times-Roman", page[14].font.family)
        self.assertEqual("Regeringens bedömning:", page[14][0])
        self.assertEqual("b", page[14][0].tag)
        self.assertEqual(None, page[14][1].tag)

        self.assertEqual("Datalagskommitténs bedömning överensstämmer med regeringens.", str(page[16])) # non-marked up bold-then-normal textelement, fixed string
        self.assertEqual("Times-Roman", page[16].font.family)
        self.assertEqual("Datalagskommitténs bedömning", page[16][0])
        self.assertEqual("b", page[16][0].tag)
        self.assertEqual(None, page[16][1].tag)

        self.assertEqual("Remissinstanserna: Kammarrätten i Göteborg anser att den registre-", str(page[36])) # non-marked up bold-then-normal textelement, fixed string, followed by encoded italics, forcing us to drop back to the default decoding strategy in OffsetDecoder1d
        self.assertEqual("Times-Roman", page[36].font.family)
        self.assertEqual("Remissinstanserna:", page[36][0])
        self.assertEqual("b", page[36][0].tag)
        self.assertEqual(None, page[36][1].tag)
        self.assertEqual("Kammarrätten i Göteborg ", page[36][2])
        self.assertEqual("i", page[36][2].tag)
        self.assertEqual(None, page[36][3].tag)

        self.assertEqual("Landsorganisationen i Sverige (LO)", page[39][0]) # ")" is encoded as TAB
        self.assertEqual("i", page[39][0].tag)
        
    def test_autodetect_encoding(self):
        from ferenda.sources.legal.se.decoders import DetectingDecoder
        self._copy_sample()
        reader = PDFReader(filename="test/files/pdfreader/multiple-encodings.pdf",
                           workdir=self.datadir,
                           textdecoder=DetectingDecoder())
        page = reader[0]
        self.assertEqual("Detta är helt vanlig icke-kodad text på svenska.",
                         str(page[0]))     # unencoded (but marked as Custom encoding)
        self.assertEqual("mellan Konungariket Sveriges regering och Konungariket Danmarks",
                         str(page[1]))       # basic encoding (0x1d)
        self.assertEqual("Skälen för regeringens bedömning och förslag",
                         str(page[2]))         # other encoding (0x20
        


class ParseXML(unittest.TestCase):
    maxDiff = None
    
    def _parse_xml(self, xmlfrag, decoding_class=BaseTextDecoder):
        pdf = PDFReader(pages=True)
        pdf.fontspec = {}
        pdf._textdecoder = decoding_class()
        xml = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE pdf2xml SYSTEM "pdf2xml.dtd">
<pdf2xml producer="poppler" version="0.24.3">
<page number="1" position="absolute" top="0" left="0" height="750" width="500">
%s
</page>
</pdf2xml>""" % xmlfrag
        xmlfp = BytesIO(xml.encode("utf-8"))
        xmlfp.name = "dummy.xml"
                                                     
        pdf._parse_xml(xmlfp)
        return pdf


    def test_grandchildren(self):
        pdf = self._parse_xml("""
<fontspec id="12" size="11" family="TimesNewRomanPS-BoldItalicMT" color="#000000"/>
<text top="270" left="278" width="450" height="12" font="12">
   <i><b>52 par</b> Sanktionsavgiften ska </i>
</text>
""")
        textbox = pdf[0][0]
        self.assertIsInstance(textbox, Textbox)
        self.assertEqual(len(textbox), 2)
        self.assertEqual(textbox[0].tag, "ib")
        self.assertEqual(textbox[0], "52 par")
        self.assertEqual(textbox[1].tag, "i")
        self.assertEqual(textbox[1], " Sanktionsavgiften ska ")

    def test_whitespace_normalization(self):
        pdf = self._parse_xml("""
<fontspec id="0" size="21" family="CCQUSK+Calibri-Bold" color="#345a8a"/>
<text top="146" left="135" width="155" height="29" font="0"><b>Document	  title	  </b></text>""")
        self.assertEqual("Document title ", str(pdf[0][0]))


    def test_multiple_textelements(self):
        pdf = self._parse_xml("""
<fontspec id="1" size="5" family="X" color="#00000"/>
<text top="0" left="0" width="23" height="13" font="1"><b>foo</b> <b>bar</b></text>
""")
        self.assertEqual("foobar", str(pdf[0][0]))
        # test that Textelement.__add__ inserts a space correctly
        self.assertEqual('<Textelement tag="b">foo bar</Textelement>',
                         serialize(pdf[0][0][0] + pdf[0][0][1]).strip())
        want = """
<Textbox bottom="13" fontid="1" height="13" left="0" lines="0" right="23" top="0" width="23">
  <Textelement tag="b">foo</Textelement>
  <Textelement tag="b">bar</Textelement>
</Textbox>
"""
        self.assertEqual(want[1:], serialize(pdf[0][0]))

        # 2nd test, with leading non-tagged Textelement
        pdf = self._parse_xml("""
<fontspec id="0" size="5" family="X" color="#00000"/>
<text top="374" left="508" width="211" height="14" font="0">näringsidkaren <i>en</i> <i>varning. En var-</i></text>
""")
        want = """
<Textbox bottom="388" fontid="0" height="14" left="508" lines="0" right="719" top="374" width="211">
  <Textelement>näringsidkaren </Textelement>
  <Textelement tag="i">en</Textelement>
  <Textelement tag="i">varning. En var-</Textelement>
</Textbox>
"""
        self.assertEqual(want[1:], serialize(pdf[0][0]))

        
    def test_footnote(self):
        pdf = self._parse_xml("""
<fontspec id="7" size="14" family="TROYEM+OriginalGaramondBT-Roman" color="#000000"/>
<fontspec id="15" size="7" family="TROYEM+OriginalGaramondBT-Roman" color="#000000"/>
<text top="830" left="85" width="241" height="20" font="7">bindande verkan för det allmänna.</text>
<text top="829" left="327" width="5" height="12" font="15">7</text>
<text top="830" left="332" width="227" height="20" font="7">Bestämmelsen kan således inte </text>""")
        want = """
<Page height="750" number="1" width="500">
  <Textbox bottom="850" fontid="7" height="21" left="85" lines="-2" right="559" top="829" width="474">
    <Textelement>bindande verkan för det allmänna.</Textelement>
    <Textelement tag="sup">7</Textelement>
    <Textelement>Bestämmelsen kan således inte </Textelement>
  </Textbox>
</Page>
"""
        self.assertEqual(want[1:],
                         serialize(pdf[0]))


    def test_footnote_lineending(self):
        pdf = self._parse_xml("""
<fontspec id="0" size="13" family="GGKKGC+TimesNewRomanPSMT" color="#000000"/>
<fontspec id="4" size="13" family="GGKKID+TimesNewRomanPS-ItalicMT" color="#000000"/>
<fontspec id="7" size="7" family="GGKKGC+TimesNewRomanPSMT" color="#000000"/>
<text top="161" left="291" width="401" height="17" font="0">Härigenom föreskrivs i fråga om mervärdesskattelagen (1994:200)</text>
<text top="159" left="692" width="5" height="11" font="7">7</text>
<text top="161" left="697" width="4" height="17" font="0"> </text>
<text top="178" left="291" width="249" height="17" font="4"><i>dels</i> att 1 kap. 12 § ska upphöra att gälla, </text>
""")
        want = """
<Page height="750" number="1" width="500">
  <Textbox bottom="178" fontid="0" height="19" left="291" lines="-1" right="697" top="159" width="406">
    <Textelement>Härigenom föreskrivs i fråga om mervärdesskattelagen (1994:200)</Textelement>
    <Textelement tag="sup">7</Textelement>
  </Textbox>
  <Textbox bottom="195" fontid="4" height="17" left="291" lines="0" right="540" top="178" width="249">
    <Textelement tag="i">dels</Textelement>
    <Textelement> att 1 kap. 12 § ska upphöra att gälla, </Textelement>
  </Textbox>
</Page>
"""
        self.assertEqual(want[1:],
                         serialize(pdf[0]))


    def test_linked_footnote(self):
        pdf = self._parse_xml("""
<fontspec id="7" size="14" family="TROYEM+OriginalGaramondBT-Roman" color="#000000"/>
<fontspec id="15" size="7" family="TROYEM+OriginalGaramondBT-Roman" color="#000000"/>
<text top="830" left="85" width="241" height="20" font="7">bindande verkan för det allmänna.</text>
<text top="829" left="327" width="5" height="12" font="15"><a href="unik-kunskap-genom-registerforskning-sou-201445.html#120">7</a></text>
<text top="830" left="332" width="227" height="20" font="7"><a href="unik-kunskap-genom-registerforskning-sou-201445.html#120"> </a>Bestämmelsen kan således inte </text>
""")
        want = """
<Page height="750" number="1" width="500">
  <Textbox bottom="850" fontid="7" height="21" left="85" lines="-2" right="559" top="829" width="474">
    <Textelement>bindande verkan för det allmänna.</Textelement>
    <LinkedTextelement tag="s" uri="unik-kunskap-genom-registerforskning-sou-201445.html#120">7</LinkedTextelement>
    <LinkedTextelement uri="unik-kunskap-genom-registerforskning-sou-201445.html#120"> </LinkedTextelement>
    <Textelement>Bestämmelsen kan således inte </Textelement>
  </Textbox>
</Page>
"""
        self.assertEqual(want[1:],
                         serialize(pdf[0]))
        

    def test_footnote_footer(self):
        pdf = self._parse_xml("""
<fontspec id="7" size="14" family="TROYEM+OriginalGaramondBT-Roman" color="#000000"/>
<fontspec id="15" size="7" family="TROYEM+OriginalGaramondBT-Roman" color="#000000"/>
<fontspec id="16" size="10" family="TROYEM+OriginalGaramondBT-Roman" color="#000000"/>
<fontspec id="17" size="5" family="TROYEM+OriginalGaramondBT-Roman" color="#000000"/>
<text top="849" left="85" width="472" height="20" font="7">ligga till grund för några individuella rättigheter. I 2 kap. 4 och 5 §§ </text>
<text top="891" left="85" width="4" height="9" font="17">7</text>
<text top="891" left="89" width="258" height="15" font="16"> Prop. 1975/76:209 s. 128, prop. 2009/10:80 s. 173. </text>
""")
        want = """
<Page height="750" number="1" width="500">
  <Textbox bottom="869" fontid="7" height="20" left="85" lines="0" right="557" top="849" width="472">
    <Textelement>ligga till grund för några individuella rättigheter. I 2 kap. 4 och 5 §§ </Textelement>
  </Textbox>
  <Textbox bottom="906" fontid="16" height="15" left="85" lines="-1" right="347" top="891" width="262">
    <Textelement tag="sup">7</Textelement>
    <Textelement> Prop. 1975/76:209 s. 128, prop. 2009/10:80 s. 173. </Textelement>
  </Textbox>
</Page>
"""
        self.assertEqual(want[1:],
                         serialize(pdf[0]))


    def test_italic_superscript_unreliable_font(self):
        # the thing here is that font 2 and font 7 really has the same
        # font family.
        # ferenda.sources.legal.se.decoders.OffsetDecoder1d knows this
        # since it's hard-coded. The main problem is that the
        # OffsetDecoder1d.fontspecs methods (that aliases the fonts)
        # is run after PDFReader._parse_xml. Maybe we need to make
        # ._parse_xml call into the given textdecoder for each
        # fontspec tag it encounters?
        from ferenda.sources.legal.se.decoders import OffsetDecoder1d
        pdf = self._parse_xml("""
<fontspec id="2" size="14" family="MAMMBB+TT5Eo00" color="#000000"/>
<fontspec id="7" size="7" family="MBAAAC+TTA1o00" color="#000000"/>
<text top="552" left="340" width="372" height="17" font="2">intressant om 50 år föreslås att projektet Kulturarw</text>
<text top="549" left="712" width="5" height="13" font="7"><i>3</i></text>
<text top="552" left="717" width="98" height="17" font="2"> får fortsätta </text>
        """, OffsetDecoder1d)
        want = """
<Page height="750" number="1" width="500">
  <Textbox bottom="569" fontid="2" height="20" left="340" lines="-2" right="815" top="549" width="475">
    <Textelement>intressant om 50 år föreslås att projektet Kulturarw</Textelement>
    <Textelement tag="is">3</Textelement>
    <Textelement> får fortsätta </Textelement>
  </Textbox>
</Page>
"""
        self.assertEqual(want[1:],
                         serialize(pdf[0]))

    def test_links(self):
        pdf = self._parse_xml("""
<fontspec id="6" size="14" family="CNMEID+TradeGothic,Bold" color="#000000"/>
<fontspec id="8" size="14" family="CNMEIF+OrigGarmndBT" color="#000000"/>
<text top="310" left="81" width="10" height="20" font="6"><a href="nya-avfallsregler-ds-200937.html#7"><b>1</b></a></text>
<text top="384" left="81" width="21" height="20" font="8"><a href="nya-avfallsregler-ds-200937.html#9">2.1</a></text>

""")
        page = pdf[0]
        self.assertIsInstance(page[0][0], LinkedTextelement)
        self.assertEqual("1", page[0][0])
        self.assertEqual("b", page[0][0].tag)
        self.assertEqual("nya-avfallsregler-ds-200937.html#7", page[0][0].uri)

        self.assertIsInstance(page[1][0], LinkedTextelement)
        self.assertEqual("2.1", page[1][0])
        self.assertEqual(None, page[1][0].tag)
        self.assertEqual("nya-avfallsregler-ds-200937.html#9", page[1][0].uri)


    def test_comment(self):
        pdf = self._parse_xml("""
<fontspec id="1" size="11" family="TimesNewRomanPS" color="#000000"/>
<text top="270" left="278" width="450" height="12" font="1">First line</text>
<!-- comments like this won't appear in real pdf2xml output, but might appear
     in test cases -->
<text top="290" left="278" width="450" height="12" font="1">Second line</text>
""")
        want = """
<Page height="750" number="1" width="500">
  <Textbox bottom="282" fontid="1" height="12" left="278" lines="0" right="728" top="270" width="450">
    <Textelement>First line</Textelement>
  </Textbox>
  <Textbox bottom="302" fontid="1" height="12" left="278" lines="0" right="728" top="290" width="450">
    <Textelement>Second line</Textelement>
  </Textbox>
</Page>
"""
        self.assertEqual(want[1:],
                         serialize(pdf[0]))


    def test_empty(self):
        pdf = self._parse_xml("""
<fontspec id="3" size="11" family="TimesNewRomanPS" color="#000000"/>
<text top="686" left="148" width="4" height="18" font="3">
  <b> </b>
</text>
""")
        want = """
<Page height="750" number="1" width="500">
  <Textbox bottom="704" fontid="3" height="18" left="148" lines="0" right="152" top="686" width="4" />
</Page>
"""
        self.assertEqual(want[1:],
                         serialize(pdf[0]))
        

    def test_after_footnote_tag(self):
        # minimized version of Prop 2011/12:60 p 147. It seems to be
        # the empty italized textelement, combined with the
        # after_footnote context, that caused a crash
        pdf = self._parse_xml("""
	<fontspec id="0" size="12" family="Times New Roman" color="#000000"/>
	<fontspec id="4" size="12" family="Times New Roman,Italic" color="#000000"/>
	<fontspec id="9" size="7" family="Times New Roman" color="#000000"/>
<text top="63" left="283" width="37" height="13" font="0">20 a §</text>
<text top="60" left="320" width="5" height="9" font="9">4</text>
<text top="442" left="304" width="4" height="13" font="4"><i> </i></text>
<text top="460" left="306" width="41" height="13" font="4"><i>20 b § </i></text>
""")
        # make sure that empty element is removed completely
        want = """
<Page height="750" number="1" width="500">
  <Textbox bottom="76" fontid="0" height="16" left="283" lines="-1" right="325" top="60" width="42">
    <Textelement>20 a §</Textelement>
    <Textelement tag="sup">4</Textelement>
  </Textbox>
  <Textbox bottom="473" fontid="4" height="31" left="304" lines="0" right="347" top="442" width="43">
    <Textelement tag="i">20 b § </Textelement>
  </Textbox>
</Page>
"""
        self.assertEqual(want[1:], serialize(pdf[0]))
        

class AsXHTML(unittest.TestCase, FerendaTestCase):

    def _test_asxhtml(self, want, body):
        body._fontspec = {0: {'family': 'Times', 'size': '12'},
                          1: {'family': 'Comic sans', 'encoding': 'Custom'}}
        got = etree.tostring(body.as_xhtml(None), pretty_print=True)
        self.assertEqualXML(want, got)

    def test_basic(self):
        body = Textbox([Textelement("test", tag=None)],
                       top=0, left=0, width=100, height=100, fontid=0)
        want = """
<p xmlns="http://www.w3.org/1999/xhtml" class="textbox fontspec0" style="top: 0px; left: 0px; height: 100px; width: 100px">test</p>
"""
        self._test_asxhtml(want, body)

    def test_elements_with_tags(self):
        body = Textbox([Textelement("normal", tag=None),
                        Textelement("bold", tag="b"),
                        Textelement("italic", tag="i"),
                        Textelement("both", tag="bi")
        ], top=0, left=0, width=100, height=100, fontid=0)
        want = """
<p xmlns="http://www.w3.org/1999/xhtml" class="textbox fontspec0" style="top: 0px; left: 0px; height: 100px; width: 100px">normal<b>bold</b><i>italic</i><b><i>both</i></b></p>
"""
        self._test_asxhtml(want, body)


    def test_leading_tag(self):
        body = Textbox([Textelement("bold", tag="b"),
                        Textelement("normal", tag=None),
        ], top=0, left=0, width=100, height=100, fontid=0)
        want = """
<p xmlns="http://www.w3.org/1999/xhtml" class="textbox fontspec0" style="top: 0px; left: 0px; height: 100px; width: 100px"><b>bold</b>normal</p>
"""
        self._test_asxhtml(want, body)
                        
    def test_tag_merge(self):
        body = Textbox([Textelement("identical ", tag=None),
                        Textelement("tags ", tag=None),
                        Textelement("should ", tag="b"),
                        Textelement("merge", tag="b"),
        ], top=0, left=0, width=100, height=100, fontid=0)
        want = """
<p xmlns="http://www.w3.org/1999/xhtml" class="textbox fontspec0" style="top: 0px; left: 0px; height: 100px; width: 100px">identical tags <b>should merge</b></p>
"""
        self._test_asxhtml(want, body)
                        
    def test_other_elements(self):
        body = Textbox([Textelement("plaintext ", tag=None),
                        LinkSubject("link", uri="http://example.org/",
                                    predicate="dcterms:references"),
                        " raw string"
        ], top=0, left=0, width=100, height=100, fontid=0)
        want = """
<p xmlns="http://www.w3.org/1999/xhtml" class="textbox fontspec0" style="top: 0px; left: 0px; height: 100px; width: 100px">plaintext <a href="http://example.org/" rel="dcterms:references">link</a> raw string</p>
"""
        self._test_asxhtml(want, body)

        # remove the last str so that the linksubject becomes the last item
        body[:] = body[:-1]
        want = """
<p xmlns="http://www.w3.org/1999/xhtml" class="textbox fontspec0" style="top: 0px; left: 0px; height: 100px; width: 100px">plaintext <a href="http://example.org/" rel="dcterms:references">link</a></p>
"""
        self._test_asxhtml(want, body)


    def test_linkelements(self):
        body = Textbox([Textelement("normal", tag=None),
                        LinkedTextelement("link", uri="http://example.org/", tag=None),
                        Textelement("footnote marker", tag="sup"),
                        LinkedTextelement("linked footnote marker",
                                          uri="http://example.org/", tag="s")],
                       top=0, left=0, width=100, height=100, fontid=0)
        
        want = """
<p xmlns="http://www.w3.org/1999/xhtml" class="textbox fontspec0" style="top: 0px; left: 0px; height: 100px; width: 100px">normal<a href="http://example.org/">link</a><sup>footnote marker</sup><a href="http://example.org/"><sup>linked footnote marker</sup></a></p>
"""
        self._test_asxhtml(want, body)

    def test_superscripts(self):
        body = Textbox([Textelement("1", tag="sup"),
                        Textelement("2", tag="is"),
                        Textelement("3", tag="bis")],
                       top=0, left=0, width=100, height=100, fontid=0)
        want = """
<p xmlns="http://www.w3.org/1999/xhtml" class="textbox fontspec0" style="top: 0px; left: 0px; height: 100px; width: 100px"><sup>1</sup><i><sup>2</sup></i><b><i><sup>3</sup></i></b></p>
"""
        self._test_asxhtml(want, body)
                        

    def test_empty_removal(self):
        body = Textbox([LinkedTextelement("  ", uri="index.html#24", tag=None),
                        Textelement("23", tag=None)],
                       top=0, left=0, width=100, height=100, fontid=0)
        want = """
<p xmlns="http://www.w3.org/1999/xhtml" class="textbox fontspec0" style="top: 0px; left: 0px; height: 100px; width: 100px">23</p>
"""
        self._test_asxhtml(want, body)
                        

class Elements(unittest.TestCase):
    maxDiff = None
    def test_addboxes(self):
        box1 = Textbox([Textelement("hey ", tag=None)], fontid=None, top=0, left=0, width=50, height=10, lines=1)
        box2 = Textbox([Textelement("ho", tag=None)], fontid=None, top=0, left=50, width=40, height=10, lines=1)
        
        combinedbox = box1 + box2
        want = """
<Textbox bottom="10" fontid="0" height="10" left="0" lines="1" right="90" top="0" width="90">
  <Textelement>hey ho</Textelement>
</Textbox>
"""
        self.assertEqual(want[1:],
                         serialize(combinedbox))
        # make sure __iadd__ performs like __add__
        box1 += box2
        self.assertEqual(want[1:],
                         serialize(box1))
        

    def test_add_different_types(self):
        box1 = Textbox([Textelement("hey", tag=None)], fontid=None, top=0, left=0, width=50, height=10, lines=1)
        box2 = Textbox([LinkedTextelement("1", tag="s", uri="foo.html")], fontid=None, top=0, left=50, width=5, height=10, lines=1)
        combinedbox = box1 + box2
        want = """
<Textbox bottom="10" fontid="0" height="10" left="0" lines="1" right="55" top="0" width="55">
  <Textelement>hey</Textelement>
  <LinkedTextelement tag="s" uri="foo.html">1</LinkedTextelement>
</Textbox>
"""
        self.assertEqual(want[1:],
                         serialize(combinedbox))
        # make sure __iadd__ performs like __add__
        box1 += box2
        self.assertEqual(want[1:],
                         serialize(box1))
