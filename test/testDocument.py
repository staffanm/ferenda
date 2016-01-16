# -*- coding: utf-8 -*-
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
from builtins import *

from ferenda.compat import unittest

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
