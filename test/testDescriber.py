# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import datetime

from ferenda.compat import unittest

from rdflib import Graph, Namespace

# SUT
from ferenda import Describer
DCT = Namespace("http://purl.org/dc/terms/")
FOAF = Namespace("http://xmlns.com/foaf/0.1/")

class TestDescriber(unittest.TestCase):
    def setUp(self):
        self.graph = Graph()
        self.graph.parse(data="""
@prefix dct: <http://purl.org/dc/terms/> .
@prefix foaf: <http://xmlns.com/foaf/0.1/> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

<http://example.org/doc> a foaf:Document;
        dct:title "Hello world"@en ;
        dct:identifier "ID1",
                       "ID2";
        dct:issued "2013-10-11"^^xsd:date;
        dct:references <http://example.org/doc2>;
        dct:subject <http://example.org/concept1>,
                    <http://example.org/concept2> .
        """, format="turtle")
        self.desc = Describer(self.graph, "http://example.org/doc")

    def test_getvalues(self):
        self.assertEqual(self.desc.getvalues(DCT.alternate),
                         [])
        self.assertEqual(self.desc.getvalues(DCT.title),
                         ["Hello world"])
        self.assertEqual(set(self.desc.getvalues(DCT.identifier)),
                         set(["ID1", "ID2"]))

    def test_getvalue(self):
        self.assertEqual(self.desc.getvalue(DCT.title),
                         "Hello world")
        self.assertEqual(self.desc.getvalue(DCT.issued),
                         datetime.date(2013,10,11))
        with self.assertRaises(KeyError):
            self.desc.getvalue(DCT.alternate)
        with self.assertRaises(KeyError):
            self.desc.getvalue(DCT.identifier)

    def test_getrels(self):
        self.assertEqual(self.desc.getrels(DCT.replaces),
                         [])
        self.assertEqual(self.desc.getrels(DCT.references),
                         ["http://example.org/doc2"])
        self.assertEqual(set(self.desc.getrels(DCT.subject)),
                         set(["http://example.org/concept1",
                              "http://example.org/concept2"]))

    def test_getrel(self):
        self.assertEqual(self.desc.getrel(DCT.references),
                         "http://example.org/doc2")
        with self.assertRaises(KeyError):
            self.desc.getrel(DCT.replaces)
        with self.assertRaises(KeyError):
            self.desc.getrel(DCT.subject)
            
    def test_getrdftype(self):
        self.assertEqual(self.desc.getrdftype(),
                         "http://xmlns.com/foaf/0.1/Document")
