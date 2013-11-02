# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from rdflib import Graph

from ferenda.compat import unittest, patch


# SUT
from ferenda import testutil


class Main(unittest.TestCase):
    def setUp(self):
        class Tester(testutil.FerendaTestCase):
            def assertLessEqual(self, x,y,z): pass
            def assertEqual(self, want, got): pass
            def assertTrue(self, stmt): pass
            def fail(self, msg): return msg
        self.tester = Tester()
            
    def test_equalgraphs(self):
        msg = self.tester.assertEqualGraphs(
            Graph(identifier="a").parse(data="""
@prefix foaf: <http://xmlns.com/foaf/0.1/> .
@prefix a: <http://example.org/actors/> .
a:nm0000102 a foaf:Person .
            """, format="turtle"),
            Graph(identifier="b").parse(data="""
@prefix foaf: <http://xmlns.com/foaf/0.1/> .
@prefix a: <http://example.org/actors/> .
a:nm0000134 a foaf:Person .
            """, format="turtle"),
            exact=True)
        self.assertEqual("""<Graph identifier=a (<class 'rdflib.graph.Graph'>)> != <Graph identifier=b (<class 'rdflib.graph.Graph'>)>
1 unexpected triples were found
1 expected triples were not found
- <http://example.org/actors/nm0000102> <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> <http://xmlns.com/foaf/0.1/Person>
+ <http://example.org/actors/nm0000134> <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> <http://xmlns.com/foaf/0.1/Person>
""", msg)


    def test_equalxml(self):
        msg = self.tester.assertEqualXML(
            """<foo arg1='x' arg2='y'/>""",
            """<foo arg2='y' arg1='x'/>"""
            )
        self.assertEqual(None, msg)
        msg = self.tester.assertEqualXML(
            """<foo arg1='x' arg2='y'/>""",
            """<bar arg2='y' arg1='x'/>"""
            )
        self.assertEqual("""--- want.xml
+++ got.xml
@@ -1 +1 @@
-<foo arg1="x" arg2="y"></foo>
+<bar arg1="x" arg2="y"></bar>


ERRORS:Tags do not match: 'want': foo, 'got': bar""", msg)

        msg = self.tester.assertEqualXML(
            """<foo arg1='x' arg2='y'/>""",
            """<foo arg2='z' arg1='x'/>"""
            )
        self.assertEqual("""--- want.xml
+++ got.xml
@@ -1 +1 @@
-<foo arg1="x" arg2="y"></foo>
+<foo arg1="x" arg2="z"></foo>


ERRORS:Attributes do not match: 'want': arg2='y', 'got': arg2='z'""", msg)

        msg = self.tester.assertEqualXML(
            """<foo arg1='x'/>""",
            """<foo arg1='x' arg2='y'/>"""
            )
        self.assertEqual("""--- want.xml
+++ got.xml
@@ -1 +1 @@
-<foo arg1="x"></foo>
+<foo arg1="x" arg2="y"></foo>


ERRORS:'got' has an attribute 'want' is missing: arg2""", msg)
            
