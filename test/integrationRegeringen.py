# -*- coding: utf-8 -*-
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
from builtins import *

import sys
import os

from ferenda.compat import unittest

# SUT
from ferenda.sources.legal.se import Regeringen


class SelectFiles(unittest.TestCase):

    def setUp(self):
        self.repo = Regeringen()

    def _t(self, data, want, compare="f"):
        got = self.repo.select_files(data)

        if compare == "f":
            # only compare the file part of resulting tuples
            filter = lambda f, t, l: f
        elif compare == "fl":
            # compare file and label
            filter = lambda f, t, l: (f, l)
        elif compare == "t":
            # compare only type
            filter = lambda f, t, l: t
        got = [filter(tup[0], tup[1], tup[2]) for tup in got]
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

    def test_hela_dokumentet_implicit_2(self):
        self._t([("a", "Bortom fagert tal - om bristande tillgänglighet som diskriminering, Ds 2010:20 (pdf 4 MB)"),
                 ("b", "Bortom fagert tal - om bristande tillgänglighet som diskriminering, del 1 av 2, Ds 2010:20 (pdf 1 MB)"),
                 ("c", "Bortom fagert tal - om bristande tillgänglighet som diskriminering, del 2 av 2, Ds 2010:20 (pdf 2 MB)"),
                 ("d", "Bortom fagert tal - om bristande tillgänglighet som diskriminering, del 1 av 2, Ds 2010:20 (tillgängligt wordformat) (doc 2 MB)"),
                 ("e", "Bortom fagert tal - om bristande tillgänglighet som diskriminering, del 2 av 2, Ds 2010:20 (tillgängligt wordformat) (doc 1 MB)")],
                ["a"])
        

    def test_delar(self):
        self._t([("4ab56c4e.pdf", "En digital agenda, SOU 2014:13 (del 1 av 2) (pdf 2,3 MB)"),
                 ("e265db7c.pdf", "En digital agenda, SOU 2014:13 (del 2 av 2) (pdf 1,4 MB)")],
                ["4ab56c4e.pdf", "e265db7c.pdf"])

    def test_delar_alternate_versions_and_more(self):
        self._t([('a', 'Utredningens pressmeddelande:"En genomgripande förändring för försäkrings- och tjänstepensionsbranschen" (pdf 106 kB)'),
                 ('b', 'Rörelsereglering för försäkring och tjänstepension, del 1, kapitel 1-24 och bilaga 2 Europaparlamentets och rådets dirketiv 2009/138/EG, SOU 2011:68 (pdf 7 MB)'),
                 ('c', 'Rörelsereglering för försäkring och tjänstepension, del 1, kapitel 1-24, SOU 2011:68 (pdf 4 MB)'),
                 ('d', 'Rörelsereglering för försäkring och tjänstepension, del 2, SOU 2011:68 (pdf 3 MB)'),
                 ('e', 'Bilaga 2 Europaparlamentets och rådets dirketiv 2009/138/EG (pdf 3 MB)'),
                 ('f', 'Rättelseblad: Rörelsereglering för försäkring och tjänstepension, del 1, SOU 2011:68 (pdf 130 kB)')],
                ["b", "d"])

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
        
    def test_single_label(self):
        self._t([("74a82f1a.pdf", "Ut ur skuldfällan, SOU 2013:72 (pdf 3,9 MB)"),],
                [("74a82f1a.pdf", "Ut ur skuldfällan, SOU 2013:72 (pdf 3,9 MB)")],
                compare="fl")

    def test_delar_label(self):
        self._t([("4ab56c4e.pdf", "En digital agenda, SOU 2014:13 (del 1 av 2) (pdf 2,3 MB)"),
                 ("e265db7c.pdf", "En digital agenda, SOU 2014:13 (del 2 av 2) (pdf 1,4 MB)")],
                [("4ab56c4e.pdf", "En digital agenda, SOU 2014:13 (del 1 av 2) (pdf 2,3 MB)"),
                 ("e265db7c.pdf", "En digital agenda, SOU 2014:13 (del 2 av 2) (pdf 1,4 MB)")],
                compare="fl")


    def test_filetype(self):
        self._t([("a", "Bättre skola genom mer attraktiva skolprofessioner, Dir. 2016:76 (pdf 284 kB)")],
                ["pdf"],
                compare="t")

        self._t([("a", "Dir. 2011:70 (doc 147 kB)")],
                ["doc"],
                compare="t")
