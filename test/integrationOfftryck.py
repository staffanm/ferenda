# -*- coding: utf-8 -*-
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
from builtins import *

import sys
import os

from ferenda.compat import unittest
from ferenda import PDFReader, PDFAnalyzer
from lxml import etree

# SUT
from ferenda.sources.legal.se import Offtryck


class Utils(object):
    def _f(self, xmlstr):
        # parse a number of fontspecs
        root = etree.fromstring(xmlstr)
        self.pdfreader._parse_xml_add_fontspec(root, {}, self.pdfreader.fontspec)

    def _p(self, xmlstr):
        root = etree.fromstring(xmlstr)
        return self.pdfreader._parse_xml_make_textbox(root, None, None, None, None)

class TestGlue(unittest.TestCase, Utils):
    scanned_source = False

    def setUp(self):
        # create a mock analyzer
        analyzer = PDFAnalyzer(None)
        analyzer.scanned_source = self.scanned_source
        self.gluefunc = Offtryck().get_gluefunc('basefile', analyzer)
        self.pdfreader = PDFReader()
        self.pdfreader.fontspec = {}
        self.pdfreader._textdecoder = lambda x, y: x
        self.pdfreader._textdecoder.fontspec = lambda x: x
        
    def test_basic_glue(self):
        self._f('<fontspec id="2" size="14" family="MAMMBB+TT5Eo00" color="#000000"/>')
        prevbox = self._p('<text top="288" left="85" width="468" height="17" font="2">Det är nu hög tid att göra en kraftsamling för informationsförsörj-</text>')
        nextbox = self._p('<text top="307" left="85" width="252" height="17" font="2">ningen till forskning och utbildning.</text>')
        self.assertTrue(self.gluefunc(prevbox, nextbox, prevbox))

    def test_basic_noglue_header(self):
        self._f('<fontspec id="1" size="26" family="MAMLPM+TT5Co00" color="#000000"/>')
        self._f('<fontspec id="2" size="14" family="MAMMBB+TT5Eo00" color="#000000"/>')
        prevbox = self._p('<text top="84" left="85" width="206" height="32" font="1">Sammanfattning </text>')
        nextbox = self._p('<text top="288" left="85" width="468" height="17" font="2">Det är nu hög tid att göra en kraftsamling för informationsförsörj-</text>')
        self.assertFalse(self.gluefunc(prevbox, nextbox, prevbox))


    def test_unreliable_fontspec(self):
        # the textbox marked as having font="6" is really font="2"
        self._f('<fontspec id="2" size="14" family="MAMMBB+TT5Eo00" color="#000000"/>')
        self._f('<fontspec id="6" size="14" family="MAPPGJ+TT9Eo00" color="#000000"/>')
        textbox = self._p('<text top="288" left="85" width="468" height="17" font="2">Det är nu hög tid att göra en kraftsamling för informationsförsörj-</text>')
        prevbox = self._p('<text top="307" left="85" width="252" height="17" font="2">ningen till forskning och utbildning.</text>')
        self.assertTrue(self.gluefunc(textbox, prevbox, textbox))
        textbox = textbox + prevbox
        nextbox = self._p('<text top="304" left="337" width="220" height="21" font="6"><i> </i>Den tekniska utvecklingen går </text>')
        self.assertTrue(self.gluefunc(textbox, nextbox, prevbox))
        textbox = textbox + nextbox
        prevbox = nextbox
        nextbox = self._p('<text top="327" left="85" width="472" height="17" font="2">snabbt, och den vetenskapliga publiceringen finner nya vägar. Detta </text>')
        self.assertTrue(self.gluefunc(textbox, nextbox, prevbox))
        
    def test_unreliable_fontspec_2(self):
        # the textbox marked as having font="9" is really font="2"
        self._f('<fontspec id="6" size="14" family="ABCDEE+OrigGarmnd BT" color="#000000"/>')
        self._f('<fontspec id="9" size="14" family="ABCDEE+TradeGothic,Bold" color="#000000"/>')
        prevbox = self._p('<text top="384" left="85" width="468" height="20" font="9"><b>1 §</b>    Syftet med denna lag är att möjliggöra personuppgiftsbehand-</text>')
        nextbox = self._p('<text top="405" left="85" width="472" height="20" font="6">ling  för  forskningsändamål  samtidigt  som  den  enskildes  fri-  och </text>')
        self.assertTrue(self.gluefunc(prevbox, nextbox, prevbox))

    def test_hanging_indent_header(self):
        # a common formatting for numbered headers is
        # 1     Förslag till
        #       lag om ändring i lagen (...) om blahonga
        #       foo bar potrzebie
        #
        # we should glue these textboxes together
        self._f('<fontspec id="4" size="16" family="MBBGJA+TT88o00" color="#000000"/>')
        firstbox = self._p('<text top="287" left="85" width="153" height="17" font="4">1 Förslag </text>')
        prevbox = self._p('<text top="287" left="201" width="64" height="17" font="4">till </text>')
        textbox = firstbox + prevbox
        nextbox = self._p('<text top="307" left="139" width="333" height="17" font="4">lag om ändring av lagen (1993:1392) om </text>')
        self.assertTrue(self.gluefunc(textbox, nextbox, prevbox))
        afternextbox = self._p('<text top="326" left="139" width="218" height="17" font="4">pliktexemplar av dokument </text>')
        textbox = textbox + nextbox
        self.assertTrue(self.gluefunc(textbox, afternextbox, nextbox))

    def test_hanging_indent_paragraphs(self):
        # commmon formattting for two normal paragraps is
        # 
        # Lorem ipsum dolor sit amet, consectetur adipiscing
        # elit. Donec interdum ac orci eu sodales.
        #    Sed placerat urna nunc, vel ullamcorper nibh pretium
        # vitae. In viverra nisi nec orci molestie cursus.
        # 
        # We shouldn't glue these together
        self._f('<fontspec id="3" size="14" family="Times New Roman" color="#000000"/>')
        p1box1 = self._p('<text top="428" left="106" width="429" height="15" font="3">Inom kort förväntas EU besluta om en förordning som utgör en ny </text>')
        p1box2 = self._p('<text top="447" left="106" width="428" height="15" font="3">personuppgiftsbehandling på plats när förordningen börjar tillämpas. </text>')
        p2box = self._p('<text top="466" left="128" width="129" height="15" font="3">Utredaren ska bl.a. </text>')
        self.assertFalse(self.gluefunc(p1box1 + p1box2, p2box, p1box2))

class TestDecodeAndGlue(unittest.TestCase, Utils):

    def setUp(self):
        # create a mock analyzer
        analyzer = PDFAnalyzer(None)
        analyzer.scanned_source = False
        self.gluefunc = Offtryck().get_gluefunc('basefile', analyzer)
        self.pdfreader = PDFReader()
        self.pdfreader.fontspec = {}
        from ferenda.sources.legal.se.decoders import OffsetDecoder20
        self.pdfreader._textdecoder = OffsetDecoder20()

    def test_hanging_indent_paragraphs_with_italics(self):
        self._f('<fontspec id="0" size="16" family="Times-Roman" color="#000000"/>')
        self._f('<fontspec id="3" size="16" family="EENIOA+Times.New.Roman.Kursiv0104" color="#000000"/>')
        self.pdfreader.fontspec[3]["encoding"] = "Custom"
        self.pdfreader.fontspec[0]["encoding"] = "WinAnsi"
        prevbox = self._p('<text top="498" left="106" width="531" height="24" font="3"><i>2IKSPOLISSTYRELSEN </i>har föreslagit att syftet enligt EG-direktivet att</text>')
        nextbox = self._p('<text top="525" left="85" width="553" height="17" font="0">åstadkomma ett fritt flöde av personuppgifter mellan medlemsstaterna i</text>')
        self.assertTrue(self.gluefunc(prevbox, nextbox, prevbox))

