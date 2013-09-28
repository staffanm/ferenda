# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import sys, os
from ferenda.compat import unittest
if os.getcwd() not in sys.path: sys.path.insert(0,os.getcwd())

from datetime import datetime
from tempfile import mkdtemp
import shutil

import whoosh.index
import whoosh.fields

from ferenda import FulltextIndex, DocumentRepository
from ferenda.fulltextindex import Identifier, Datetime, Text, Label, Keywords, Boolean, URI, Less, More, Between

basic_dataset = [
    {'uri':'http://example.org/doc/1',
     'repo':'base',
     'basefile':'1',
     'title':'First example',
     'identifier':'Doc #1',
     'text':'This is the main text of the document (independent sections excluded)'},
    {'uri':'http://example.org/doc/1#s1',
     'repo':'base',
     'basefile':'1',
     'title':'First sec',
     'identifier':'Doc #1 (section 1)',
     'text':'This is an independent section, with extra section boost'},
    {'uri':'http://example.org/doc/1#s2',
     'repo':'base',
     'basefile':'1',
     'title':'Second sec',
     'identifier':'Doc #1 (section 2)',
     'text':'This is another independent section'},
    {'uri':'http://example.org/doc/1#s1',
     'repo':'base',
     'basefile':'1',
     'title':'First section',
     'identifier':'Doc #1 (section 1)',
     'text':'This is an (updated version of a) independent section, with extra section boost'},
    {'uri':'http://example.org/doc/2',
     'repo':'base',
     'basefile':'2',
     'title':'Second document',
     'identifier':'Doc #2',
     'text':'This is the second document (not the first)'}
    ]

repos = [DocumentRepository()]

class BasicIndex(object):

    def test_create(self):
        # As long as the constructor creates the index, this code will
        # fail:
        
        # # assert that the index doesn't exist
        # self.assertFalse(self.index.exists())
        # # assert that we have no documents
        # self.assertEqual(self.index.doccount(),0)
        
        # # Do it
        # self.index.create()
        self.assertTrue(self.index.exists())

        # assert that the schema, using our types, looks OK
        want = {'uri':Identifier(),
                'repo':Label(),
                'basefile':Label(),
                'title':Text(boost=4),
                'identifier':Label(boost=16),
                'text':Text()}
        got = self.index.schema()
        self.assertEqual(want,got)
                                    

    def test_insert(self):
        self.index.update(**basic_dataset[0])
        self.index.update(**basic_dataset[1])
        self.index.commit()
        self.assertEqual(self.index.doccount(),2)
        self.index.update(**basic_dataset[2])
        self.index.update(**basic_dataset[3]) # updated version of basic_dataset[1]
        self.index.commit() 
        self.assertEqual(self.index.doccount(),3)

        
class BasicQuery(object):

    def load(self, data):
        # print("loading...")
        for doc in data:
            self.index.update(**doc)
            self.index.commit()

    def test_basic(self):
        self.assertEqual(self.index.doccount(),0)
        self.load(basic_dataset)
        self.assertEqual(self.index.doccount(),4)
        res, pager = self.index.query("main")
        self.assertEqual(len(res),1)
        self.assertEqual(res[0]['identifier'], 'Doc #1')
        self.assertEqual(res[0]['uri'], 'http://example.org/doc/1')
        res, pager = self.index.query("document")
        self.assertEqual(len(res),2)
        # Doc #2 contains the term 'document' in title (which is a
        # boosted field), not just in text.
        self.assertEqual(res[0]['identifier'], 'Doc #2') 
        res, pager = self.index.query("section")
        from pprint import pprint
        self.assertEqual(len(res),3)
        # NOTE: ES scores all three results equally (1.0), so it doesn't
        # neccesarily put section 1 in the top
        if isinstance(self, ESBase):
            self.assertEqual(res[0]['identifier'], 'Doc #1 (section 2)') 
        else:
            self.assertEqual(res[0]['identifier'], 'Doc #1 (section 1)') 

class ESBase(unittest.TestCase):
    def setUp(self):
        self.location = "http://localhost:9200/ferenda/"
        self.index = FulltextIndex.connect("ELASTICSEARCH", self.location, repos)

    def tearDown(self):
        self.index.destroy()


@unittest.skipIf('SKIP_ELASTICSEARCH_TESTS' in os.environ,
                 "Skipping Elasticsearch tests")    
class ESBasicIndex(BasicIndex, ESBase): pass


@unittest.skipIf('SKIP_ELASTICSEARCH_TESTS' in os.environ,
                 "Skipping Elasticsearch tests")    
class ESBasicQuery(BasicQuery, ESBase): pass


class WhooshBase(unittest.TestCase):
    def setUp(self):
        self.location = mkdtemp()
        self.index = FulltextIndex.connect("WHOOSH", self.location, repos)

    def tearDown(self):
        self.index.destroy()


class WhooshBasicIndex(BasicIndex, WhooshBase): 
    def test_create(self):
        # First do the basic tests
        super(WhooshBasicIndex,self).test_create()

        # then do more low-level tests
        # 1 assert that some files have been created at the specified location
        self.assertNotEqual(os.listdir(self.location),[])
        # 2 assert that it's really a whoosh index
        self.assertTrue(whoosh.index.exists_in(self.location))

        # 3. assert that the actual schema with whoosh types is, in
        # fact, correct
        got = self.index.index.schema
        want = whoosh.fields.Schema(uri=whoosh.fields.ID(unique=True, stored=True),
                                    repo=whoosh.fields.ID(stored=True),
                                    basefile=whoosh.fields.ID(stored=True),
                                    title=whoosh.fields.TEXT(field_boost=4,stored=True),
                                    identifier=whoosh.fields.ID(field_boost=16,stored=True),
                                    text=whoosh.fields.TEXT(stored=True))
        self.assertEqual(sorted(want.names()), sorted(got.names()))
        for fld in got.names():
            self.assertEqual((fld,want[fld]),(fld,got[fld]))
               
       
class WhooshBasicQuery(BasicQuery, WhooshBase): pass
        

# ----------------------------------------------------------------
# Non-working test classes - TBD!

class DocRepo1(DocumentRepository):
    alias = "repo1"
    def get_indexed_properties(self):
        return {'issued':Datetime(),
                'publisher':Label(),
                'abstract': Text(boost=2),
                'category':Keywords()}

class DocRepo2(DocumentRepository):
    alias = "repo2"
    def get_indexed_properties(self):
        return {'secret':Boolean(),   
                'references': URI(),
                'category': Keywords()}

custom_dataset = [
    {'repo':'repo1',
     'basefile':'1',
     'uri':'http://example.org/repo1/1',
     'title':'Title of first document in first repo',
     'identifier':'R1 D1',
     'issued':datetime(2013,2,14,14,6),
     'publisher': 'Examples & son',
     'category': ['green', 'standards'],
     'text': 'Long text here'},
    {'repo':'repo1',
     'basefile':'2',
     'uri':'http://example.org/repo1/2',
     'title':'Title of second document in first repo',
     'identifier':'R1 D2',
     'issued':datetime(2013,3,4,14,16),
     'publisher': 'Examples & son',
     'category': ['suggestions'],
     'text': 'Even longer text here'},
    {'repo':'repo2',
     'basefile':'1',
     'uri':'http://example.org/repo2/1',
     'title':'Title of first document in second repo',
     'identifier':'R2 D1',
     'secret': False,
     'references':'http://example.org/repo2/2',
     'category':['green', 'yellow']},
    {'repo':'repo2',
     'basefile':'2',
     'uri':'http://example.org/repo2/2',
     'title':'Title of second document in second repo',
     'identifier':'R2 D2',
     'secret': True,
     'references': None,
     'category':['yellow', 'red']}
    ]

#class CustomizedIndex(unittest.TestCase):
class CustomizedIndex(object):

    def test_setup():
        self.location = mkdtemp()
        self.index = FulltextIndex.connect("WHOOSH", self.location, [DocRepo1(), DocRepo2()])
        # introspecting the schema (particularly if it's derived
        # directly from our definitions, not reverse-engineerded from
        # a Whoosh index on-disk) is useful for eg creating dynamic
        # search forms
        self.assertEqual(index.schema(),{'uri':Identifier(),
                                         'repo':Label(),
                                         'basefile':Label(),
                                         'title':Text(boost=4),
                                         'identifier':Label(boost=16),
                                         'text':Text(),
                                         'issued':Datetime(),
                                         'publisher':Label(),
                                         'abstract': Text(boost=2),
                                         'category': Keywords(),
                                         'secret': Boolean(),
                                         'references': URI(),
                                         'category': Keywords()})
        shutil.rmtree(self.location)

    
# class CustomQuery(unittest.TestCase):
class CustomQuery(object):        
    def setUp(self):
        self.location = mkdtemp()
        self.index = FulltextIndex.connect("WHOOSH", self.location, [DocRepo1(), DocRepo2()])
        self.load(custom_dataset)
        
    def tearDown(self):
        shutil.rmtree(self.location)
    
    def load(self, data):
        for doc in data:
            self.index.update(**doc)

    def test_boolean(self):
        res, pager = self.index.query(secret=True)
        self.assertEqual(len(res),1)
        self.assertEqual(res[0]['identifier'], 'R2 D2')
        res = self.index.query(secret=False)
        self.assertEqual(len(res),1)
        self.assertEqual(res[0]['identifier'], 'R2 D1')
    
    def test_keywords(self):
        res, pager = self.index.query(category='green')
        self.assertEqual(len(res),2)
        identifiers = set([x['identifier'] for x in res])
        self.assertEqual(identifiers, set(['R1 D1','R2 D1']))
        
    def test_repo_limited_freetext(self):
        res, pager = self.index.query('first', repo='repo1')
        self.assertEqual(len(res),2)
        self.assertEqual(res[0]['identifier'], 'R1 D1') # contains the term 'first' twice
        self.assertEqual(res[1]['identifier'], 'R1 D2') #          -""-             once

    def test_repo_dateinterval(self):

        res, pager = self.index.query(issued=Less(datetime(2013,3,1)))
        self.assertEqual(len(res),1)
        self.assertEqual(res[0]['identifier'], 'R1 D1') 

        res, pager = self.index.query(issued=More(datetime(2013,3,1)))
        self.assertEqual(res[0]['identifier'], 'R1 D2') 

        res, pager = self.index.query(issued=Between(datetime(2013,2,1),datetime(2013,4,1)))
        self.assertEqual(len(res),2)
        identifiers = set([x['identifier'] for x in res])
        self.assertEqual(identifiers, set(['R1 D1','R1 D2']))
