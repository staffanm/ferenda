# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import sys, os
if os.getcwd() not in sys.path: sys.path.insert(0,os.getcwd())
import pkg_resources
pkg_resources.resource_listdir('ferenda','res')

from pyparsing import Word,nums

from ferenda.compat import unittest

from ferenda.citationparser import CitationParser
from ferenda.uriformatter import URIFormatter
from ferenda.elements import Body, Heading, Paragraph, Footnote, LinkSubject, serialize
import ferenda.uriformats
import ferenda.citationpatterns

class Main(unittest.TestCase):

    def test_parse_recursive(self):
        doc_citation = ("Doc" + Word(nums).setResultsName("ordinal") 
                        + "/" + 
                        Word(nums,exact=4).setResultsName("year")).setResultsName("DocRef")

        def doc_uri_formatter(parts):
            return "http://example.org/docs/%(year)s/%(ordinal)s/" % parts


        doc = Body([Heading(["About Doc 43/2012 and it's interpretation"]),
                    Paragraph(["According to Doc 43/2012",
                               Footnote(["Available at http://example.org/xyz"]),
                               " the bizbaz should be frobnicated"])
                    ])

        result = Body([Heading(["About ",
                                LinkSubject("Doc 43/2012", predicate="dct:references",
                                           uri="http://example.org/docs/2012/43/"),
                                " and it's interpretation"]),
                       Paragraph(["According to ",
                                  LinkSubject("Doc 43/2012", predicate="dct:references",
                                              uri="http://example.org/docs/2012/43/"),
                                  Footnote(["Available at ",
                                            LinkSubject("http://example.org/xyz", 
                                                        predicate="dct:references",
                                                        uri="http://example.org/xyz")
                                            ]),
                                  " the bizbaz should be frobnicated"])
                       ])
        
        cp = CitationParser(ferenda.citationpatterns.url, doc_citation)
        cp.set_formatter(URIFormatter(("url", ferenda.uriformats.url),
                                      ("DocRef", doc_uri_formatter)))
        doc = cp.parse_recursive(doc)
        self.maxDiff = 4096
        self.assertEqual(serialize(doc),serialize(result))

import doctest
from ferenda import citationparser
def load_tests(loader,tests,ignore):
    tests.addTests(doctest.DocTestSuite(citationparser))
    return tests
