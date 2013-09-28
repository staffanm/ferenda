# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import sys, os
from ferenda.compat import unittest
if os.getcwd() not in sys.path: sys.path.insert(0,os.getcwd())

from ferenda import Devel

class Main(unittest.TestCase):
    def test_parsestring(self):
        d = Devel()
        with self.assertRaises(NotImplementedError):
            d.parsestring(None,None,None)
