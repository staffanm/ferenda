# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import sys, os
from ferenda.compat import unittest
if os.getcwd() not in sys.path: sys.path.insert(0,os.getcwd())

from ferenda import util

# SUT
from ferenda import PDFReader
from ferenda import PDFAnalyzer


# these test could be in testPDFAnalyze, but they depend on huge and
# slow external libs. Corresponding tests in testPDFAnalyze mocks out
# those libs, these tests exercise those libs.

class Analyze(unittest.TestCase):
    def setUp(self):
        self.maxDiff = None
        self.pdf = PDFReader(filename="test/files/pdfanalyze/lipsum.pdf",
                             workdir="test/files/pdfanalyze/")
        self.analyzer = PDFAnalyzer(self.pdf)

    def tearDown(self):
        util.robust_remove("test/files/pdfanalyze/lipsum.metrics.json")
        util.robust_remove("test/files/pdfanalyze/lipsum.plot.png")
        util.robust_remove("test/files/pdfanalyze/lipsum.debug.pdf")

    def test_plot(self):
        # just test that a plot is created
        plotpath = "test/files/pdfanalyze/lipsum.plot.png"
        self.assertFalse(os.path.exists(plotpath))
        self.analyzer.metrics(plotpath=plotpath)
        self.assertTrue(os.path.exists(plotpath))

    # reportlab doesn't work with py3.2, current release of pyPDF2
    # (1.24) has a py3 bug that crashes page merging (patch exists at
    # https://github.com/mstamy2/PyPDF2/pull/172)
    @unittest.skipIf(sys.version_info > (3, 0, 0), "pyPDF2 not working on py3")
    def test_drawboxes(self):
        # just test that a pdf is created
        pdfpath = "test/files/pdfanalyze/lipsum.debug.pdf"
        self.assertFalse(os.path.exists(pdfpath))
        metrics = self.analyzer.metrics()
        self.analyzer.drawboxes(pdfpath, metrics=metrics)
        self.assertTrue(os.path.exists(pdfpath))
