# -*- coding: utf-8 -*-
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
from builtins import *

import ast
import sys
import os
import codecs
import re

from rdflib import Namespace, Graph, RDF, URIRef

from ferenda.compat import unittest
from ferenda import ResourceLoader
from ferenda.sources.legal.se.legalref import LegalRef
from ferenda.elements import serialize
from ferenda.testutil import file_parametrize
from ferenda.thirdparty.coin import URIMinter

class TestLegalRef(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # this particular test method is set up to use lagen.nu style
        # URIs because the canonical URIs are significantly different.
        space = "lagen/nu/res/uri/swedishlegalsource.space.ttl"
        slugs = "lagen/nu/res/uri/swedishlegalsource.slugs.ttl"
        extra = ["lagen/nu/res/extra/swedishlegalsource.ttl",
                 "lagen/nu/res/extra/sfs.ttl"]
        cfg = Graph().parse(space,
                            format="turtle").parse(slugs, format="turtle")
        cls.metadata = Graph()
        for ttl in extra:
            cls.metadata.parse(ttl, format="turtle")
        COIN = Namespace("http://purl.org/court/def/2009/coin#")
        # select correct URI for the URISpace definition by
        # finding a single coin:URISpace object
        spaceuri = cfg.value(predicate=RDF.type, object=COIN.URISpace)
        cls.minter = URIMinter(cfg, spaceuri)
    
    def _test_parser(self, testfile, parser):
        # encoding = 'iso-8859-1'
        encoding = 'windows-1252'
        with codecs.open(testfile,encoding=encoding) as fp:
            testdata = fp.read()

        parts = re.split('\r?\n\r?\n',testdata,1)
        if len(parts) == 1:
            want = ''
        else:
            (testdata, want) = parts
        want = want.replace("\r\n", "\n").strip()
        # p.currentlynamedlaws = {} # needed?
        test_paras = re.split('\r?\n---\r?\n',testdata)
        got_paras = []

        # we need to set up logging in some way as legalref.parse will
        # use logging facilities. For tests though, we should only log
        # at CRITICAL level.
        import logging
        r = logging.getLogger()
        if not r.handlers:
            h = logging.StreamHandler()
            h.setFormatter(logging.Formatter("%(name)s %(levelname)s %(message)s"))
            r.addHandler(h)
            r.setLevel(logging.CRITICAL)
        for para in test_paras:
            if para.startswith("RESET:"):
                parser.currentlynamedlaws.clear()
            elif para.startswith("NOBASE:"):
                baseuri_attributes = {}
            elif para.startswith("BASE:"):
                b = para.split("\n")[0].split(":",1)[1]
                baseuri_attributes = ast.literal_eval(b)
                if 'type' in baseuri_attributes:
                    baseuri_attributes['type'] = URIRef(baseuri_attributes['type'])
                para = para.split("\n",1)[1]
                if 'kommittensbetankande' in baseuri_attributes:
                    parser.kommittensbetankande = baseuri_attributes['kommittensbetankande']
                    del baseuri_attributes['kommittensbetankande']
            else:
                baseuri_attributes = {'law': '9999:999'}
            nodes = parser.parse(para, self.minter, self.metadata,
                                 baseuri_attributes)
            got_paras.append(serialize(nodes).strip())
        got = "\n---\n".join(got_paras).replace("\r\n","\n").strip()
        self.maxDiff = None
        self.assertEqual(want, got)

class Lagrum(TestLegalRef):
    def parametric_test(self,datafile):
        p = LegalRef(LegalRef.LAGRUM)
        return self._test_parser(datafile, p)

class KortLagrum(TestLegalRef):
    def parametric_test(self,datafile):
        p = LegalRef(LegalRef.LAGRUM, LegalRef.KORTLAGRUM)
        return self._test_parser(datafile, p)

class EnklaLagrum(TestLegalRef):
    def parametric_test(self,datafile):
        p = LegalRef(LegalRef.ENKLALAGRUM)
        return self._test_parser(datafile, p)

class Forarbeten(TestLegalRef):
    def parametric_test(self,datafile):
        p = LegalRef(LegalRef.FORARBETEN)
        return self._test_parser(datafile, p)

class Rattsfall(TestLegalRef):
    def parametric_test(self,datafile):
        p = LegalRef(LegalRef.RATTSFALL)
        return self._test_parser(datafile, p)

class EULaw(TestLegalRef):
    def parametric_test(self,datafile):
        p = LegalRef(LegalRef.EULAGSTIFTNING)
        return self._test_parser(datafile, p)

class EUCaselaw(TestLegalRef):
    def parametric_test(self,datafile):
        p = LegalRef(LegalRef.EGRATTSFALL)
        return self._test_parser(datafile, p)

# Some tests are not simply working right now. Since having testdata
# and wanted result in the same file makes it tricky to mark tests as
# expectedFailure, we'll just list them here.
def make_closure(brokentests):
    def broken(testname):
        return testname in brokentests
    return broken

file_parametrize(Lagrum,"test/files/legalref/SFS",".txt",
                 make_closure(['sfs-tricky-bokstavslista.txt',
                               'sfs-tricky-eller.txt',
                               'sfs-tricky-eller-paragrafer-stycke.txt',
                               'sfs-tricky-overgangsbestammelse.txt',
                               'sfs-tricky-uppdelat-lagnamn.txt',
                               'sfs-tricky-vvfs.txt']))
file_parametrize(KortLagrum, "test/files/legalref/Short",".txt")
file_parametrize(EnklaLagrum, "test/files/legalref/Simple",".txt")
file_parametrize(Forarbeten, "test/files/legalref/Regpubl",".txt")
file_parametrize(Rattsfall, "test/files/legalref/DV",".txt")
file_parametrize(EULaw, "test/files/legalref/EGLag",".txt")
file_parametrize(EUCaselaw, "test/files/legalref/ECJ",".txt",
                 make_closure(['civilservicetrib.txt',
                               'simple.txt']))
