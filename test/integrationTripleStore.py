# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import sys, os
from ferenda.compat import unittest


import time
import subprocess
import os
import tempfile
import shutil
import logging
import json

from six import text_type as str
from rdflib import Graph
from rdflib.util import guess_format
from rdflib.compare import graph_diff, isomorphic
from ferenda import util, errors

from ferenda.triplestore import TripleStore, SleepycatStore, FusekiStore

from ferenda.testutil import FerendaTestCase

class TripleStoreTestCase(FerendaTestCase):
    
    # Set this to True if you want module-level text fixtures to
    # automatically start and stop the triple store's process for you.
    manage_server = False

    store = None

    def test_add_serialized(self):
        # test adding to default graph
        self.assertEqual(0,self.store.triple_count())
        self.store.add_serialized(
            util.readfile("test/files/datasets/dataset.nt"),
            format="nt")
        self.assertEqual(7,self.store.triple_count())

    def test_add_serialized_named_graph(self):
        self.test_add_serialized() # set up environment for this case
        self.store.add_serialized(
            util.readfile("test/files/datasets/dataset2.nt"),
            format="nt", context="http://example.org/ctx1")
        self.assertEqual(3,self.store.triple_count(
            context="http://example.org/ctx1"))
        self.assertEqual(10,self.store.triple_count())

    def test_add_contexts(self):
        self.store.add_serialized(
            util.readfile("test/files/datasets/movies.ttl"),
            format="turtle", context="http://example.org/movies")
        self.assertEqual(21, self.store.triple_count(
            context="http://example.org/movies"))
        self.store.add_serialized(
            util.readfile("test/files/datasets/actors.ttl"),
            format="turtle", context="http://example.org/actors")
        self.assertEqual(18, self.store.triple_count(
            context="http://example.org/actors"))
        self.assertEqual(39, self.store.triple_count())
        dump = self.store.get_serialized(format="nt")
        self.assertTrue(len(dump) > 10) # to account for any spurious
                                        # newlines -- real dump should
                                        # be over 4K
        self.store.clear(context="http://example.org/movies")
        self.assertEqual(0, self.store.triple_count("http://example.org/movies"))
        self.assertEqual(18, self.store.triple_count())
        self.store.clear(context="http://example.org/actors")
        self.assertEqual(0, self.store.triple_count())
        
    def test_add_serialized_file(self):
        self.assertEqual(0,self.store.triple_count())

        # default graph
        self.store.add_serialized_file("test/files/datasets/dataset.nt",
                                       format="nt")
        self.assertEqual(7,self.store.triple_count())
        # named graph
        self.store.add_serialized_file("test/files/datasets/dataset2.nt",
                                       format="nt",
                                       context="http://example.org/ctx1")
        self.assertEqual(3,self.store.triple_count(
            context="http://example.org/ctx1"))
        self.assertEqual(10,self.store.triple_count())

    def test_roundtrip(self):
        data = b'<http://example.org/1> <http://purl.org/dc/terms/title> "language literal"@sv .'
        self.store.add_serialized(data, format="nt")
        res = self.store.get_serialized(format="nt").strip()
        self.assertEqual(res, data)

    def test_clear(self):
        data = """<http://example.org/1> <http://purl.org/dc/terms/title> "language literal"@sv .\n\n"""
        self.store.add_serialized(data, format="nt")
        res = self.store.clear()
        self.assertEqual(0,self.store.triple_count())
        
    def test_get_serialized(self):
        self.loader.add_serialized(util.readfile("test/files/datasets/dataset.nt"),format="nt")
        del self.loader
        res = self.store.get_serialized(format="nt")
        self.assertEqualGraphs(Graph().parse(data=util.readfile("test/files/datasets/dataset.nt"), format="nt"),
                               Graph().parse(data=res, format="nt"))

    def test_get_serialized_file(self):
        want = tempfile.mktemp(suffix=".nt")
        util.writefile(want, util.readfile("test/files/datasets/dataset.nt"))
        got = tempfile.mktemp(suffix=".nt")
        self.loader.add_serialized(
            util.readfile("test/files/datasets/dataset.nt"),format="nt")
        del self.loader
        self.store.get_serialized_file(got, format="nt")
        self.assertEqualGraphs(want,got)
        
    def test_select(self):
        self.loader.add_serialized(
            util.readfile("test/files/datasets/movies.ttl"),
            format="turtle", context="http://example.org/movies")
        self.loader.add_serialized(
            util.readfile("test/files/datasets/actors.ttl"),
            format="turtle", context="http://example.org/actors")
        del self.loader
        # test1: the simplest possible select
        sq = """PREFIX foaf: <http://xmlns.com/foaf/0.1/>
                PREFIX owl: <http://www.w3.org/2002/07/owl#>

                SELECT ?name
                WHERE  { GRAPH <http://example.org/actors> { ?uri foaf:name ?name .
                        ?uri owl:sameAs <http://live.dbpedia.org/resource/Kevin_Bacon> } }"""

        p = self.store.select(sq,"python")
        self.assertIsInstance(p[0]['name'], str)
        self.assertEqual(p,[{'name':'Kevin Bacon'}])

        # test 2:select across graphs and retrieve results with non-ascii chars
        sq = """PREFIX foaf: <http://xmlns.com/foaf/0.1/>
                PREFIX owl: <http://www.w3.org/2002/07/owl#>
                PREFIX schema: <http://schema.org/>
                SELECT ?moviename
                WHERE { ?actoruri owl:sameAs <http://live.dbpedia.org/resource/Kevin_Bacon> .
                        ?movieuri schema:actor ?actoruri;
                                  schema:name ?moviename .
                        FILTER(langMatches(lang(?moviename), "tr"))
                }
        """
        p = self.store.select(sq, "python")
        self.assertIsInstance(p[0]['moviename'], str)
        self.assertEqual(p, [{'moviename': 'Kardeş Gibiydiler'}])

        if self.store.__class__ == SleepycatStore:
            self.store.graph.close()
        
    def test_construct(self):
        self.loader.add_serialized(
            util.readfile("test/files/datasets/addressbook.ttl"),
            format="turtle")
        del self.loader

        sq = """PREFIX ab: <http://learningsparql.com/ns/addressbook#>
                PREFIX d: <http://learningsparql.com/ns/data#>

                CONSTRUCT { ?person ?p ?o . }
                WHERE {
                    ?person ab:firstName "Craig" ; ab:lastName "Ellis" ;
                ?p ?o . }"""
        want = Graph()
        want.parse(data="""
@prefix d:<http://learningsparql.com/ns/data#> . 
@prefix ab:<http://learningsparql.com/ns/addressbook#> .

d:i8301
    ab:email "c.ellis@usairwaysgroup.com",
             "craigellis@yahoo.com" ;
    ab:firstName "Craig" ;
    ab:lastName "Ellis" .
""", format="turtle")
        if self.store.__class__ == FusekiStore:
            got = self.store.construct(sq, uniongraph=False)
        else:
            got = self.store.construct(sq)

        # self.assertTrue(isomorphic(want,got))
        self.assertEqualGraphs(want, got, exact=True)
        if self.store.__class__ == SleepycatStore:
            self.store.graph.close()

    def test_construct_annotations(self):
        self.loader.add_serialized(
            util.readfile("test/files/datasets/repo_a.ttl"), format="turtle")
        self.loader.add_serialized(
            util.readfile("test/files/datasets/repo_b.ttl"), format="turtle")

        # NOTE: The real mechanism for constructing the SPARQL query
        # (in construct_annotations) is more complex, but this gets
        # the same result in the base case.
        uri = "http://example.org/repo/a/1"
        sq = util.readfile("ferenda/res/sparql/annotations.rq") % {'uri': uri}
        # FIXME: stupid Fuseki workaround
        if self.storetype == "FUSEKI":
            got = self.store.construct(sq, uniongraph=False)
        else:
            got = self.store.construct(sq)
        want = Graph()
        want.parse(data=util.readfile("test/files/datasets/annotations_a1.ttl"),
                   format="turtle")
        self.assertEqualGraphs(want, got, exact=True)

    def test_construct_annotations_rfc(self):
        # print("Not loading, re-using data")
        self.loader.add_serialized(
             util.readfile("test/files/datasets/rfc.nt"), format="nt",
            context="http://localhost:8000/dataset/rfc"
        )

        uri = "http://localhost:8000/res/rfc/7066"
        sq = util.readfile("ferenda/sources/tech/res/sparql/rfc-annotations.rq") % {'uri': uri}
        got = self.store.construct(sq)
        want = Graph()
        want.parse(data=util.readfile("test/files/datasets/annotations-rfc.nt"),
                   format="nt")
        self.assertEqualGraphs(want, got, exact=True)

    def test_facet_query(self):
        results1 = json.load(open("test/files/datasets/results1.json"))
        results2 = json.load(open("test/files/datasets/results2.json"))

        self.loader.add_serialized(
            util.readfile("test/files/datasets/books.ttl"),
            format="turtle", context="http://example.org/ctx/base")
        self.loader.add_serialized(
            util.readfile("test/files/datasets/articles.ttl"),
            format="turtle", context="http://example.org/ctx/other")

        # Since the query is partially constructed by DocumentRepository, we
        # need to run that code.
        import rdflib
        from ferenda import DocumentRepository
        repo = DocumentRepository()
        repo.config.storetype = self.storetype
        repo.rdf_type = rdflib.URIRef("http://purl.org/ontology/bibo/Book")

        # test 1
        sq = repo.facet_query("http://example.org/ctx/base")
        got = self.store.select(sq, format="python")
        self.assertEqual(len(got), len(results1))
        for row in results1:
            self.assertIn(row, got)

        # test 2
        sq = repo.facet_query("http://example.org/ctx/other")
        got = self.store.select(sq, format="python")
        self.assertEqual(len(got), len(results2))
        for row in results2:
            self.assertIn(row, got)

        if self.storetype == "SLEEPYCAT":
            self.store.graph.close()


    def test_invalid_select(self):
        with self.assertRaises(errors.SparqlError):
            self.store.select("This is not a valid SPARQL query")

    def test_invalid_construct(self):
        with self.assertRaises(errors.SparqlError):
            self.store.construct("This is not a valid SPARQL query")

@unittest.skipIf('SKIP_FUSEKI_TESTS' in os.environ,
                 "Skipping Fuseki tests")    
class Fuseki(TripleStoreTestCase, unittest.TestCase):
    storetype = "FUSEKI"
    @classmethod
    def setUpClass(cls):
        if cls.manage_server:
            # Note: In order for this to work, the script "fuseki"
            # must be in PATH, and FUSEKI_HOME must be set to the
            # directory of that script (which should also contain
            # fuseki-server.jar)
            # assume that the config.ttl from the fuseki distribution is
            # used, creating an updateable in-memory dataset at /ferenda
            subprocess.check_call("fuseki start > /dev/null", shell=True)
            # It seems to take a little while from the moment that `fuseki
            # start' returns to when the HTTP service actually is up and
            # running
            time.sleep(3)

    @classmethod
    def tearDownClass(cls):
        if cls.manage_server:
            subprocess.check_call("fuseki stop > /dev/null", shell=True)
        pass

    def setUp(self):       
        self.store = TripleStore.connect(self.storetype, "http://localhost:3030/", "ferenda")
        # print("Not clearing http://localhost:3030/ferenda")
        self.store.clear()
        self.loader = self.store


@unittest.skipIf('SKIP_FUSEKI_TESTS' in os.environ,
                 "Skipping Fuseki/curl tests")    
class FusekiCurl(Fuseki):
    def setUp(self):       
        self.store = TripleStore.connect(self.storetype, "http://localhost:3030/", "ferenda", curl=True)
        self.store.clear()
        self.loader = self.store


@unittest.skipIf('SKIP_SESAME_TESTS' in os.environ,
                 "Skipping Sesame tests")    
class Sesame(TripleStoreTestCase, unittest.TestCase):
    storetype = "SESAME"
    @classmethod
    def setUpClass(cls):
        # start up tomcat/sesame on port 8080
        if cls.manage_server:
            subprocess.check_call("catalina.sh start > /dev/null", shell=True)
            # It seems to take a little while from the moment that
            # `catalina.sh start' returns to when the HTTP service
            # actually is up and answering.
            time.sleep(1)

    @classmethod
    def tearDownClass(cls):
        if cls.manage_server:
            subprocess.check_call("catalina.sh stop > /dev/null", shell=True)

    def setUp(self):
        self.store = TripleStore.connect(self.storetype, "http://localhost:8080/openrdf-sesame", "ferenda")
        self.store.clear()
        self.loader = self.store

    def tearDown(self):
        pass        


class SesameCurl(Sesame):
    def setUp(self):
        self.store = TripleStore.connect(self.storetype, "http://localhost:8080/openrdf-sesame", "ferenda", curl=True)
        self.store.clear()
        self.loader = self.store


# A mixin class that changes the behaviour of most tests (tests that
# attempt to modify the store, apart from initial loading of data,
# should fail as inmemory stores are read-only).
class Inmemory(object):

    _store = None # the real store object

    # self.store isn't set by the derived setUp methods (only
    # self.loader). Some property magic to make self.store call
    # self.getstore if needed
    @property
    def store(self):
        if self._store is None: # happens for the Inmemory tests
            self._store = self.getstore()
        return self._store

    @store.setter
    def store(self, value):
        self._store = value

    @store.deleter
    def store(self):
        del self._store

    def test_add_contexts(self):
        with self.assertRaises(errors.TriplestoreError):
            super(Inmemory,self).test_add_contexts()

    def test_roundtrip(self):
        with self.assertRaises(errors.TriplestoreError):
            super(Inmemory,self).test_roundtrip()

    def test_clear(self):
        with self.assertRaises(errors.TriplestoreError):
            super(Inmemory,self).test_clear()

    def test_add_serialized_named_graph(self):
        with self.assertRaises(errors.TriplestoreError):
            super(Inmemory,self).test_add_serialized_named_graph()

    def test_add_serialized_file(self):
        with self.assertRaises(errors.TriplestoreError):
            super(Inmemory,self).test_add_serialized_file()

    def test_add_serialized(self):
        with self.assertRaises(errors.TriplestoreError):
            super(Inmemory,self).test_add_serialized()
        
class SQLite(TripleStoreTestCase,unittest.TestCase):
    storetype = "SQLITE"
    def setUp(self):
        self.store = TripleStore.connect(self.storetype, "ferenda.sqlite", "ferenda")
        self.store.clear()
        self.loader = self.store

    def tearDown(self):
        self.store.close()
        del self.store
        os.remove("ferenda.sqlite")


class SQLiteInmemory(Inmemory, SQLite):

    def setUp(self):
        self.loader = TripleStore.connect(self.storetype, "ferenda.sqlite", "ferenda")
        self.loader.clear()

    def getstore(self):
        return TripleStore.connect("SQLITE", "ferenda.sqlite", "ferenda", inmemory=True)


@unittest.skipIf('SKIP_SLEEPYCAT_TESTS' in os.environ,
                 "Skipping Sleepycat tests")    
class Sleepycat(TripleStoreTestCase, unittest.TestCase):
    storetype = "SLEEPYCAT"

    def setUp(self):
        self.store = TripleStore.connect(self.storetype, "ferenda.db", "ferenda")
        self.store.clear()
        self.loader = self.store

    def tearDown(self):
        del self.store
        if hasattr(self,'loader'):
            del self.loader
        if os.path.exists("ferenda.db"):
            shutil.rmtree("ferenda.db")


@unittest.skipIf('SKIP_SLEEPYCAT_TESTS' in os.environ,
                 "Skipping Sleepycat/inmemory tests")    
class SleepycatInmemory(Inmemory, Sleepycat):

    def setUp(self):
        self.loader = TripleStore.connect(self.storetype, "ferenda.db", "ferenda")
        self.loader.clear()
        self.store = None

    def getstore(self):
        return TripleStore.connect("SLEEPYCAT", "ferenda.db", "ferenda", inmemory=True)

