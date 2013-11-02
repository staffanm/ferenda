# -*- coding: utf-8 -*-
from __future__ import unicode_literals

# the main idea here is, just like testTriplestore, to just make sure
# every line of code is run once, not to instantiate all different
# implementations/configurations and run them all

import json, re, os
from tempfile import mkstemp, mkdtemp
import shutil

import requests.exceptions

from ferenda import util, errors
from ferenda.compat import patch, Mock, unittest
from ferenda.testutil import FerendaTestCase

# SUT
from ferenda import FulltextIndex
from ferenda import fulltextindex 
from integrationFulltextIndex import WhooshBasicIndex, WhooshBasicQuery
from integrationFulltextIndex import BasicIndex, BasicQuery, ESBase

CREATE_CANNED = False

# this is copied directly from testTriplestore and should perhaps go
# into ferenda.testutil
def canned(*responses, **kwargs):
    returned = []
    param = {}
    def fakeresponse(*args, **kwargs):
        if len(returned) > len(responses):
            raise IndexError("Ran out of canned responses after %s calls" %
                             len(returned))
        resp = Mock()
        resp.status_code = responses[len(returned)][0]
        responsefile = responses[len(returned)][1]
        if responsefile:
            responsefile = "test/files/fulltextindex/" + responsefile
            resp.content = util.readfile(responsefile, "rb")
            resp.text = util.readfile(responsefile)
            if responsefile.endswith(".json"):
                data = json.loads(util.readfile(responsefile))
                resp.json = Mock(return_value=data)
        returned.append(True)
        return resp

    def makeresponse(*args, **kwargs):
        clb = getattr(requests, param['method'])
        resp = clb(*args, **kwargs)
        if resp.status_code != responses[len(returned)][0]:
            print("WARNING: Expected status code %s, got %s (respfile %s)" %
                  (responses[len(returned)][0], resp.status_code,
                   responses[len(returned)][1]))

        responsefile = "test/files/fulltextindex/" + responses[len(returned)][1]
        with open(responsefile, 'wb') as fp:
            fp.write(resp.content)
        returned.append(True)
        return resp

    if kwargs.get('create', True):
        param['method'] = kwargs.get('method')
        return makeresponse
    else:
        return fakeresponse

class MockESBase(ESBase):

    @patch('ferenda.fulltextindex.requests')
    def setUp(self, mock_requests):
        can = canned((404, "exists-not.json"),
                     create=CREATE_CANNED, method="get")
        mock_requests.get.side_effect = can

        can = canned((200, "create.json"),
                     create=CREATE_CANNED, method="post")
        mock_requests.put.side_effect = can
        self.location = "http://localhost:9200/ferenda/"
        self.index = FulltextIndex.connect("ELASTICSEARCH", self.location, [])

    @patch('ferenda.fulltextindex.requests')
    def tearDown(self, mock_requests):
        can = canned((200, "delete.json"),
                     create=CREATE_CANNED, method="delete")
        mock_requests.delete.side_effect = can 
        self.index.destroy()
    
class MockESBasicIndex(BasicIndex, MockESBase):

    @patch('ferenda.fulltextindex.requests')
    def test_create(self, mock_requests):
        # since we stub out MockESBase.setUp (which creates the
        # schema/mapping), the only two requests test_create will do
        # is to check if a mapping exists, and it's definition
        can = canned((200, "exists.json"),
                     (200, "schema.json"),
                     create=CREATE_CANNED, method='get')
        mock_requests.get.side_effect = can
        super(MockESBasicIndex, self).test_create()
        
    @patch('ferenda.fulltextindex.requests')
    def test_insert(self, mock_requests):
        can = canned((201, "insert-1.json"),
                     (201, "insert-2.json"),
                     (201, "insert-3.json"),
                     (200, "insert-4.json"), # no new stuff?
                     create=CREATE_CANNED, method="put")
        mock_requests.put.side_effect = can

        can = canned((200, "commit.json"),
                     (200, "commit.json"),
                     create=CREATE_CANNED, method="post")
        mock_requests.post.side_effect = can

        can = canned((200, "count-2.json"),
                     (200, "count-3.json"),
                     create=CREATE_CANNED, method="get")
        mock_requests.get.side_effect = can

        super(MockESBasicIndex, self).test_insert()

class MockESBasicQuery(BasicQuery, MockESBase): 

    @patch('ferenda.fulltextindex.requests')
    def test_basic(self, mock_requests):
        can = canned((201, "insert-1.json"),
                     (201, "insert-2.json"),
                     (201, "insert-3.json"),
                     (200, "insert-4.json"), # no new stuff?
                     (201, "insert-5.json"),
                     create=CREATE_CANNED, method="put")
        mock_requests.put.side_effect = can

        can = canned((200, "commit.json"),
                     (200, "commit.json"),
                     (200, "commit.json"),
                     (200, "commit.json"),
                     (200, "commit.json"), # one commit per update, because of reasons...
                     (200, "query-main.json"),
                     (200, "query-document.json"),
                     (200, "query-section.json"),
                     create=CREATE_CANNED, method="post")
        mock_requests.post.side_effect = can

        can = canned((200, "count-0.json"),
                     (200, "count-4.json"),
                     create=CREATE_CANNED, method="get")
        mock_requests.get.side_effect = can

        super(MockESBasicQuery, self).test_basic()

    @patch('ferenda.fulltextindex.requests')
    def test_fragmented(self, mock_requests):
        can = canned((201, "insert-1.json"),
                     create=CREATE_CANNED, method="put")
        mock_requests.put.side_effect = can

        can = canned((200, "commit.json"),
                     (200, "query-needle.json"),
                     create=CREATE_CANNED, method="post")
        mock_requests.post.side_effect = can

        super(MockESBasicQuery, self).test_fragmented()

class TestIndexedType(unittest.TestCase):

    def test_eq(self):
        id1 = fulltextindex.Identifier(boost=16)
        id2 = fulltextindex.Identifier(boost=16)
        lbl = fulltextindex.Label(boost=16)
        self.assertEqual(id1, id2)
        self.assertNotEqual(id1, lbl)
    
    def test_repr(self):
        self.assertEqual("<Identifier>", repr(fulltextindex.Identifier()))
        self.assertEqual("<Identifier boost=16>",
                         repr(fulltextindex.Identifier(boost=16)))
        self.assertEqual("<Label boost=16 foo=bar>",
                         repr(fulltextindex.Label(boost=16, foo='bar')))

