# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import datetime

from ferenda.compat import unittest

from rdflib import Graph, Namespace

# SUT
from ferenda import Describer
DCTERMS = Namespace("http://purl.org/dc/terms/")
FOAF = Namespace("http://xmlns.com/foaf/0.1/")

class TestDescriber(unittest.TestCase):
    def setUp(self):
        self.graph = Graph()
        self.graph.parse(data="""
@prefix dcterms: <http://purl.org/dc/terms/> .
@prefix foaf: <http://xmlns.com/foaf/0.1/> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

<http://example.org/doc> a foaf:Document;
        dcterms:title "Hello world"@en ;
        dcterms:identifier "ID1",
                       "ID2";
        dcterms:issued "2013-10-11"^^xsd:date;
        dcterms:references <http://example.org/doc2>;
        dcterms:subject <http://example.org/concept1>,
                    <http://example.org/concept2> .
        """, format="turtle")
        self.desc = Describer(self.graph, "http://example.org/doc")

    def test_getvalues(self):
        self.assertEqual(self.desc.getvalues(DCTERMS.alternate),
                         [])
        self.assertEqual(self.desc.getvalues(DCTERMS.title),
                         ["Hello world"])
        self.assertEqual(set(self.desc.getvalues(DCTERMS.identifier)),
                         set(["ID1", "ID2"]))

    def test_getvalue(self):
        self.assertEqual(self.desc.getvalue(DCTERMS.title),
                         "Hello world")
        self.assertEqual(self.desc.getvalue(DCTERMS.issued),
                         datetime.date(2013,10,11))
        with self.assertRaises(KeyError):
            self.desc.getvalue(DCTERMS.alternate)
        with self.assertRaises(KeyError):
            self.desc.getvalue(DCTERMS.identifier)

    def test_getrels(self):
        self.assertEqual(self.desc.getrels(DCTERMS.replaces),
                         [])
        self.assertEqual(self.desc.getrels(DCTERMS.references),
                         ["http://example.org/doc2"])
        self.assertEqual(set(self.desc.getrels(DCTERMS.subject)),
                         set(["http://example.org/concept1",
                              "http://example.org/concept2"]))

    def test_getrel(self):
        self.assertEqual(self.desc.getrel(DCTERMS.references),
                         "http://example.org/doc2")
        with self.assertRaises(KeyError):
            self.desc.getrel(DCTERMS.replaces)
        with self.assertRaises(KeyError):
            self.desc.getrel(DCTERMS.subject)
            
    def test_getrdftype(self):
        self.assertEqual(self.desc.getrdftype(),
                         "http://xmlns.com/foaf/0.1/Document")
