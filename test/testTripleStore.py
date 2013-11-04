# -*- coding: utf-8 -*-
from __future__ import unicode_literals

# the main idea is to just make sure every line of code is run once,
# not to instantiate all eight different
# implementations/configurations and run them all. This will make the
# test code mimick the implementation to some extent, but as the plan
# is to mock all http requests/RDFLib calls (neither of which is
# idempotent), that is sort of unavoidable.

import json, re, os, sqlite3
from tempfile import mkstemp, mkdtemp
import shutil

import pyparsing
from rdflib import Graph, URIRef, RDFS, Literal
import requests.exceptions

from ferenda.compat import patch, Mock, unittest
from ferenda import util, errors
from ferenda.testutil import FerendaTestCase

# SUT
from ferenda import TripleStore

# we could have a switch in canned() that, if set, actually calls
# the request.get or post methods and writes the result to the
# given files.
def canned(*responses):
    returned = []
    def makeresponse(*args, **kwargs):
        if len(returned) > len(responses):
            raise IndexError("Ran out of canned responses after %s calls" % len(returned))
        resp = Mock()
        resp.status_code = responses[len(returned)][0]
        responsefile = responses[len(returned)][1]
        if responsefile:
            responsefile = "test/files/triplestore/" + responsefile
            resp.content = util.readfile(responsefile, "rb")
            resp.text = util.readfile(responsefile)
            if responsefile.endswith(".json"):
                data = json.loads(util.readfile(responsefile))
                resp.json = Mock(return_value=data)
        returned.append(True)
        return resp
    return makeresponse
        
class Main(unittest.TestCase, FerendaTestCase):

    @patch('ferenda.triplestore.util.runcmd')
    def test_curl(self, runcmd_mock):
        # needs to test add_serialized, add_serialized_file, get_serialized
        # and get_serialized_file. We'll patch util.runcmd and make sure that
        # the command line is correct. We should also have util.runcmd return
        # a non-zero return code once.
        # our util.runcmd replacement should, for the get_serialized file,
        # create a suitable temp file

        store = TripleStore.connect("FUSEKI", "", "", curl=True)
        # 1. add_serialized
        runcmd_mock.return_value = (0, "", "")
        store.add_serialized("tripledata", "nt")
        cmdline = runcmd_mock.call_args[0][0] # first ordered argument
        # replace the temporary file name
        cmdline = re.sub('"@[^"]+"', '"@tempfile.nt"', cmdline)
        self.assertEqual('curl -X POST --data-binary "@tempfile.nt" --header "Content-Type:text/plain;charset=UTF-8" "/?default"', cmdline)
        runcmd_mock.mock_reset()

        # 2. add_serialized_file
        runcmd_mock.return_value = (0, "", "")
        store.add_serialized_file("tempfile.nt", "nt")
        cmdline = runcmd_mock.call_args[0][0] # first ordered argument
        self.assertEqual('curl -X POST --data-binary "@tempfile.nt" --header "Content-Type:text/plain;charset=UTF-8" "/?default"', cmdline)
        runcmd_mock.mock_reset()

        # 3. get_serialized
        def create_tempfile(*args, **kwargs):
            filename = re.search('-o "([^"]+)"', args[0]).group(1)
            with open(filename, "wb") as fp:
                fp.write("tripledata\n".encode())
            return (0, "", "")
        runcmd_mock.side_effect = create_tempfile
        res = store.get_serialized("nt")
        self.assertEqual(b"tripledata\ntripledata\n", res)
        cmdline = runcmd_mock.call_args[0][0] # first ordered argument
        # replace the temporary file name
        cmdline = re.sub('-o "[^"]+"', '-o "tempfile.nt"', cmdline)
        # FIXME is this really right?
        self.assertEqual('curl -o "tempfile.nt" --header "Accept:text/plain" "/?graph=urn:x-arq:UnionGraph"', cmdline)
        runcmd_mock.side_effect = None
        runcmd_mock.mock_reset()

        # 4. get_serialized_file
        store.get_serialized_file("triples.nt", "nt")
        cmdline = runcmd_mock.call_args[0][0] # first ordered argument
        self.assertEqual('curl -o "triples.nt" --header "Accept:text/plain" "/?default"', cmdline)
        runcmd_mock.mock_reset()

        # 5. handle errors
        with self.assertRaises(errors.TriplestoreError):
            runcmd_mock.return_value = (1, "", "Internal error")
            store.get_serialized_file("triples.nt", "nt")

    def test_fuseki_initialize_triplestore(self):
        store = TripleStore.connect("FUSEKI", "", "")
        store.initialize_repository()

        store = TripleStore.connect("FUSEKI", "http://localhost/", "mydataset")
        store.initialize_repository()
        
    @patch('requests.get', side_effect=canned(("200", "defaultgraph.nt"),
                                             ("200", "namedgraph.nt"),
                                             ("200", "namedgraph.nt"),
                                             ("200", "defaultgraph.ttl"),
                                             ("200", "namedgraph.ttl")))
    def test_fuseki_get_serialized_file(self, mock_get):
        # Test 1: imagine that server has data in the default graph
        # and in one named graph
        rf = util.readfile
        tmp = mkdtemp()
        try:
            store = TripleStore.connect("FUSEKI", "", "")
            # test 1.1: Get everything, assert that the result is a combo
            store.get_serialized_file(tmp+"/out.nt") # no ctx, will result in 2 gets
            self.assertEqual(mock_get.call_count, 2)
            self.assertEqual(rf("test/files/triplestore/combinedgraph.nt"),
                             rf(tmp+"/out.nt"))
            # test 1.2: Get only namedgraph, assert that only that is returned
            store.get_serialized_file(tmp+"/out.nt", context="namedgraph") # 1 get
            self.assertEqual(rf("test/files/triplestore/namedgraph.nt"),
                             rf(tmp+"/out.nt"))
            self.assertEqual(mock_get.call_count, 3)
            # test 1.3: Get everything in a different format
            store.get_serialized_file(tmp+"/out.ttl", format="turtle") # results in 2 gets
            self.assertEqualGraphs("test/files/triplestore/combinedgraph.ttl",
                                  tmp+"/out.ttl")
            self.assertEqual(mock_get.call_count, 5)
        finally:
            shutil.rmtree(tmp)
                
    @patch('requests.get', side_effect=canned(("200", "namedgraph.nt"),))
    def test_fuseki_get_serialized(self, mock_get):
        store = TripleStore.connect("FUSEKI", "", "", curl=False)
        # test 1: a namedgraph (cases with no context are already run by
        # test_fuseki_get_serialized_file)
        want = util.readfile("test/files/triplestore/namedgraph.nt", "rb")
        got = store.get_serialized(context="namedgraph") # results in single get
        self.assertEqual(want, got)

    @patch('requests.delete')
    def test_fuseki_clear(self, mock_delete):
        store = TripleStore.connect("FUSEKI", "", "")
        store.clear()
        self.assertEqual(mock_delete.call_count, 2)            

        with self.assertRaises(errors.TriplestoreError):
            mock_delete.side_effect = requests.exceptions.ConnectionError("Server error")
            got = store.clear()

        with self.assertRaises(errors.TriplestoreError):
            mock_delete.side_effect = requests.exceptions.HTTPError("Server error")
            got = store.clear()

        mock_delete.side_effect = requests.exceptions.HTTPError("No such graph")
        got = store.clear("namedgraph")


    @patch('requests.get', side_effect=canned(("200", "triplecount-21.xml"),
                                             ("200", "triplecount-18.xml"),
                                             ("200", "triplecount-18.xml")))
    def test_fuseki_triple_count(self, mock_get):
        store = TripleStore.connect("FUSEKI", "", "")
        self.assertEqual(39, store.triple_count())
        self.assertEqual(mock_get.call_count, 2)
        self.assertEqual(18, store.triple_count(context="namedgraph"))
        self.assertEqual(mock_get.call_count, 3)


    @patch('requests.post', side_effect=canned((204, None),
                                               (204, None)))
    def test_fuseki_add_serialized_file(self, mock_post):
        store = TripleStore.connect("FUSEKI", "", "")
        store.add_serialized_file("test/files/triplestore/defaultgraph.ttl",
                                  format="turtle")
        self.assertEqual(mock_post.call_count, 1)

    @patch('requests.get', side_effect=canned(("200", "ping.txt"),))
    def test_sesame_ping(self, mock_get):
        store = TripleStore.connect("SESAME", "", "")
        self.assertEqual("5", store.ping())

    def test_sesame_initialize_triplestore(self):
        store = TripleStore.connect("SESAME", "", "")
        store.initialize_repository()

    @patch('requests.get', side_effect=canned(("200", "combinedgraph.nt"),
                                              ("200", "namedgraph.nt")))
    def test_sesame_get_serialized(self, mock_get):
        store = TripleStore.connect("SESAME", "", "")
        want = util.readfile("test/files/triplestore/combinedgraph.nt", "rb")
        got = store.get_serialized() 
        self.assertEqual(want, got)
        self.assertEqual(mock_get.call_count, 1)

        want = util.readfile("test/files/triplestore/namedgraph.nt", "rb")
        got = store.get_serialized(context="namedgraph") # results in single get
        self.assertEqual(want, got)
        self.assertEqual(mock_get.call_count, 2)

    @patch('requests.post', side_effect=canned((204, None),
                                               (204, None)))
    def test_sesame_add_serialized(self, mock_post):
        store = TripleStore.connect("SESAME", "", "")
        rf = util.readfile
        store.add_serialized(rf("test/files/triplestore/defaultgraph.ttl"),
                             format="turtle")
        self.assertEqual(mock_post.call_count, 1)

        store.add_serialized(rf("test/files/triplestore/namedgraph.nt"),
                             format="nt",
                             context="namedgraph")
        self.assertEqual(mock_post.call_count, 2)

   
    @patch('requests.get', side_effect=canned((200, "select-results.xml"),
                                              (200, "select-results.json"),
                                              (200, "select-results.xml")))
    def test_sesame_select(self, mock_get):
        store = TripleStore.connect("SESAME", "", "")
        rf = util.readfile
        want = rf("test/files/triplestore/select-results.xml")
        got = store.select("the-query")
        self.assertEqual(want, got)
        self.assertEqual(mock_get.call_count, 1)

        want = json.loads(rf("test/files/triplestore/select-results.json"))
        got = store.select("the-query", format="json")
        self.assertEqual(want, got)
        self.assertEqual(mock_get.call_count, 2)

        want = json.loads(rf("test/files/triplestore/select-results-python.json"))
        got = store.select("the-query", format="python")
        self.assertEqual(want, got)
        self.assertEqual(mock_get.call_count, 3)

        with self.assertRaises(errors.TriplestoreError):
            mock_get.side_effect = requests.exceptions.HTTPError("Server error")
            got = store.select("the-query", format="python")
            

    
    @patch('requests.get', side_effect=canned((200, "construct-results.xml")))
    def test_sesame_construct(self, mock_get):
        store = TripleStore.connect("SESAME", "", "")
        rf = util.readfile
        want = Graph()
        want.parse(data=rf("test/files/triplestore/construct-results.ttl"),
                   format="turtle")
        got = store.construct("the-query")
        self.assertEqualGraphs(want, got)
        self.assertEqual(mock_get.call_count, 1)

        with self.assertRaises(errors.TriplestoreError):
            mock_get.side_effect = requests.exceptions.HTTPError("Server error")
            got = store.construct("the-query")
        
        
    @patch('requests.get', side_effect=canned(("200", "size-39.txt"),
                                             ("200", "size-18.txt")))
    def test_sesame_triple_count(self, mock_get):
        store = TripleStore.connect("SESAME", "", "")
        self.assertEqual(39, store.triple_count())
        self.assertEqual(mock_get.call_count, 1)
        self.assertEqual(18, store.triple_count(context="namedgraph"))
        self.assertEqual(mock_get.call_count, 2)
        

    @patch('ferenda.triplestore.ConjunctiveGraph')
    def test_sqlite_init(self, mock_graph):
        # create a new db that doesnt exist
        mock_graph.open.return_value = 42
        store = TripleStore.connect("SQLITE", "", "")
        self.assertTrue(mock_graph.return_value.open.called)
        self.assertTrue(mock_graph.return_value.open.call_args[1]['create'])

        # reopen an existing db
        fd, tmpname = mkstemp()
        fp = os.fdopen(fd)
        fp.close()
        store = TripleStore.connect("SQLITE", tmpname, "")
        os.unlink(tmpname)
        self.assertFalse(mock_graph.return_value.open.call_args[1]['create'])

        # make an inmemory db
        store = TripleStore.connect("SQLITE", "", "", inmemory=True)
        self.assertTrue(mock_graph.return_value.quads.called)
        self.assertTrue(mock_graph.return_value.addN.called)

    @patch('ferenda.triplestore.ConjunctiveGraph')
    def test_sqlite_add_serialized(self, mock_graph):
        store = TripleStore.connect("SQLITE", "", "")
        store.add_serialized("tripledata", "nt")
        self.assertTrue(mock_graph.return_value.parse.called)
        self.assertTrue(mock_graph.return_value.commit.called)
        mock_graph.reset_mock()
        
        store.add_serialized("tripledata", "nt", "namedgraph")
        self.assertTrue(mock_graph.return_value.get_context.called)
        self.assertTrue(mock_graph.return_value.get_context.return_value.parse.called)

        store = TripleStore.connect("SQLITE", "", "", inmemory=True)
        with self.assertRaises(errors.TriplestoreError):
            store.add_serialized("tripledata", "nt")

        
    @patch('ferenda.triplestore.ConjunctiveGraph')
    def test_sqlite_add_serialized_file(self, mock_graph):
        store = TripleStore.connect("SQLITE", "", "")
        fd, tmpname = mkstemp()
        fp = os.fdopen(fd, "w")
        fp.write("tripledata")
        fp.close()
        store.add_serialized_file(tmpname, "nt")
        os.unlink(tmpname)

    @patch('ferenda.triplestore.ConjunctiveGraph')
    def test_sqlite_get_serialized(self, mock_graph):
        store = TripleStore.connect("SQLITE", "", "")
        mock_graph.return_value.serialize.return_value = "tripledata"
        self.assertEqual(store.get_serialized(), "tripledata")

    @patch('ferenda.triplestore.ConjunctiveGraph')
    def test_sqlite_triple_count(self, mock_graph):
        store = TripleStore.connect("SQLITE", "", "")
        self.assertEqual(0, store.triple_count())

    @patch('ferenda.triplestore.ConjunctiveGraph')
    def test_sqlite_select(self, mock_graph):
        store = TripleStore.connect("SQLITE", "", "")
        sq = """SELECT ?p FROM <http://example.org/ctx> WHERE {?s ?p ?o . }"""
        res = mock_graph.return_value.get_context.return_value.query.return_value
        want = [{"s": "http://example.org/doc1",
                 "p": "http://www.w3.org/2000/01/rdf-schema#comment",
                 "o": "Hello"}]
        res.bindings = want
        self.assertEqual(want, store.select(sq, format="python"))
        mock_graph.reset_mock()
        store.select(sq, "sparql")
        mock_graph.return_value.get_context.return_value.query.return_value.serialize.assert_called_with(format="xml")
        
        store.select(sq, "json")
        mock_graph.return_value.get_context.return_value.query.return_value.serialize.assert_called_with(format="json")
        
        mock_graph.return_value.get_context.return_value.query.side_effect = pyparsing.ParseException("Syntax error")
        with self.assertRaises(errors.SparqlError):
            store.select(sq)
        
        
    @patch('ferenda.triplestore.ConjunctiveGraph')
    def test_sqlite_construct(self, mock_graph):
        store = TripleStore.connect("SQLITE", "", "")
        sq = """CONSTRUCT ?s ?p ?o WHERE {?o ?p ?s . }"""
        g = Graph()
        g.add((URIRef("http://example.org/doc1"), RDFS.comment, Literal("Hey")))
        g.add((URIRef("http://example.org/doc2"), RDFS.comment, Literal("Ho")))
        res = Mock
        res.graph = g
        mock_graph.return_value.query.return_value = res
        self.assertEqual(g, store.construct(sq))
    
        mock_graph.return_value.query.side_effect = pyparsing.ParseException("Syntax error")
        with self.assertRaises(errors.SparqlError):
            store.construct(sq)

    
    @patch('ferenda.triplestore.ConjunctiveGraph')
    def test_sqlite_clear(self, mock_graph):
        store = TripleStore.connect("SQLITE", "", "")
        g = Graph()
        g.add((URIRef("http://example.org/doc1"), RDFS.comment, Literal("Hey")))
        g.add((URIRef("http://example.org/doc2"), RDFS.comment, Literal("Ho")))
        mock_graph.return_value.get_context.return_value = g
        store.clear("namedgraph")
        self.assertEqual(2, mock_graph.return_value.remove.call_count)
        self.assertEqual(1, mock_graph.return_value.commit.call_count)
        
    @patch('ferenda.triplestore.ConjunctiveGraph')
    def test_sqlite_initialize_triplestore(self, mock_graph):
        store = TripleStore.connect("SQLITE", "", "")
        store.initialize_repository()
        self.assertTrue(mock_graph.return_value.open.call_args[1]['create'])
        

    @patch('ferenda.triplestore.ConjunctiveGraph')
    def test_sqlite_remove_repository(self, mock_graph):
        store = TripleStore.connect("SQLITE", "", "")
        store.remove_repository()
        self.assertTrue(mock_graph.return_value.destroy.called)

    @patch('ferenda.triplestore.ConjunctiveGraph')
    def test_sqlite_close(self, mock_graph):
        # make sure this wierd but harmless sqlite3 exception is
        # caught
        mock_graph.return_value.close.side_effect = sqlite3.ProgrammingError("You made a wrong")
        store = TripleStore.connect("SQLITE", "", "")
        store.close()
        


    @patch('ferenda.triplestore.ConjunctiveGraph')
    def test_sleepycat_init(self, mock_graph):
        store = TripleStore.connect("SLEEPYCAT", "", "")
        
    @patch('ferenda.triplestore.ConjunctiveGraph')
    def test_sleepycat_triple_count(self, mock_graph):
        store = TripleStore.connect("SLEEPYCAT", "", "")
        self.assertEqual(0, store.triple_count())

    def test_invalid_store(self):
        with self.assertRaises(ValueError):
            TripleStore.connect("INVALID", "", "")
            
