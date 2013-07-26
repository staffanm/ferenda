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

from rdflib import Graph
from rdflib.util import guess_format
from rdflib.compare import graph_diff, isomorphic
from ferenda import util

from ferenda.triplestore import TripleStore

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
        self.store.context = "http://example.org/ctx1"
        self.store.add_serialized(self.dataset2,format="nt")
        self.assertEqual(3,self.store.triple_count())
        self.store.context = None
        self.assertEqual(10,self.store.triple_count())

    def test_add_contexts(self):
        self.store.context = "http://example.org/movies"
        self.store.add_serialized(self.movies,format="turtle")
        self.assertEqual(21,self.store.triple_count())
        self.store.context = "http://example.org/actors"
        self.store.add_serialized(self.actors,format="turtle")
        # print(self.store.get_serialized())
        self.assertEqual(18,self.store.triple_count())
        self.store.context = None
        self.assertEqual(39,self.store.triple_count())
        self.store.context = "http://example.org/movies"
        self.store.clear()
        # print(self.store.get_serialized())
        self.assertEqual(0,self.store.triple_count())
        self.store.context = None
        self.assertEqual(18,self.store.triple_count())
        self.store.context = "http://example.org/actors"
        self.store.clear()
        self.store.context = None
        self.assertEqual(0,self.store.triple_count())
        
    def test_add_serialized_file(self):
        self.assertEqual(0,self.store.triple_count())
        tmp1 = tempfile.mktemp()
        with open(tmp1,"w") as fp:
            fp.write(self.dataset)
        tmp2 = tempfile.mktemp()
        with open(tmp2,"w") as fp:
            fp.write(self.dataset2)

        # default graph
        self.store.add_serialized_file(tmp1,format="nt")
        self.assertEqual(7,self.store.triple_count())
        # named graph
        self.store.context = "http://example.org/ctx1"
        self.store.add_serialized_file(tmp2,format="nt")
        self.assertEqual(3,self.store.triple_count())
        self.store.context = None
        self.assertEqual(10,self.store.triple_count())

        os.unlink(tmp1)
        os.unlink(tmp2)

    def test_roundtrip(self):
        data = """<http://example.org/1> <http://purl.org/dc/terms/title> "language literal"@sv ."""
        self.store.add_serialized(data, format="nt")
        res = self.store.get_serialized(format="nt")
        self.assertEqual(res, data)

    def test_clear(self):
        data = """<http://example.org/1> <http://purl.org/dc/terms/title> "language literal"@sv .\n\n"""
        self.store.add_serialized(data, format="nt")
        res = self.store.clear()
        self.assertEqual(0,self.store.triple_count())
        
    def test_get_serialized(self):
        self.store.add_serialized(self.dataset,format="nt")
        res = self.store.get_serialized(format="nt")
        self.assertEqualGraphs(Graph().parse(data=self.dataset, format="nt"),
                               Graph().parse(data=res, format="nt"))

    def test_get_serialized_file(self):
        want = tempfile.mktemp(suffix=".nt")
        util.writefile(want, self.dataset)
        got = tempfile.mktemp(suffix=".nt")
        self.store.add_serialized(self.dataset,format="nt")
        self.store.get_serialized_file(got, format="nt")
        self.assertEqualGraphs(want,got)
        
    def test_select(self):
        self.store.context = "http://example.org/movies"
        self.store.add_serialized(self.movies,format="turtle")
        self.store.context = "http://example.org/actors"
        self.store.add_serialized(self.actors,format="turtle")
        sq = """PREFIX foaf: <http://xmlns.com/foaf/0.1/>
                PREFIX owl: <http://www.w3.org/2002/07/owl#>

                SELECT ?name
                WHERE  { GRAPH <http://example.org/actors> { ?uri foaf:name ?name .
                        ?uri owl:sameAs <http://live.dbpedia.org/resource/Kevin_Bacon> } }"""

        self.store.context = None # note the graph identifier in the Sparql query
        p = self.store.select(sq,"python")
        self.assertEqual(p,[{'name':'Kevin Bacon'}])
        if self.store.storetype == self.store.SLEEPYCAT:
            self.store.graph.close()
        
    def test_construct(self):
        self.store.add_serialized("""
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

        sq = """
PREFIX ab: <http://learningsparql.com/ns/addressbook#>
PREFIX d: <http://learningsparql.com/ns/data#>

CONSTRUCT
{ ?person ?p ?o . }
WHERE {
?person ab:firstName "Craig" ; ab:lastName "Ellis" ;
?p ?o . }
        """
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
        if self.store.storetype == self.store.SLEEPYCAT:
            self.store.graph.close()

@unittest.skipIf('SKIP_FUSEKI_TESTS' in os.environ,
                 "Skipping Fuseki tests")    
class Fuseki(TripleStoreTestCase,unittest.TestCase):
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
        # to filter out spurious warnings from requests/urllib3 under
        # py3. Does not work when running the entire test suite, for
        # some reason, but works fine when only testing with this module.
        # logging.captureWarnings(True)
        
        self.store = TripleStore("http://localhost:3030/", "ds", storetype=TripleStore.FUSEKI)
        self.store.clear()

    def tearDown(self):
        # logging.captureWarnings(False)
        pass


@unittest.skipIf('SKIP_SESAME_TESTS' in os.environ,
                 "Skipping Sesame tests")    
class Sesame(TripleStoreTestCase,unittest.TestCase):
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
        # to filter out spurious warnings from requests/urllib3 under py3
        # logging.captureWarnings(True) 
        self.store = TripleStore("http://localhost:8080/openrdf-sesame", "ferenda", storetype=TripleStore.SESAME)
        self.store.clear()

    def tearDown(self):
        pass
        # logging.captureWarnings(False)
        

class SQLite(TripleStoreTestCase,unittest.TestCase):

    def setUp(self):
        self.store = TripleStore("ferenda.sqlite", "ferenda", storetype=TripleStore.SQLITE)
        self.store.clear()

    def tearDown(self):
        self.store.close()
        del self.store
        os.remove("ferenda.sqlite")

@unittest.skipIf('SKIP_SLEEPYCAT_TESTS' in os.environ,
                 "Skipping Fuseki tests")    
class Sleepycat(TripleStoreTestCase,unittest.TestCase):

    def setUp(self):
        self.store = TripleStore("ferenda.db", "ferenda", storetype=TripleStore.SLEEPYCAT)
        self.store.clear()

    def tearDown(self):
        del self.store
        if os.path.exists("ferenda.db"):
            shutil.rmtree("ferenda.db")


