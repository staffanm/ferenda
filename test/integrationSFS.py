# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import sys, os
from ferenda.compat import unittest

from six import text_type as str

from ferenda.manager import setup_logger; setup_logger('CRITICAL')

import codecs
# from ferenda.sources.legal.se import SFS
from lagen.nu import SFS  # uses a more complete URISpace definition
from ferenda.elements import serialize, LinkSubject
from ferenda import TextReader


class Parse(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # Creating a new SFS object, particularly loading resources
        # and URI space is so expensive, so we set it up once here
        cls.p = SFS()
        cls.p.id = '9999:998'
        # for compatibility with old test files:
        cls.p.minter.space.base = "http://localhost:8000/res/" 

    
    def parametric_test(self, filename):
        self.maxDiff = None
        reader = TextReader(filename=filename, encoding='iso-8859-1',
                              linesep=TextReader.DOS)
        reader.autostrip = True
        # p.lagrum_parser = FakeParser()
        parser = self.p.get_parser("9999:998", reader)
        b = parser(reader)
        elements = self.p._count_elements(b)

        # FIXME: How was this used? Where should we plug
        # skipfragments?
        if 'K' in elements and elements['K'] > 1 and elements['P1'] < 2:
            self.p.skipfragments = [
                ('rinfoex:avdelningnummer', 'rpubl:kapitelnummer'),
                ('rpubl:kapitelnummer', 'rpubl:paragrafnummer')]
        else:
            self.p.skipfragments = [('rinfoex:avdelningnummer',
                                     'rpubl:kapitelnummer')]

        # NB: _construct_ids won't look for references
        self.p.visit_node(b, self.p.construct_id, {'basefile': '9999:998'})
        self.p.visit_node(b, self.p.find_definitions, False, debug=False)
        from pudb import set_trace; set_trace()
        self.p.lagrum_parser.parse_recursive(b)
        self._remove_uri_for_testcases(b)
        resultfilename = filename.replace(".txt", ".xml")
        if os.path.exists(resultfilename):
            with codecs.open(resultfilename, encoding="utf-8") as fp:
                result = fp.read().strip()
            self.assertEqual(result, serialize(b).strip())
        else:
            self.assertEqual("", serialize(b).strip())
        # reset the state of the repo...
        self.p.current_section = '0'
        self.p.current_headline_level = 0

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
    
file_parametrize(Parse, "test/files/sfs/parse", ".txt", broken)
