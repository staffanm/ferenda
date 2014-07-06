# -*- coding: utf-8 -*-
from __future__ import unicode_literals, print_function

import sys, os
from ferenda.compat import unittest
if os.getcwd() not in sys.path: sys.path.insert(0,os.getcwd())

from ferenda.manager import setup_logger; setup_logger('CRITICAL')

import doctest

import rdflib

from ferenda.testutil import RepoTester

# SUT
from ferenda import Facet

class Faceting(RepoTester):

    def test_query(self):
        # NOTE: this is also tested by a doctest
        want = """PREFIX dcterms: <http://purl.org/dc/terms/>
PREFIX foaf: <http://xmlns.com/foaf/0.1/>
PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>

SELECT DISTINCT ?uri ?rdf_type ?dcterms_title ?dcterms_publisher ?dcterms_identifier ?dcterms_issued
FROM <http://example.org/ctx/base>
WHERE {
    ?uri rdf:type foaf:Document .
    OPTIONAL { ?uri rdf:type ?rdf_type . }
    OPTIONAL { ?uri dcterms:title ?dcterms_title . }
    OPTIONAL { ?uri dcterms:publisher ?dcterms_publisher . }
    OPTIONAL { ?uri dcterms:identifier ?dcterms_identifier . }
    OPTIONAL { ?uri dcterms:issued ?dcterms_issued . }

}"""
        self.assertEqual(want,
                         self.repo.facet_query("http://example.org/ctx/base"))

    def test_facets(self):
        # tests that all expected facets are created and have the
        # expected properties
        facets = self.repo.facets()
        self.assertEqual(facets[0].rdftype, rdflib.RDF.type)
        # and more ...


    def test_year(self):
        self.assertEqual('2014',
                         Facet.year({'dcterms_issued': '2014-06-05T12:00:00'}))
        self.assertEqual('2014',
                         Facet.year({'dcterms_issued': '2014-06-05'}))
        self.assertEqual('2014',
                         Facet.year({'dcterms_issued': '2014-06'}))
        with self.assertRaises(Exception):
            Facet.year({'dcterms_issued': 'This is clearly an invalid date'})
        with self.assertRaises(Exception):
            Facet.year({'dcterms_issued': '2014-14-99'})
        


# Add doctests in the module
from ferenda import facet
from ferenda.testutil import Py23DocChecker
def load_tests(loader,tests,ignore):
    tests.addTests(doctest.DocTestSuite(facet, checker=Py23DocChecker()))
    return tests
