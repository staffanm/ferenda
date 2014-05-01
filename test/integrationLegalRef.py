# -*- coding: utf-8 -*-
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
import sys
if sys.version_info[:2] == (3,2): # remove when py32 support ends
    import uprefix
    uprefix.register_hook()
    from future.builtins import *
    uprefix.unregister_hook()
else:
    from future.builtins import *

import os
from ferenda.compat import unittest
if os.getcwd() not in sys.path: sys.path.insert(0,os.getcwd())

import codecs
import re
    
from ferenda.sources.legal.se.legalref import LegalRef
from ferenda.elements import serialize
from ferenda.testutil import file_parametrize


@unittest.skipIf('SKIP_SIMPLEPARSE_TESTS' in os.environ,
                 "Skipping SimpleParser dependent tests")    
class TestLegalRef(unittest.TestCase):
    
    def _test_parser(self, testfile, parser):
        encoding = 'iso-8859-1'
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
        for para in test_paras:
            if para.startswith("RESET:"):
                parser.currentlynamedlaws.clear()
            if para.startswith("NOBASE:"):
                baseuri = None
            else:
                baseuri = 'http://rinfo.lagrummet.se/publ/sfs/9999:999'
            # print("Parsing %r" % para)
            nodes = parser.parse(para, baseuri)
            got_paras.append(serialize(nodes).strip())
        got = "\n---\n".join(got_paras).replace("\r\n","\n").strip()
        self.maxDiff = None
        self.assertEqual(want, got)

@unittest.skipIf('SKIP_SIMPLEPARSE_TESTS' in os.environ,
                 "Skipping SimpleParser dependent tests")    
class Lagrum(TestLegalRef):
    def parametric_test(self,datafile):
        p = LegalRef(LegalRef.LAGRUM)
        return self._test_parser(datafile, p)

@unittest.skipIf('SKIP_SIMPLEPARSE_TESTS' in os.environ,
                 "Skipping SimpleParser dependent tests")    
class KortLagrum(TestLegalRef):
    def parametric_test(self,datafile):
        p = LegalRef(LegalRef.LAGRUM, LegalRef.KORTLAGRUM)
        return self._test_parser(datafile, p)

@unittest.skipIf('SKIP_SIMPLEPARSE_TESTS' in os.environ,
                 "Skipping SimpleParser dependent tests")    
class Forarbeten(TestLegalRef):
    def parametric_test(self,datafile):
        p = LegalRef(LegalRef.FORARBETEN)
        return self._test_parser(datafile, p)

@unittest.skipIf('SKIP_SIMPLEPARSE_TESTS' in os.environ,
                 "Skipping SimpleParser dependent tests")    
class Rattsfall(TestLegalRef):
    def parametric_test(self,datafile):
        p = LegalRef(LegalRef.RATTSFALL)
        return self._test_parser(datafile, p)

@unittest.skipIf('SKIP_SIMPLEPARSE_TESTS' in os.environ,
                 "Skipping SimpleParser dependent tests")    
class EULaw(TestLegalRef):
    def parametric_test(self,datafile):
        p = LegalRef(LegalRef.EGLAGSTIFTNING)
        return self._test_parser(datafile, p)

@unittest.skipIf('SKIP_SIMPLEPARSE_TESTS' in os.environ,
                 "Skipping SimpleParser dependent tests")    
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
file_parametrize(Forarbeten, "test/files/legalref/Regpubl",".txt")
file_parametrize(Rattsfall, "test/files/legalref/DV",".txt")
file_parametrize(EULaw, "test/files/legalref/EGLag",".txt")
file_parametrize(EUCaselaw, "test/files/legalref/ECJ",".txt",
                 make_closure(['civilservicetrib.txt',
                               'simple.txt']))
