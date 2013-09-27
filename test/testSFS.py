# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import sys, os
from ferenda.compat import unittest

from six import text_type as str

from ferenda.manager import setup_logger; setup_logger('CRITICAL')

import codecs
from ferenda.sources.legal.se import SFS
from ferenda.elements import serialize, LinkSubject
from ferenda import TextReader


class FakeParser(object):

    def parse(self, s, baseuri, rdftype):
        return [s]


class Parse(unittest.TestCase):

    def parametric_test(self, filename):
        p = SFS()
        p.id = '(test)'
        p.reader = TextReader(filename=filename, encoding='iso-8859-1',
                              linesep=TextReader.DOS)
        p.reader.autostrip = True
        # p.lagrum_parser = FakeParser()
        b = p.makeForfattning()
        elements = p._count_elements(b)
        if 'K' in elements and elements['K'] > 1 and elements['P1'] < 2:
            # should be "skipfragments = ['A','K']", but this breaks test cases
            skipfragments = ['A', 'K']
        else:
            skipfragments = ['A']
        p._construct_ids(b, '', 'http://rinfo.lagrummet.se/publ/sfs/9999:999',
                         skipfragments)

        self._remove_uri_for_testcases(b)
        resultfilename = filename.replace(".txt", ".xml")
        self.maxDiff = 4096
        if os.path.exists(resultfilename):
            with codecs.open(resultfilename, encoding="utf-8") as fp:
                result = fp.read().strip()
            self.assertEqual(result, serialize(b).strip())
        else:
            self.assertEqual("", serialize(b).strip())

    def _remove_uri_for_testcases(self, part):
        if hasattr(part,'uri'):
            del part.uri
        for subpart in part:
            if not isinstance(subpart, str):
                self._remove_uri_for_testcases(subpart)
            elif hasattr(subpart, 'uri') and not isinstance(subpart, LinkSubject):
                del subpart.uri
            
                
            
from ferenda.testutil import file_parametrize

# tests that are broken 
brokentests = ['definition-no-definition.txt',
               'definition-paranthesis-lista.txt',
               'definition-paranthesis-multiple.txt',
               'definition-strecksatslista-andrastycke.txt',
               'extra-overgangsbestammelse-med-rubriker.txt',
               'regression-10kap-ellagen.txt',
               'tricky-felformatterad-tabell.txt',
               'tricky-lang-rubrik.txt',
               'tricky-lista-inte-rubrik.txt',
               'tricky-lista-not-rubriker-2.txt',
               'tricky-lopande-rubriknumrering.txt',
               'tricky-okand-aldre-lag.txt',
               'tricky-paragraf-inledande-tomrad.txt',
               'tricky-tabell-overgangsbest.txt',
               'tricky-tabell-sju-kolumner.txt']

def broken(testname):
    return testname in brokentests
file_parametrize(Parse,"test/files/sfs/parse",".txt", broken)
