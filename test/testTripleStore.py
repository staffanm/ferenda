# -*- coding: utf-8 -*-
from __future__ import unicode_literals

# the main idea is to just make sure every line of code is run once,
# not to instantiate all eight different
# implementations/configurations and run them all. This will make the
# test code mimick the implementation to some extent, but as the plan
# is to mock all http requests/RDFLib calls (neither of which is
# idempotent), that is sort of unavoidable.

from ferenda.compat import patch, Mock, unittest
from ferenda import util
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
        returned.append(True)
        return resp
    return makeresponse
        
class UnitTripleStore(unittest.TestCase, FerendaTestCase):

    @patch('ferenda.triplestore.util.runcmd')
    def test_curl(self, runcmd_mock):
        # needs to test add_serialized, add_serialized_file, get_serialized
        # and get_serialized_file. We'll patch util.runcmd and make sure that
        # the command line is correct. We should also have util.runcmd return
        # a non-zero return code once.
        # our util.runcmd replacement should, for the get_serialized file,
        # create a suitable temp file 
        store = TripleStore.connect("FUSEKI", "", "", curl=True)

    @patch('requests.get', side_effect=canned(("200", "defaultgraph.nt"),
                                             ("200", "namedgraph.nt"),
                                             ("200", "namedgraph.nt"),
                                             ("200", "defaultgraph.ttl"),
                                             ("200", "namedgraph.ttl")))
    def test_fuseki_get_serialized_file(self, mock_get):
        # test 1: imagine that server has data in the default graph
        # and in one named graph
        rf = util.readfile
        store = TripleStore.connect("FUSEKI", "", "")
        # test 1.1: Get everything, assert that the result is a combo
        store.get_serialized_file("out.nt") # no ctx, will result in 2 gets
        self.assertEqual(mock_get.call_count, 2)
        self.assertEqual(rf("test/files/triplestore/combinedgraph.nt"),
                         rf("out.nt"))
        # test 1.2: Get only namedgraph, assert that only that is returned
        store.get_serialized_file("out.nt", context="namedgraph") # 1 get
        self.assertEqual(rf("test/files/triplestore/namedgraph.nt"),
                         rf("out.nt"))
        self.assertEqual(mock_get.call_count, 3)
        # test 1.3: Get everything in a different format
        store.get_serialized_file("out.ttl", format="turtle") # results in 2 gets
        self.assertEqualGraphs("test/files/triplestore/combinedgraph.ttl",
                              "out.ttl")
        self.assertEqual(mock_get.call_count, 5)
                
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
      

    @patch('requests.get', side_effect=canned(("200", "triplecount-21.xml"),
                                             ("200", "triplecount-18.xml"),
                                             ("200", "triplecount-18.xml")))
    def test_fuseki_triple_count(self, mock_get):
        store = TripleStore.connect("FUSEKI", "", "")
        self.assertEqual(39, store.triple_count())
        self.assertEqual(mock_get.call_count, 2)
        self.assertEqual(18, store.triple_count(context="namedgraph"))
        self.assertEqual(mock_get.call_count, 3)

    @patch('requests.get', side_effect=canned(("200", "ping.txt"),))
    def test_sesame_ping(self, mock_get):
        store = TripleStore.connect("SESAME", "", "")
        self.assertEqual("5", store.ping())

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
        
        
