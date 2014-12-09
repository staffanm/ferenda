# -*- coding: utf-8 -*-
from __future__ import unicode_literals, print_function

import sys, os
# from ferenda.compat import unittest
if os.getcwd() not in sys.path: sys.path.insert(0,os.getcwd())
from ferenda.manager import setup_logger; setup_logger('CRITICAL')

import copy
import json
import shutil

from lxml import etree
from rdflib import RDF, Graph
from rdflib.namespace import DCTERMS

from ferenda.compat import Mock, MagicMock, patch
from ferenda import util
from ferenda.testutil import RepoTester
from ferenda.elements import Link

# SUT
from ferenda import Facet, Feedset, Feed

class News(RepoTester):
    # results1 = json.load(open("test/files/datasets/results1.json"))
    results2 = json.load(open("test/files/datasets/results2-plus-entries.json"))
    results2data = Graph().parse(open("test/files/datasets/results2data.ttl"), format="turtle")

    facets = [Facet(rdftype=RDF.type),
              Facet(rdftype=DCTERMS.publisher),
              Facet(rdftype=DCTERMS.issued)]
    
    feedsets = [Feedset(label="By publisher",
                        feeds=[Feed(title="Books published by Nature",
                                    slug="publisher/nature",
                                    binding="dcterms_publisher",
                                    value="http://example.org/journals/nature"),
                               Feed(title="Books published by Biochem",
                                    slug="publisher/biochem",
                                    binding="dcterms_publisher",
                                    value="http://example.org/journals/biochem"),
                               Feed(title="Books published by Analytical",
                                    slug="publisher/analytical",
                                    binding="dcterms_publisher",
                                    value="http://example.org/journals/analytical")]),
                Feedset(label="By document type",
                        feeds=[Feed(title="bibo:Book",
                                    slug="type/book",
                                    binding="rdf_type",
                                    value="http://purl.org/ontology/bibo/Book")]),
                Feedset(label="main",
                        feeds=[Feed(title="All documents in base",
                                    slug="main",
                                    binding=None,
                                    value=None)])]


    def setUp(self):
        super(News, self).setUp()

    def test_news(self):
        self.repo.news() # gmmm

    def test_feedsets(self):
        got = self.repo.news_feedsets(self.results2, self.facets)
        want = self.feedsets
