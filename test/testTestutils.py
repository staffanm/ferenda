# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from datetime import datetime
from copy import copy
import collections
import json
import os
import shutil
import tempfile

from lxml import etree
from rdflib import Graph
from bs4 import BeautifulSoup
from six import text_type as str
import six

from ferenda.compat import unittest, patch, Mock
from ferenda import util
from ferenda import DocumentRepository

# SUT
from ferenda import testutil

class Tester(testutil.FerendaTestCase, unittest.TestCase):
    def runTest(self):
        pass


class Main(unittest.TestCase):
    maxDiff = None
    
    def setUp(self):
        self.tester = Tester()


class EqualGraphs(Main):
    graph_a = """@prefix foaf: <http://xmlns.com/foaf/0.1/> .
@prefix a: <http://example.org/actors/> .
a:nm0000102 a foaf:Person .
"""
    graph_b = """@prefix foaf: <http://xmlns.com/foaf/0.1/> .
@prefix a: <http://example.org/actors/> .
a:nm0000134 a foaf:Person .
            """
    graph_a_nt = """<http://example.org/actors/nm0000102> <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> <http://xmlns.com/foaf/0.1/Person> .
"""

    def test_inequalgraphs(self):
        wantmsg = """<Graph identifier=a (<class 'rdflib.graph.Graph'>)> != <Graph identifier=b (<class 'rdflib.graph.Graph'>)>
1 unexpected triples were found
1 expected triples were not found
- <http://example.org/actors/nm0000102> <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> <http://xmlns.com/foaf/0.1/Person>
+ <http://example.org/actors/nm0000134> <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> <http://xmlns.com/foaf/0.1/Person>
"""
        with self.assertRaises(AssertionError) as cm:
            self.tester.assertEqualGraphs(
                Graph(identifier="a").parse(data=self.graph_a, format="turtle"),
                Graph(identifier="b").parse(data=self.graph_b, format="turtle"),
                exact=True)
        self.assertEqual(str(cm.exception), wantmsg)

    def test_loadgraphs(self):
        with open("graph_a.ttl", "w") as fp:
            fp.write(self.graph_a)
        with open("graph_a.nt", "w") as fp:
            fp.write(self.graph_a_nt)
        self.tester.assertEqualGraphs("graph_a.ttl", "graph_a.nt")
        util.robust_remove("graph_a.ttl")
        util.robust_remove("graph_a.nt")


class EqualXML(Main):
    def test_ok(self):
        self.tester.assertEqualXML(
            """<foo arg1='x' arg2='y'/>""",
            """<foo arg2='y' arg1='x'/>"""
            )

    def test_ok_ns_naive(self):
        self.tester.assertEqualXML(
            """<foo xmlns="http://example.org/ns" arg1='x' arg2='y'/>""",
            """<foo xmlns="http://example.org/ns2" arg2='y' arg1='x'/>""",
            namespace_aware=False)

    def test_nonmatching_tags(self):
        wantmsg = """--- want.xml
+++ got.xml
@@ -1 +1 @@
-<foo arg1="x" arg2="y"></foo>
+<bar arg1="x" arg2="y"></bar>


ERRORS:Tags do not match: 'want': foo, 'got': bar"""
        with self.assertRaises(AssertionError) as cm:
            self.tester.assertEqualXML(
                """<foo arg1='x' arg2='y'/>""",
                """<bar arg2='y' arg1='x'/>"""
            )
        self.assertEqual(str(cm.exception), wantmsg)

    def test_nonmatching_attribute_values(self):
        wantmsg = """--- want.xml
+++ got.xml
@@ -1 +1 @@
-<foo arg1="x" arg2="y"></foo>
+<foo arg1="x" arg2="z"></foo>


ERRORS:Attributes do not match: 'want': arg2='y', 'got': arg2='z'"""
        with self.assertRaises(AssertionError) as cm:
            self.tester.assertEqualXML(
                """<foo arg1='x' arg2='y'/>""",
                """<foo arg2='z' arg1='x'/>"""
                )
        self.assertEqual(str(cm.exception), wantmsg)

    def test_missing_attribute(self):
        wantmsg = """--- want.xml
+++ got.xml
@@ -1 +1 @@
-<foo arg1="x"></foo>
+<foo arg1="x" arg2="y"></foo>


ERRORS:'got' has an attribute 'want' is missing: arg2"""

        with self.assertRaises(AssertionError) as cm:
            self.tester.assertEqualXML(
                """<foo arg1='x'/>""",
                """<foo arg1='x' arg2='y'/>"""
            )
        self.assertEqual(str(cm.exception), wantmsg)
            
    def test_nonmatching_text(self):
        wantmsg = """--- want.xml
+++ got.xml
@@ -1 +1 @@
-<foo>Foo</foo>
+<foo>Bar</foo>


ERRORS:text: 'want': 'Foo' != 'got': 'Bar'"""
        with self.assertRaises(AssertionError) as cm:
            self.tester.assertEqualXML(
                """<foo>Foo</foo>""",
                """<foo>Bar</foo>"""
            )
        self.assertEqual(str(cm.exception), wantmsg)

    def test_children_len(self):
        wantmsg = """--- want.xml
+++ got.xml
@@ -1 +1 @@
-<foo><bar></bar></foo>
+<foo><bar></bar><bar></bar></foo>


ERRORS:children length differs, 'want': 1, 'got': 2"""
        with self.assertRaises(AssertionError) as cm:
            self.tester.assertEqualXML(
                """<foo><bar/></foo>""",
                """<foo><bar/><bar/></foo>"""
            )
        self.assertEqual(str(cm.exception), wantmsg)

    def test_nonmatching_tail(self):
        wantmsg = """--- want.xml
+++ got.xml
@@ -1 +1 @@
-<foo><bar></bar>a</foo>
+<foo><bar></bar>b</foo>


ERRORS:tail: 'want': 'a' != 'got': 'b'
children 1 do not match: bar"""
        with self.assertRaises(AssertionError) as cm:
            self.tester.assertEqualXML(
                """<foo><bar/>a</foo>""",
                """<foo><bar/>b</foo>"""
            )
        self.assertEqual(str(cm.exception), wantmsg)

    def test_ok_binary(self):
        self.tester.assertEqualXML(
            b"<foo arg1='x' arg2='y'/>",
            b"<foo arg2='y' arg1='x'/>"
        )

    def test_ok_element(self):
        self.tester.assertEqualXML(
            etree.fromstring("<foo arg1='x' arg2='y'/>"),
            etree.fromstring("<foo arg2='y' arg1='x'/>")
        )

    def test_soup(self):
        # should fail - soup's not one of the argument types we handle
        with self.assertRaises(ValueError):
            self.tester.assertEqualXML(
                BeautifulSoup("<foo arg1='x' arg2='y'/>"),
                BeautifulSoup("<foo arg2='y' arg1='x'/>"))

    def test_tidy_html(self):
        w = "<html><body><h1>Hi</h1></body></html>"
        g = """<html>
  <body>
    <h1>
      Ho
    </h1>
  </body>
</html>"""
        wantmsg = """--- want.xml
+++ got.xml
@@ -6 +6 @@
 </head>
 
 <body>
-  <h1>Hi</h1>
+  <h1>Ho</h1>
 </body>
 </html>


ERRORS:text: 'want': 'Hi' != 'got': '\\n      Ho\\n    '
children 1 do not match: h1
children 1 do not match: body"""
        with self.assertRaises(AssertionError) as cm:
            self.tester.assertEqualXML(w, g, tidy_xhtml=True)
        self.assertEqual(str(cm.exception), wantmsg)


class AlmostEqualDatetime(Main):
    def test_within_tolerance(self):
        self.tester.assertAlmostEqualDatetime(
            datetime(2015, 2, 15, 13, 18, 5),
            datetime(2015, 2, 15, 13, 18, 7),
            3)

    def test_outside_tolerance(self):
        with self.assertRaises(AssertionError) as cm:
            self.tester.assertAlmostEqualDatetime(
                datetime(2015, 2, 15, 13, 18, 5),
                datetime(2015, 2, 15, 13, 18, 7),
                1)
        # the expected error message differs between python 2 and 3, so we just check the end
        self.assertTrue(str(cm.exception).endswith("Difference between 2015-02-15T13:18:05 and "
                                                   "2015-02-15T13:18:07 is 2.0 seconds which is "
                                                   "NOT almost equal"))


class EqualDirs(Main):

    def setUp(self):
        super(EqualDirs, self).setUp()
        self.datadir = tempfile.mkdtemp()
        util.writefile(self.datadir + "/want/one.txt", "Contents of one")
        util.writefile(self.datadir + "/got/one.txt", "Contents of one")
        util.writefile(self.datadir + "/want/sub/two.text", "Contents of two")
        util.writefile(self.datadir + "/got/sub/two.text", "Contents of two")

    def tearDown(self):
        shutil.rmtree(self.datadir)

    def test_equal(self):
        self.tester.assertEqualDirs(self.datadir+"/want", self.datadir+"/got")

    def test_equal_suffix(self):
        util.writefile(self.datadir + "/got/sub/two.text",
                       "Different contents of two")
        self.tester.assertEqualDirs(self.datadir+"/want", self.datadir+"/got",
                                    suffix=".txt")

    def test_equal_subset(self):
        util.writefile(self.datadir + "/got/sub/three.txt", "Contents of 3")
        self.tester.assertEqualDirs(self.datadir+"/want", self.datadir+"/got",
                                    subset=True)
        pass

    def test_inequal(self):
        util.writefile(self.datadir + "/want/sub/three.txt",
                       "Contents of 3")
        with self.assertRaises(AssertionError):
            self.tester.assertEqualDirs(self.datadir+"/want",
                                        self.datadir+"/got")

    def test_inequal_content(self):
        util.writefile(self.datadir + "/got/sub/two.text",
                       "Different contents of two")
        with self.assertRaises(AssertionError):
            self.tester.assertEqualDirs(self.datadir+"/want",
                                        self.datadir+"/got")


class MyRepo(DocumentRepository):
    pass

class TestRepo(unittest.TestCase, testutil.FerendaTestCase):
    maxDiff = None

    def setUp(self):
        self.datadir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.datadir)


    def _runtest(self):
        # create a new class and set its docroot (like functionalSources does)
        name = "TestMyRepo"
        if six.PY2:
            name = name.encode()
        the_new_class = type(name, (testutil.RepoTester,),
                             {'repoclass': MyRepo,
                              'docroot': self.datadir})
        testutil.parametrize_repotester(the_new_class, include_failures=False)
        # now we need to load tests and execute one of them
        # can't find a way to load a single test from a class
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(the_new_class)
        self.assertEqual(1, suite.countTestCases())
        result = unittest.TestResult()
        suite.run(result)  # can't find a way to run a single test from a suite
        if result.errors:
            # fail with first trackback
            self.fail(result.errors[0][1])
        elif result.failures:
            self.fail(result.failures[0][1])
        self.assertTrue(result.wasSuccessful())


    basicjson = {'@settings': {'config': {'refresh': True}},
                 'http://example.org/': {'file': 'index.html'},
                 'http://example.org/doc/a_.html':
                 {'file': 'a_.html',
                  'expect': 'downloaded/a.html'}}

    def test_download(self):
        # create a basic.json + 1-2 resources
        os.mkdir(self.datadir+"/source")
        with open(self.datadir+"/source/basic.json", "w") as fp:
            json.dump(self.basicjson,
                      fp)
        util.writefile(self.datadir+"/source/index.html",
                       "<p><a href='doc/a_.html'>ID: a</a></p>")
        util.writefile(self.datadir+"/source/a_.html",
                       "<p>This is doc A</p>")
        self._runtest()


    # This gets called by RepoTester.download_test in a horrifying
    # way.
    def _myget(self, url, **kwargs):
        res = Mock()
        res.headers = collections.defaultdict(lambda:None)
        res.headers['Content-type'] = "text/html"
        res.status_code = 200
        res.encoding = 'utf-8'
        if url == "http://example.org/":
            res.content = b"<p><a href='doc/a_.html'>ID: a</a></p>"
        elif url == "http://example.org/doc/a_.html":
            res.content = b"<p>This is doc A</p>"
        else:
            raise ValueError("Unknown url %s" % url)
        res.text = res.content.decode()
        return res
    
    def test_download_setfile(self):
        # create a empty.json
        os.mkdir(self.datadir+"/source")
        with open(self.datadir+"/source/empty.json", "w") as fp:
            json.dump({'@settings': {'config': {'refresh': True}}},
                      fp)

        os.environ["FERENDA_SET_TESTFILE"] = "true"
        self._runtest()
        del os.environ["FERENDA_SET_TESTFILE"]

        # make sure downloaded files have been placed where they
        # should + empty.json has correct content.
        self.assertTrue(os.path.exists(self.datadir+"/source/empty-0.html"))
        self.assertEqual("<p>This is doc A</p>",
                         util.readfile(self.datadir+"/source/empty-1.html"))
                         
        with open(self.datadir+"/source/empty.json") as fp:
            gotjson = json.load(fp)
        wantjson = copy(self.basicjson)
        wantjson['http://example.org/']['file'] = "empty-0.html"
        wantjson['http://example.org/doc/a_.html']['file'] = "empty-1.html"
        self.assertEqual(wantjson, gotjson)
        
        pass

    expected_xhtml = """<?xml version='1.0' encoding='utf-8'?>
<html xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns:prov="http://www.w3.org/ns/prov#" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:foaf="http://xmlns.com/foaf/0.1/" xmlns="http://www.w3.org/1999/xhtml" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" version="XHTML+RDFa 1.1" xsi:schemaLocation="http://www.w3.org/1999/xhtml http://www.w3.org/MarkUp/SCHEMA/xhtml-rdfa-2.xsd" xml:lang="en">
  <head about="http://localhost:8000/res/base/a">
    <meta property="dcterms:identifier" content="a" xml:lang=""/>
    <link rel="rdf:type" href="http://xmlns.com/foaf/0.1/Document"/>
    <meta property="prov:wasGeneratedBy" content="testTestutils.MyRepo" xml:lang=""/>
  </head>
  <body about="http://localhost:8000/res/base/a">
    <p>This is doc A</p>
  </body>
</html>
"""

    def test_parse(self):
        os.mkdir(self.datadir+"/downloaded")
        util.writefile(self.datadir+"/downloaded/a.html",
                       "<p>This is doc A</p>")
        util.writefile(self.datadir+"/parsed/a.xhtml", self.expected_xhtml)
        self._runtest()

    def test_parse_setfile(self):
        os.mkdir(self.datadir+"/downloaded")
        util.writefile(self.datadir+"/downloaded/a.html",
                       "<p>This is doc A</p>")
        util.writefile(self.datadir+"/parsed/a.xhtml",  "")
        os.environ["FERENDA_SET_TESTFILE"] = "1"
        self._runtest()
        del os.environ["FERENDA_SET_TESTFILE"]
        self.assertEqualXML(self.expected_xhtml,
                            util.readfile(self.datadir+"/parsed/a.xhtml"))
        pass

    expected_ttl = """@prefix dcterms: <http://purl.org/dc/terms/> .
@prefix foaf: <http://xmlns.com/foaf/0.1/> .
@prefix prov: <http://www.w3.org/ns/prov#> .
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xml: <http://www.w3.org/XML/1998/namespace> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

<http://localhost:8000/res/base/a> a foaf:Document ;
    dcterms:identifier "a" ;
    prov:wasGeneratedBy "testTestutils.MyRepo" .

"""

    def test_distill(self):
        os.mkdir(self.datadir+"/downloaded")
        util.writefile(self.datadir+"/downloaded/a.html",
                       "<p>This is doc A</p>")
        util.writefile(self.datadir+"/distilled/a.ttl", self.expected_ttl)
        self._runtest()

    def test_distill_setfile(self):
        os.mkdir(self.datadir+"/downloaded")
        util.writefile(self.datadir+"/downloaded/a.html",
                       "<p>This is doc A</p>")
        util.writefile(self.datadir+"/distilled/a.ttl",  "")
        os.environ["FERENDA_SET_TESTFILE"] = "1"
        self._runtest()
        del os.environ["FERENDA_SET_TESTFILE"]
        self.assertEqual(self.expected_ttl,
                         util.readfile(self.datadir+"/distilled/a.ttl"))
        pass

