#!/usr/bin/env python
# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import os,sys
import codecs
if sys.version_info < (2,7,0):
    import unittest2 as unittest
else:
    import unittest
if os.getcwd() not in sys.path: sys.path.insert(0,os.getcwd())

import six

from ferenda import CitationParser
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
            if isinstance(node,six.text_type):
                got.append(node.strip())
            else:
                (text,result) = node
                got.append(result.asXML().strip())
        
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

if sys.version_info[0:2] == (3,3):
    file_parametrize(URL, "test/files/citation/url", ".txt", unittest.expectedFailure)
else:
    file_parametrize(URL, "test/files/citation/url", ".txt")
# file_parametrize(URL, "test/files/citation/eulaw", ".txt")
