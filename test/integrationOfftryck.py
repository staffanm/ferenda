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


class TestGlue(unittest.TestCase):
    scanned_source = False

    def setUp(self):
        # create a mock analyzer
        analyzer = PDFAnalyzer(None)
        analyzer.scanned_source = self.scanned_source
        self.gluefunc = Offtryck().get_gluefunc('basefile', analyzer)
        self.pdfreader = PDFReader()
        self.pdfreader.fontspec = {}
        self.pdfreader._textdecoder = lambda x, y: x
        
    def _f(self, xmlstr):
        # parse a number of fontspecs
        root = etree.fromstring(xmlstr)
        self.pdfreader._parse_xml_add_fontspec(root, {}, self.pdfreader.fontspec)

    def _p(self, xmlstr):
        root = etree.fromstring(xmlstr)
        return self.pdfreader._parse_xml_make_textbox(root, None, None, None, None)
        

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
        self.assertTrue(self.gluefunc(prevbox, prevbox, textbox))
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
        
