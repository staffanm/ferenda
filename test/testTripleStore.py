#!/usr/bin/env python
# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import sys, os
if sys.version_info < (2,7,0):
    import unittest2 as unittest
else:
    import unittest
# if os.getcwd() not in sys.path: sys.path.insert(0,os.getcwd())

import time
import subprocess
import os
import tempfile
import shutil
import logging

from six import text_type as str
from rdflib import Graph
from rdflib.util import guess_format
from rdflib.compare import graph_diff, isomorphic
from ferenda import util, errors

from ferenda.triplestore import TripleStore, SleepycatStore

from ferenda.testutil import FerendaTestCase

class TripleStoreTestCase(FerendaTestCase):
    
    # Set this to True if you want module-level text fixtures to
    # automatically start and stop the triple store's process for you.
    manage_server = False

    dataset = """<http://localhost/publ/dir/2012:35> <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> <http://rinfo.lagrummet.se/ns/2008/11/rinfo/publ#Direktiv> .
<http://localhost/publ/dir/2012:35> <http://purl.org/dc/terms/identifier> "Dir. 2012:35" .
<http://localhost/publ/dir/2012:35> <http://purl.org/dc/terms/title> "Ett minskat och f\\u00F6renklat uppgiftsl\\u00E4mnande f\\u00F6r f\\u00F6retagen"@sv .
<http://localhost/publ/dir/2012:35> <http://purl.org/dc/terms/published> "2012-04-26"^^<http://www.w3.org/2001/XMLSchema#date> .
<http://localhost/publ/dir/2012:35> <http://www.w3.org/2002/07/owl#sameAs> <http://rinfo.lagrummet.se/publ/dir/2012:35> .
<http://localhost/publ/dir/2012:35> <http://rinfo.lagrummet.se/ns/2008/11/rinfo/publ#departement> <http://lagen.nu/org/2008/naringsdepartementet> .
<http://localhost/publ/dir/2012:35> <http://www.w3.org/ns/prov-o/wasGeneratedBy> "ferenda.sources.Direktiv.DirPolopoly" .
"""
    dataset2 = """
<http://localhost/publ/dir/2012:36> <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> <http://rinfo.lagrummet.se/ns/2008/11/rinfo/publ#Direktiv> .
<http://localhost/publ/dir/2012:36> <http://purl.org/dc/terms/identifier> "Dir. 2012:36" .
<http://localhost/publ/dir/2012:36> <http://purl.org/dc/terms/title> "Barns s\\u00E4kerhet i f\\u00F6rskolan"@sv .
"""
    movies = """
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix schema: <http://schema.org/> .
@prefix foaf: <http://xmlns.com/foaf/0.1/> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix a: <http://example.org/actors/> .
@prefix m: <http://example.org/movies/> .

m:tt0117665 rdf:type schema:Movie;
    schema:name "Sleepers"@en,
                "Kardeş Gibiydiler"@tr;
    schema:actor a:nm0000102,
                 a:nm0000134,
                 a:nm0000093;
    schema:datePublished "1996-10-18"^^xsd:date;
    owl:sameAs <http://www.imdb.com/title/tt0117665/> .

m:tt0137523 rdf:type schema:Movie;
    schema:name "Fight Club"@en,
                "Бойцовский клуб"@ru;
    schema:actor a:nm0000093,
                 a:nm0001570;
    owl:sameAs <http://www.imdb.com/title/tt0137523/> .

m:tt0099685 rdf:type schema:Movie;
    schema:name "Goodfellas"@en,
                "Maffiabröder"@sv;
    schema:actor a:nm0000134,
                 a:nm0000501,
                 a:nm0000582;
    owl:sameAs <http://www.imdb.com/title/tt099685/> .
"""
    actors = """
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix foaf: <http://xmlns.com/foaf/0.1/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix a: <http://example.org/actors/> .

a:nm0000102 rdf:type foaf:Person;
    foaf:name "Kevin Bacon";
    owl:sameAs <http://live.dbpedia.org/resource/Kevin_Bacon> .
    
a:nm0000134 rdf:type foaf:Person;
    foaf:name "Robert De Niro";
    owl:sameAs <http://live.dbpedia.org/resource/Robert_De_Niro> .
    
a:nm0000093 rdf:type foaf:Person;
    foaf:name "Brad Pitt";
    owl:sameAs <http://live.dbpedia.org/resource/Brad_Pitt> .

a:nm0001570 rdf:type foaf:Person;
    foaf:name "Edward Norton";
    owl:sameAs <http://live.dbpedia.org/resource/Edward_Norton> .

a:nm0000501 rdf:type foaf:Person;
    foaf:name "Ray Liotta";
    owl:sameAs <http://live.dbpedia.org/resource/Ray_Liotta> .

a:nm0000582 rdf:type foaf:Person;
    foaf:name "Joe Pesci";
    owl:sameAs <http://live.dbpedia.org/resource/Joe_Pesci> .
"""

    def test_add_serialized(self):
        # test adding to default graph
        self.assertEqual(0,self.store.triple_count())
        self.store.add_serialized(self.dataset,format="nt")
        self.assertEqual(7,self.store.triple_count())

    def test_add_serialized_named_graph(self):
        self.test_add_serialized() # set up environment for this case
        self.store.add_serialized(self.dataset2,format="nt", context="http://example.org/ctx1")
        self.assertEqual(3,self.store.triple_count(context="http://example.org/ctx1"))
        self.assertEqual(10,self.store.triple_count())

    def test_add_contexts(self):
        self.store.add_serialized(self.movies, format="turtle", context="http://example.org/movies")
        self.assertEqual(21, self.store.triple_count(context="http://example.org/movies"))
        self.store.add_serialized(self.actors, format="turtle", context="http://example.org/actors")
        self.assertEqual(18, self.store.triple_count(context="http://example.org/actors"))
        self.assertEqual(39, self.store.triple_count())
        dump = self.store.get_serialized(format="nt")
        self.assertTrue(len(dump) > 10) # to account for any spurious newlines -- real dump should be over 4K
        self.store.clear(context="http://example.org/movies")
        self.assertEqual(0, self.store.triple_count("http://example.org/movies"))
        self.assertEqual(18, self.store.triple_count())
        self.store.clear(context="http://example.org/actors")
        self.assertEqual(0, self.store.triple_count())
        
    def test_add_serialized_file(self):
        self.assertEqual(0,self.store.triple_count())
        tmp1 = tempfile.mktemp()
        with open(tmp1,"w") as fp:
            fp.write(self.dataset)
        tmp2 = tempfile.mktemp()
        with open(tmp2,"w") as fp:
            fp.write(self.dataset2)

        # default graph
        self.store.add_serialized_file(tmp1, format="nt")
        self.assertEqual(7,self.store.triple_count())
        # named graph
        self.store.add_serialized_file(tmp2, format="nt", context="http://example.org/ctx1")
        self.assertEqual(3,self.store.triple_count(context="http://example.org/ctx1"))
        self.assertEqual(10,self.store.triple_count())

        os.unlink(tmp1)
        os.unlink(tmp2)

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
        self.loader.add_serialized(self.dataset,format="nt")
        del self.loader
        res = self.store.get_serialized(format="nt")
        self.assertEqualGraphs(Graph().parse(data=self.dataset, format="nt"),
                               Graph().parse(data=res, format="nt"))

    def test_get_serialized_file(self):
        want = tempfile.mktemp(suffix=".nt")
        util.writefile(want, self.dataset)
        got = tempfile.mktemp(suffix=".nt")
        self.loader.add_serialized(self.dataset,format="nt")
        del self.loader
        self.store.get_serialized_file(got, format="nt")
        self.assertEqualGraphs(want,got)
        
    def test_select(self):
        self.loader.add_serialized(self.movies,format="turtle", context="http://example.org/movies")
        self.loader.add_serialized(self.actors,format="turtle", context="http://example.org/actors")
        del self.loader
        sq = """PREFIX foaf: <http://xmlns.com/foaf/0.1/>
                PREFIX owl: <http://www.w3.org/2002/07/owl#>

                SELECT ?name
                WHERE  { GRAPH <http://example.org/actors> { ?uri foaf:name ?name .
                        ?uri owl:sameAs <http://live.dbpedia.org/resource/Kevin_Bacon> } }"""

        p = self.store.select(sq,"python")
        self.assertIsInstance(p[0]['name'], str)
        self.assertEqual(p,[{'name':'Kevin Bacon'}])
        if self.store.__class__ == SleepycatStore:
            self.store.graph.close()
        
    def test_construct(self):
        self.loader.add_serialized("""
@prefix ab: <http://learningsparql.com/ns/addressbook#> .
@prefix d: <http://learningsparql.com/ns/data#> .

d:i0432 ab:firstName "Richard" .
d:i0432 ab:lastName "Mutt" .
d:i0432 ab:homeTel "(229) 276-5135" .
d:i0432 ab:email "richard49@hotmail.com" .

d:i9771 ab:firstName "Cindy" .
d:i9771 ab:lastName "Marshall" .
d:i9771 ab:homeTel "(245) 646-5488" .
d:i9771 ab:email "cindym@gmail.com" .

d:i8301 ab:firstName "Craig" .
d:i8301 ab:lastName "Ellis" .
d:i8301 ab:email "craigellis@yahoo.com" .
d:i8301 ab:email "c.ellis@usairwaysgroup.com" .
""", format="turtle")
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
        got = self.store.construct(sq)
        self.assertTrue(isomorphic(want,got))
        if self.store.__class__ == SleepycatStore:
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
    @classmethod
    def setUpClass(cls):
        if cls.manage_server:
            # Note: In order for this to work, the script "fuseki"
            # must be in PATH, and FUSEKI_HOME must be set to the
            # directory of that script (which should also contain
            # fuseki-server.jar)
            # assume that the config.ttl from the fuseki distribution is
            # used, creating an updateable in-memory dataset at /ds
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
        self.store = TripleStore.connect("FUSEKI", "http://localhost:3030/", "ds")
        self.store.clear()
        self.loader = self.store


@unittest.skipIf('SKIP_FUSEKI_TESTS' in os.environ,
                 "Skipping Fuseki/curl tests")    
class FusekiCurl(Fuseki):
    def setUp(self):       
        self.store = TripleStore.connect("FUSEKI", "http://localhost:3030/", "ds", curl=True)
        self.store.clear()
        self.loader = self.store


@unittest.skipIf('SKIP_SESAME_TESTS' in os.environ,
                 "Skipping Sesame tests")    
class Sesame(TripleStoreTestCase, unittest.TestCase):
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
        self.store = TripleStore.connect("SESAME", "http://localhost:8080/openrdf-sesame", "ferenda")
        self.store.clear()
        self.loader = self.store

    def tearDown(self):
        pass        


class SesameCurl(Sesame):
    def setUp(self):
        self.store = TripleStore.connect("SESAME", "http://localhost:8080/openrdf-sesame", "ferenda", curl=True)
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

    def setUp(self):
        self.store = TripleStore.connect("SQLITE", "ferenda.sqlite", "ferenda")
        self.store.clear()
        self.loader = self.store

    def tearDown(self):
        self.store.close()
        del self.store
        os.remove("ferenda.sqlite")


class SQLiteInmemory(Inmemory, SQLite):

    def setUp(self):
        self.loader = TripleStore.connect("SQLITE", "ferenda.sqlite", "ferenda")
        self.loader.clear()

    def getstore(self):
        return TripleStore.connect("SQLITE", "ferenda.sqlite", "ferenda", inmemory=True)


@unittest.skipIf('SKIP_SLEEPYCAT_TESTS' in os.environ,
                 "Skipping Sleepycat tests")    
class Sleepycat(TripleStoreTestCase, unittest.TestCase):

    def setUp(self):
        self.store = TripleStore.connect("SLEEPYCAT", "ferenda.db", "ferenda")
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
        self.loader = TripleStore.connect("SLEEPYCAT", "ferenda.db", "ferenda")
        self.loader.clear()
        self.store = None

    def getstore(self):
        return TripleStore.connect("SLEEPYCAT", "ferenda.db", "ferenda", inmemory=True)

