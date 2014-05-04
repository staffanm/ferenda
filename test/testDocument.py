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

import rdflib

#SUT
from ferenda import Document

class TestDocument(unittest.TestCase):
    def test_init(self):
        d = Document()
        self.assertIsInstance(d.meta, rdflib.Graph)
        self.assertEqual(d.body, [])
        self.assertIsNone(d.uri)
        self.assertIsNone(d.lang)
        self.assertIsNone(d.basefile)
