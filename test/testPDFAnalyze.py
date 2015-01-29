# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import sys, os, tempfile, shutil
from lxml import etree
from ferenda.compat import unittest
if os.getcwd() not in sys.path: sys.path.insert(0,os.getcwd())

from bz2 import BZ2File
from ferenda import errors, util
from six import text_type as str

from ferenda import PDFReader

# SUT
from ferenda import PDFAnalyzer

class Analyze(unittest.TestCase):
    def setUp(self):
        self.maxDiff = None
        self.pdf = PDFReader(filename="test/files/pdfanalyze/lipsum.pdf",
                             workdir="test/files/pdfanalyze/")
        self.analyzer = PDFAnalyzer(self.pdf)

    def test_documents(self):
        self.assertEquals([(0,3)], self.analyzer.documents())

    def test_vcounters(self):
        vcounters = self.analyzer.count_vertical_margins(0, 3)
        self.assertEquals(set(vcounters.keys()),
                          set(('bottommargin', 'topmargin', 'pageheight')))
        self.assertEquals(max(vcounters['pageheight']), 1262)
        self.assertEquals(vcounters['bottommargin'][76], 22) # charcount of headers from 2 pages
        self.assertEquals(vcounters['topmargin'][1167], 3) # pagenumbers on 3 pages 
        
    def test_hcounters(self):
        hcounters = self.analyzer.count_horizontal_margins(0, 3)
        self.assertEquals(set(hcounters.keys()),
                          set(('leftmargin', 'rightmargin', 'leftmargin_even', 'rightmargin_even', 'pagewidth')))
        self.assertEquals(set(hcounters['leftmargin'].keys()), set((135, 775, 778))) # 775, 778 are pagenumbers on pg 1 + 3
        self.assertEquals(hcounters['leftmargin_even'].keys(), [108])
        self.assertEquals(hcounters['rightmargin'].most_common(1)[0][0], 784)

    def test_stylecounters(self):
        stylecounters = self.analyzer.count_styles(0, 3)
        self.assertEquals(dict(stylecounters['frontmatter_styles']),
                          {('Comic Sans MS', 14): 2150,
                           ('Cambria,Bold', 14): 68,
                           ('Cambria,Bold', 17): 64,
                           ('Cambria', 37): 55,
                           (u'Cambria,Bold', 19): 28})
        self.assertEquals(dict(stylecounters['rest_styles']),
                          {('Comic Sans MS', 14): 5922,
                           ('Cambria,Bold', 14): 133,
                           ('Cambria,Bold', 17): 128,
                           ('Cambria,Bold', 19): 61})

    @unittest.expectedFailure
    def test_analyze_hmargins(self):
        hcounters = self.analyzer.count_horizontal_margins(0, 3)
        hmetrics = self.analyzer.analyze_horizontal_margins(hcounters)
        self.assertEquals(hmetrics, {})

    @unittest.expectedFailure
    def test_analyze_vmargins(self):
        vcounters = self.analyzer.count_vertical_margins(0, 3)
        vmetrics = self.analyzer.analyze_vertical_margins(vcounters)
        self.assertEquals(vmetrics, {})

    @unittest.expectedFailure
    def test_analyze_styles(self):
        stylecounters = self.analyzer.count_styles(0, 3)
        stylemetrics = self.analyzer.analyze_styles(stylecounters)
        self.assertEquals(stylemetrics, {})

    # this is more of a functional test
    @unittest.expectedFailure
    def test_margins(self):
        jsonpath = "test/files/pdfanalyze/lipsum.metrics.json"
        self.assertFalse(os.path.exists(jsonpath))
        metrics = self.analyzer.metrics(jsonpath)
        self.assertEquals(metrics, {'marginleft': 135,
                                    'marginright': 649})
        self.assertTrue(os.path.exists(jsonpath))
        os.unlink(jsonpath)

    @unittest.expectedFailure
    def test_margins_subdocument(self):
        self.analyzer.frontmatter = 0
        metrics = self.analyzer.metrics(startpage=1, pagecount=1)
        self.assertEquals(metrics, {'marginleft': 135,
                                    'marginright': 649})

    @unittest.expectedFailure
    def test_plot(self):
        # just test that a plot is created
        plotpath = "test/files/pdfanalyze/lipsum.metrics.json"
        self.assertFalse(os.path.exists(plotpath))
        self.analyzer.metrics(plotpath=plotpath)
        self.assertTrue(os.path.exists(plotpath))
        os.unlink(plotpath)

    @unittest.expectedFailure
    def test_drawboxes(self):
        # just test that a pdf is created
        pdfpath = "test/files/pdfanalyze/lipsum.debug.pdf"
        self.assertFalse(os.path.exists(pdfpath))
        self.analyzer.drawboxes(pdfpath)
        self.assertTrue(os.path.exists(pdfpath))
        os.unlink(pdfpath)
