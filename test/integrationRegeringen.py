# -*- coding: utf-8 -*-
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
from builtins import *

import sys
import os

from ferenda.compat import unittest

# SUT
from ferenda.sources.legal.se import Regeringen


class SelectPDFs(unittest.TestCase):

    def setUp(self):
        self.repo = Regeringen()

    def _t(self, data, want):
        got = self.repo.select_pdfs(data)
        self.assertEqual(want, got)
        
    def test_single(self):
        self._t([("74a82f1a.pdf", "Ut ur skuldfällan, SOU 2013:72 (pdf 3,9 MB)"),],
                ["74a82f1a.pdf"])

    def test_hela_dokumentet(self):
        self._t([("24449365.pdf", "PBL-överprövning, SOU 2014:14 - hela dokumentet (pdf 3,2 MB)"),
                 ("cf16178c.pdf", "PBL-överprövning, SOU 2014:14 - del 1 (pdf 1,8 MB)"),
                 ("c504d179.pdf", "PBL-överprövning, SOU 2014:14 - del 2 (pdf 1,5 MB)")],
                ["24449365.pdf"])

        self._t([("086b9403.pdf", "Överskuldsättning? SOU 2013:78, hela betänkandet (pdf 4,1 MB)"),
                 ("bb25c9ce.pdf", "Överskuldsättning? SOU 2013:78, del 1 av 2 (pdf 3,0 MB)"),
                 ("f49ca004.pdf", "Överskuldsättning? SOU 2013:78, del 2 av 2 (pdf 1,5 MB)")],
                ["086b9403.pdf"])
        
    def test_hela_dokumentet_implicit(self):
        self._t([("74a82f1a.pdf", "Ut ur skuldfällan, SOU 2013:72 (pdf 3,9 MB)"),
                 ("f220eff3.pdf", "Ut ur skuldfällan, SOU 2013:72, del 1 av 2 (pdf 3,1 MB)"),
                 ("1c1364c5.pdf", "Ut ur skuldfällan, SOU 2013:72, del 2 av 2, Bilagor (pdf 997 kB)")],
                ["74a82f1a.pdf"])

    def test_delar(self):
        self._t([("4ab56c4e.pdf", "En digital agenda, SOU 2014:13 (del 1 av 2) (pdf 2,3 MB)"),
                 ("e265db7c.pdf", "En digital agenda, SOU 2014:13 (del 2 av 2) (pdf 1,4 MB)")],
                ["4ab56c4e.pdf", "e265db7c.pdf"])

    def test_sammanfattning(self):
        self._t([("0a086bba.pdf", "Unga som varken arbetar, SOU 2013:74 (pdf 2,2 MB)"),
                 ("afcd1231.pdf", "Sammanfattning på lättläst svenska (pdf 69 kB)"),
                 ("5f5d1c72.pdf", "Sammanfattning (pdf 115 kB)"),
                 ("5a01d46b.pdf", "Sammanfattning på engelska (Summary in english) (pdf 116 kB)")],
                ["0a086bba.pdf"])

    def test_remiss(self):
        self._t([("b69bdbb0.pdf", "Se medborgarna, SOU 2009:92 (pdf 2,7 MB)"),
                 ("17788e61.pdf", "Remissammanställning över slutbetänkande av utredningen (pdf 605 kB)")],
                ["b69bdbb0.pdf"])

        self._t([("1dc00905.pdf", "Strategi för myndigheternas, SOU 2009:86  (pdf 2,9 MB)"),
                 ("36b059e0.pdf", "Sammanställning över remissyttranden (pdf 1,2 MB)")],
                ["1dc00905.pdf"])

        self._t([("57313bec.pdf", "Tonnageskatt, SOU 2006:20 (pdf 1,6 MB)"),
                 ("d838f8a5.pdf", "Lista över remissinstanser (pdf 42 kB)")],
                ["57313bec.pdf"])
        

        
