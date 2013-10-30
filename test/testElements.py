# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import sys, os, tempfile, shutil
from datetime import date
from six import text_type as str
from lxml import etree
from bs4 import BeautifulSoup

from ferenda.compat import unittest

if os.getcwd() not in sys.path: sys.path.insert(0,os.getcwd())
from ferenda.manager import setup_logger; setup_logger('CRITICAL')

# SUT
from ferenda.elements import serialize, deserialize, AbstractElement, UnicodeElement, CompoundElement, TemporalElement, OrdinalElement, PredicateElement, Body, Section, Paragraph, Link, html

class Main(unittest.TestCase):

    def test_serialize_roundtrip(self):
        # Create a elements object tree
        tree = Body([Section([Paragraph(["Hello"]),
                              Paragraph(["World"])],
                             ordinal="1",
                             title="Main section"),
                     Section([42,
                              date(2013,11,27),
                              b'bytestring',
                              {'foo': 'bar',
                               'x': 'y'}],
                             ordinal=2,
                             title="Native types")
                 ])
        serialized = serialize(tree)
        self.assertIsInstance(serialized, str)
        newtree = deserialize(serialized, globals())
        self.assertEqual(tree, newtree)

    def test_serialize_pyparsing(self):
        # these objects can't be roundtripped
        from ferenda.citationpatterns import url
        x = url.parseString("http://example.org/foo?param=val")
        serialized = serialize(Body([x]))
        self.assertEqual("""<Body>
  <url>
    <scheme>http</scheme>
    <netloc>example.org</netloc>
    <path>/foo</path>
    <query>param=val</query>
  </url>
</Body>
""", serialized)
        
        

    def test_abstract(self):
        x = AbstractElement()
        with self.assertRaises(AttributeError):
            x.foo = "bar"

        self.assertEqual(b'<abstractelement xmlns="http://www.w3.org/1999/xhtml"/>',
                         etree.tostring(x.as_xhtml()))
        

    def test_compound(self):
        x = CompoundElement(["hello", "world"], id="42", foo="bar")
        x.foo = "baz"
        with self.assertRaises(AttributeError):
            x.y = "z"
        x.append(os.listdir) # a non-serializable object (in this case a function)
        self.assertEqual(b'<compoundelement xmlns="http://www.w3.org/1999/xhtml" id="42">helloworld&lt;built-in function listdir&gt;</compoundelement>',
                         etree.tostring(x.as_xhtml()))
        self.assertEqual(Body([Section([Paragraph(["Hello"]),
                                        Paragraph(["World"])])]).as_plaintext(),
                         "Hello World")
        

    def test_unicode(self):
        x = UnicodeElement("Hello world", id="42")
        self.assertEqual(b'<unicodeelement xmlns="http://www.w3.org/1999/xhtml" id="42">Hello world</unicodeelement>',
                         etree.tostring(x.as_xhtml()))

        with self.assertRaises(TypeError):
            UnicodeElement(b'bytestring')
        
    def test_temporal(self):
        class TemporalString(UnicodeElement, TemporalElement): pass
        x = TemporalString("Hello", entryintoforce=date(2013,1,1),
                           expires=date(2014,1,1))
        self.assertFalse(x.in_effect(date(2012,7,1)))
        self.assertTrue(x.in_effect(date(2013,7,1)))
        self.assertFalse(x.in_effect(date(2014,7,1)))
                                                        
    def test_ordinal(self):
        class OrdinalString(UnicodeElement, OrdinalElement): pass
        x = OrdinalString("Foo", ordinal="2")
        y = OrdinalString("Bar", ordinal="2 a")
        z = OrdinalString("Baz", ordinal="10")
        w = OrdinalString("Duplicate of Foo", ordinal="2")
        self.assertTrue(x < y < z)
        self.assertTrue(z > y > x)
        self.assertTrue(x != y)
        self.assertTrue(x == w)
        self.assertTrue(x <= w <= y)
        self.assertTrue(y >= w >= x)
        
    def test_predicate(self):
        class PredicateString(UnicodeElement, PredicateElement): pass
        # known vocabulary used
        x = PredicateString("This is my title", predicate="http://purl.org/dc/terms/title")
        self.assertEqual("dct:title", x.predicate)

        # unknown vocabulary used
        y = PredicateString("This is my title", predicate="http://example.org/vocab/title")
        self.assertEqual("http://example.org/vocab/title", y.predicate)

        # No predicate used --- default to rdfs:Resource
        z = PredicateString("This is a resource")
        from rdflib import RDFS
        self.assertEqual(RDFS.Resource, z.predicate)

    def test_link(self):
        x = Link("Link text", uri="http://example.org/")
        self.assertEqual("Link text", str(x))
        self.assertEqual("Link('Link text', uri=http://example.org/)", repr(x))
        
    def test_elements_from_soup(self):
        soup = BeautifulSoup("""<html>
<head>
  <title>Example doc</title>
</head>
<body>
  <marquee>Hello world</marquee>
  <!-- Hello world -->
  <center>Hello world</center>
  <p>That's enough of this nonsense</p>
</body>""")
        got = html.elements_from_soup(soup.html)
        self.assertEqual(html.HTML([html.Head([html.Title(["Example doc"])]),
                                    html.Body([html.P(["That's enough of this nonsense"])])]),
                         got)

        
import doctest
def load_tests(loader,tests,ignore):
    from ferenda.elements import elements
    tests.addTests(doctest.DocTestSuite(elements))
    return tests
