# -*- coding: utf-8 -*-
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
from builtins import *

from rdflib import Graph

from ferenda.elements import Body
from ferenda.compat import unittest
# SUT
from ferenda import Document


class Main(unittest.TestCase):
    def test_create(self):
        doc = Document(uri="http://example.org/",
                       lang="en",
                       basefile="1")
        self.assertEqual(doc.uri, "http://example.org/")
        self.assertEqual(doc.lang, "en")
        self.assertEqual(doc.basefile, "1")

    def test_create_meta(self):
        g = Graph()
        doc = Document(meta=g)
        self.assertIs(g, doc.meta)

    def test_create_body(self):
        b = Body()
        doc = Document(body=b)
        self.assertIs(b, doc.body)
        
    
