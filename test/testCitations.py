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
import codecs
from ferenda.compat import unittest
if os.getcwd() not in sys.path: sys.path.insert(0,os.getcwd())

from ferenda import CitationParser
from ferenda import util
import ferenda.citationpatterns
from ferenda.testutil import file_parametrize

class ParametricBase(unittest.TestCase):
    parser = ferenda.citationpatterns.url
    def parametric_test(self,filename):
        with codecs.open(filename,encoding="utf-8") as fp:
            testdata = fp.read()
        
        cp = CitationParser(self.parser)
        nodes = cp.parse_string(testdata)
        got = []
        for node in nodes:
            if isinstance(node, str):
                got.append(node.strip())
            else:
                (text,result) = node
                got.append(util.parseresults_as_xml(result).strip())
        
        wantfile = os.path.splitext(filename)[0] + ".result"
        if os.path.exists(wantfile):
            with open(wantfile) as fp:
                want = [x.strip() for x in fp.read().split("\n\n")]
        else:
            print("\nparse_string() returns:")
            print("\n\n".join(compare))
            self.fail("%s not found" % wantfile)
        self.maxDiff = 4096
        self.assertListEqual(want,got)

class URL(ParametricBase):
    parser = ferenda.citationpatterns.url

class EULaw(ParametricBase):
    parser = ferenda.citationpatterns.eulaw

file_parametrize(URL, "test/files/citation/url", ".txt")
# file_parametrize(URL, "test/files/citation/eulaw", ".txt")
