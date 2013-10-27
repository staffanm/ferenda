# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import sys, os, tempfile, shutil
from datetime import date
from six import text_type as str
from lxml import etree

from ferenda.compat import unittest

if os.getcwd() not in sys.path: sys.path.insert(0,os.getcwd())
from ferenda.manager import setup_logger; setup_logger('CRITICAL')

# SUT
from ferenda.elements import serialize, deserialize, AbstractElement, UnicodeElement, CompoundElement, Body, Section, Paragraph

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
        
