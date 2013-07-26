#!/usr/bin/env python
# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import sys, os
if sys.version_info < (2,7,0):
    import unittest2 as unittest
else:
    import unittest
if os.getcwd() not in sys.path: sys.path.insert(0,os.getcwd())

from ferenda import Devel

class Main(unittest.TestCase):
    def test_parsestring(self):
        d = Devel()
        with self.assertRaises(NotImplementedError):
            d.parsestring(None,None,None)
