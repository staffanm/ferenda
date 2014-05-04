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
