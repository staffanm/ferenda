# -*- coding: utf-8 -*-
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
from builtins import *

import os

import doctest
import rdflib

from ferenda.compat import patch
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
        self.assertEqual(facets[0].label, "Sorted by %(term)s")
        self.assertIsInstance(facets[0].label, str)
        
        # and more ...

    def test_faceted_data(self):
        canned = [{"uri": "http://example.org/books/A_Tale_of_Two_Cities",
                   "dcterms_title": "A Tale of Two Cities"},
                  {"uri": "http://example.org/books/The_Lord_of_the_Rings",
                   "dcterms_title": "The Lord of the Rings"},
        ]
        with patch('ferenda.DocumentRepository.facet_select', return_value=canned) as mock:
            faceted_data = self.repo.faceted_data()
        self.assertEqual(faceted_data, canned)
        self.assertTrue(os.path.exists(self.datadir + "/base/toc/faceted_data.json"))
        # on second run, faceted_data should be read from the cache
        # (if outfile_is_newer is called, we're far enough down in
        # that branch to know that the cache file is used if
        # outfile_is_newer returns True)
        with patch('ferenda.util.outfile_is_newer', return_value=True):
            faceted_data = self.repo.faceted_data()
        self.assertEqual(faceted_data, canned)

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
