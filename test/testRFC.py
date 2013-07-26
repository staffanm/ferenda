#!/usr/bin/env python
# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import sys, os
if sys.version_info < (2,7,0):
    import unittest2 as unittest
else:
    import unittest
# if os.getcwd() not in sys.path: sys.path.insert(0,os.getcwd())

from ferenda.manager import setup_logger; setup_logger('CRITICAL')


from ferenda.testutil import RepoTester, FerendaTestCase, testparser, file_parametrize, parametrize_repotester
from ferenda.sources.tech import RFC

# import ferenda.sources.tech.rfc

class Parse(unittest.TestCase, FerendaTestCase):
    def parametric_test(self, filename):
        # parser = ferenda.sources.tech.rfc.RFC.get_parser()
        parser = RFC.get_parser()
        testparser(self,parser,filename)

file_parametrize(Parse,"test/files/rfc",".txt")

class TestRFC(RepoTester):
    repoclass = RFC
    docroot = os.path.dirname(__file__)+"/files/repo/rfc"

parametrize_repotester(TestRFC)
