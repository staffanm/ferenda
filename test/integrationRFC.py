# -*- coding: utf-8 -*-
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
from builtins import *

import sys
import os

from ferenda.compat import unittest
from ferenda.testutil import FerendaTestCase, testparser, file_parametrize
from ferenda.sources.tech import RFC


# FIXME: This test should be re-worked as a normal RepoTester test
class Parse(unittest.TestCase, FerendaTestCase):
    def parametric_test(self, filename):
        # parser = ferenda.sources.tech.rfc.RFC.get_parser()
        parser = RFC.get_parser()
        testparser(self,parser,filename)

file_parametrize(Parse,"test/files/rfc",".txt")
