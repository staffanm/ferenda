# -*- coding: utf-8 -*-
import sys
if sys.version_info[:2] == (3,2): # remove when py32 support ends
    import uprefix
    uprefix.register_hook()
    from future.builtins import *
    uprefix.unregister_hook()
else:
    from future.builtins import *

import os
if os.getcwd() not in sys.path: sys.path.insert(0,os.getcwd())
import pkg_resources
pkg_resources.resource_listdir('ferenda','res')

from copy import deepcopy

from pyparsing import Word,nums

from ferenda.compat import unittest

from ferenda.citationparser import CitationParser
from ferenda.uriformatter import URIFormatter
from ferenda.elements import Body, Heading, Paragraph, Footnote, LinkSubject, UnicodeElement, serialize
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
                                LinkSubject("Doc 43/2012", predicate="dcterms:references",
                                           uri="http://example.org/docs/2012/43/"),
                                " and it's interpretation"]),
                       Paragraph(["According to ",
                                  LinkSubject("Doc 43/2012", predicate="dcterms:references",
                                              uri="http://example.org/docs/2012/43/"),
                                  Footnote(["Available at ",
                                            LinkSubject("http://example.org/xyz", 
                                                        predicate="dcterms:references",
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

    def test_parse_existing(self):
        # make sure parserecursive doesn't mess with existing structure.
        class MyHeader(UnicodeElement): pass
        

        doc = Body([MyHeader("My document"),
                    Paragraph([
                        "It's a very very fine document.",
                        MyHeader("Subheading"),
                        "And now we're done."
                        ])
                    ])
        
        want = serialize(doc)

        # first test a blank CitationParser, w/o patterns or formatter
        cp = CitationParser() 
        
        doccopy = deepcopy(doc)
        cp.parse_recursive(doccopy)
        got = serialize(doccopy)
        self.assertEqual(want, got)

        cp = CitationParser(ferenda.citationpatterns.url)
        cp.set_formatter(URIFormatter(("url", ferenda.uriformats.url)))
        doccopy = deepcopy(doc)
        cp.parse_recursive(doccopy)
        got = serialize(doccopy)
        self.assertEqual(want, got)

        
import doctest
from ferenda import citationparser
def load_tests(loader,tests,ignore):
    tests.addTests(doctest.DocTestSuite(citationparser))
    return tests
