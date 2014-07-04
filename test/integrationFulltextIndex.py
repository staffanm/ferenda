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
from rdflib.namespace import RDF, DC, DCTERMS

from ferenda import FulltextIndex, DocumentRepository, Facet
from ferenda.fulltextindex import Identifier, Datetime, Text, Label, Keyword, Boolean, URI, Resource, Less, More, Between

#----------------------------------------------------------------
#
# Initial testdata and testrepo implementations used by our test cases
#

basic_dataset = [
    {'uri':'http://example.org/doc/1',
     'repo':'base',
     'basefile':'1',
     'dcterms_title':'First example',
     'dcterms_identifier':'Doc #1',
     'text':'This is the main text of the document (independent sections excluded)'},
    {'uri':'http://example.org/doc/1#s1',
     'repo':'base',
     'basefile':'1',
     'dcterms_title':'First sec',
     'dcterms_identifier':'Doc #1 (section 1)',
     'text':'This is an independent section, with extra section boost'},
    {'uri':'http://example.org/doc/1#s2',
     'repo':'base',
     'basefile':'1',
     'dcterms_title':'Second sec',
     'dcterms_identifier':'Doc #1 (section 2)',
     'text':'This is another independent section'},
    {'uri':'http://example.org/doc/1#s1',
     'repo':'base',
     'basefile':'1',
     'dcterms_title':'First section',
     'dcterms_identifier':'Doc #1 (section 1)',
     'text':'This is an (updated version of a) independent section, with extra section boost'},
    {'uri':'http://example.org/doc/2',
     'repo':'base',
     'basefile':'2',
     'dcterms_title':'Second document',
     'dcterms_identifier':'Doc #2',
     'text':'This is the second document (not the first)'}
    ]

# FIXME: It'd be neat if this dataset was identical to what could be
# extracted from files/testrepos/repo2
custom_dataset = [
    {'repo':'repo1',
     'basefile':'1',
     'uri':'http://example.org/repo1/1',
     'dcterms_title':'Title of first document in first repo',
     'dcterms_identifier':'R1 D1',
     'dcterms_issued':datetime(2013,2,14,14,6), # important to use real datetime object, not string representation
     'dcterms_publisher': [{'iri': 'http://example.org/vocab/publ1',
                   'label': 'Publishing & sons'}],
     'dc_subject': ['green', 'standards'],
     'text': 'Long text here'},
    {'repo':'repo1',
     'basefile':'2',
     'uri':'http://example.org/repo1/2',
     'dcterms_title':'Title of second document in first repo',
     'dcterms_identifier':'R1 D2',
     'dcterms_issued':datetime(2013,3,4,14,16),
     'dcterms_publisher': [{'iri': 'http://example.org/vocab/publ2',
                            'label': 'Bookprinters and associates'},
                           {'iri': 'http://example.org/vocab/publ3',
                            'label': 'Printers intl.'}],
     'dc_subject': ['suggestions'],
     'text': 'Even longer text here'},
    {'repo':'repo2',
     'basefile':'1',
     'uri':'http://example.org/repo2/1',
     'dcterms_title':'Title of first document in second repo',
     'dcterms_identifier':'R2 D1',
     'ex_secret': False,
     'dcterms_references':'http://example.org/repo2/2',
     'dc_subject':['green', 'yellow'],
     'text': 'All documents must have texts'},
    {'repo':'repo2',
     'basefile':'2',
     'uri':'http://example.org/repo2/2',
     'dcterms_title':'Title of second document in second repo',
     'dcterms_identifier':'R2 D2',
     'ex_secret': True,
     'dcterms_references':'http://example.org/repo2/2',
     'dc_subject':['yellow', 'red'],
     'text': 'Even this one'}
    ]

class DocRepo1(DocumentRepository):
    alias = "repo1"
    namespaces = ['rdf', 'rdfs', 'xsd', 'xsi', 'dc', 'dcterms']
    def facets(self):
        return [Facet(RDF.type),           
                Facet(DCTERMS.title),      
                Facet(DCTERMS.publisher),
                Facet(DCTERMS.identifier),
                Facet(DCTERMS.issued),
                Facet(DCTERMS.publisher),
                Facet(DCTERMS.abstract),
                Facet(DC.subject)]

class DocRepo2(DocumentRepository):
    alias = "repo2"
    namespaces = ['rdf', 'rdfs', 'xsd', 'xsi', 'dc', 'dcterms', ('ex', 'http://example.org/vocab/')]
    def facets(self):
        EX = self.ns['ex']
        return [Facet(RDF.type),           
                Facet(DCTERMS.title),      
                Facet(DCTERMS.publisher, multiple_values=True),
                Facet(DCTERMS.identifier),
                Facet(DCTERMS.issued),
                Facet(EX.secret, indexingtype=Boolean()),
                Facet(DCTERMS.references),
                Facet(DC.subject)]

#----------------------------------------------------------------
#
# The actual test -- note that these do not derive from
# unittest.TestCase. They are used as one of two superclasses to yield
# working TestCase classes, but this way allows us to define a test of
# the API, and then having it run once for each backend configuration.

class BasicIndex(object):

    repos = [DocumentRepository()]

    def test_create(self):
        # setUp calls FulltextIndex.connect, creating the index
        self.assertTrue(self.index.exists())
        # assert that the schema, using our types, looks OK
        want = {
            'basefile':Label(),
            'dcterms_identifier':Label(boost=16),
            'dcterms_issued': Datetime(),
            'dcterms_publisher':Resource(),
            'dcterms_title':Text(boost=4),
            'rdf_type': URI(),
            'repo':Label(),
            'text':Text(),
            'uri':Identifier()
        }
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

    repos = [DocumentRepository()]

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
        self.assertEqual(res[0]['dcterms_identifier'], 'Doc #1')
        self.assertEqual(res[0]['uri'], 'http://example.org/doc/1')
        res, pager = self.index.query("document")
        self.assertEqual(len(res),2)
        # Doc #2 contains the term 'document' in title (which is a
        # boosted field), not just in text.
        self.assertEqual(res[0]['dcterms_identifier'], 'Doc #2')
        res, pager = self.index.query("section")
        # can't get these results when using MockESBasicQuery with
        # CREATE_CANNED=True for some reason...
        if type(self) == ESBasicQuery:
            self.assertEqual(len(res),3)
            # NOTE: ES scores all three results equally (1.0), so it doesn't
            # neccesarily put section 1 in the top
            if isinstance(self, ESBase):
                self.assertEqual(res[0]['dcterms_identifier'], 'Doc #1 (section 2)') 
            else:
                self.assertEqual(res[0]['dcterms_identifier'], 'Doc #1 (section 1)')


    def test_fragmented(self):
        self.load([
            {'uri':'http://example.org/doc/3',
             'repo':'base',
             'basefile':'3',
             'dcterms_title':'Other example',
             'dcterms_identifier':'Doc #3',
             'text':"""Haystack needle haystack haystack haystack haystack
                       haystack haystack haystack haystack haystack haystack
                       haystack haystack needle haystack haystack."""}
            ])
        res, pager = self.index.query("needle")
        # this should return 1 hit (only 1 document)
        self.assertEqual(1, len(res))
        # that has a fragment connector (' ... ') in the middle
        self.assertIn(' ... ', "".join(str(x) for x in res[0]['text']))
        
    
class CustomIndex(object):

    repos = [DocRepo1(), DocRepo2()]
    
    def test_setup(self):
        # introspecting the schema - useful for eg creating dynamic
        # search forms
        self.assertEqual({
            'basefile':Label(),
            'dc_subject': Keyword(),
            'dcterms_abstract': Text(boost=2),
            'dcterms_identifier':Label(boost=16),
            'dcterms_issued':Datetime(),
            'dcterms_publisher':Resource(),
            'dcterms_references': URI(),
            'dcterms_title':Text(boost=4),
            'ex_secret': Boolean(),
            'rdf_type': URI(),
            'repo':Label(),
            'text':Text(),
            'uri':Identifier(),
        }, self.index.schema())

    def test_insert(self):
        self.index.update(**custom_dataset[0]) # repo1
        self.index.update(**custom_dataset[3]) # repo2
        self.index.commit()
        self.assertEqual(self.index.doccount(),2)

        res, pager = self.index.query(uri="http://example.org/repo1/1")
        self.assertEqual(len(res), 1)
        self.assertEqual(custom_dataset[0],res[0])

        res, pager = self.index.query(uri="http://example.org/repo2/2")
        self.assertEqual(len(res), 1)
        self.assertEqual(custom_dataset[3],res[0])

    
    
class CustomQuery(object):        

    repos = [DocRepo1(), DocRepo2()]

    def load(self, data):
        for doc in data:
            self.index.update(**doc)
            self.index.commit()

    def test_boolean(self):
        self.load(custom_dataset)
        res, pager = self.index.query(ex_secret=True)
        self.assertEqual(len(res),1)
        self.assertEqual(res[0]['dcterms_identifier'], 'R2 D2')
        res, pager = self.index.query(ex_secret=False)
        self.assertEqual(len(res),1)
        self.assertEqual(res[0]['dcterms_identifier'], 'R2 D1')
    
    def test_keywords(self):
        self.load(custom_dataset)
        res, pager = self.index.query(dc_subject='green')
        self.assertEqual(len(res),2)
        identifiers = set([x['dcterms_identifier'] for x in res])
        self.assertEqual(identifiers, set(['R1 D1','R2 D1']))
        
    def test_repo_limited_freetext(self):
        self.load(custom_dataset)
        res, pager = self.index.query('first', repo='repo1')
        self.assertEqual(len(res),2)
        self.assertEqual(res[0]['dcterms_identifier'], 'R1 D1') # contains the term 'first' twice
        self.assertEqual(res[1]['dcterms_identifier'], 'R1 D2') #          -""-             once

    def test_repo_dateinterval(self):
        self.load(custom_dataset)

        res, pager = self.index.query(dcterms_issued=Less(datetime(2013,3,1)))
        self.assertEqual(len(res),1)
        self.assertEqual(res[0]['dcterms_identifier'], 'R1 D1') 

        res, pager = self.index.query(dcterms_issued=More(datetime(2013,3,1)))
        self.assertEqual(res[0]['dcterms_identifier'], 'R1 D2') 

        res, pager = self.index.query(dcterms_issued=Between(datetime(2013,2,1),datetime(2013,4,1)))
        self.assertEqual(len(res),2)
        identifiers = set([x['dcterms_identifier'] for x in res])
        self.assertEqual(identifiers, set(['R1 D1','R1 D2']))


    def test_resource_partial_uri(self):
        self.load(custom_dataset)
        res, pager = self.index.query(dcterms_publisher="*/publ1")
        self.assertEqual(len(res),1)
        self.assertEqual(res[0]['dcterms_identifier'], 'R1 D1') 

#----------------------------------------------------------------
#
# Additional base classes used together with above testcases to yield
# working testcase classes

class ESBase(unittest.TestCase):
    maxDiff = None
    def setUp(self):
        self.maxDiff = None
        self.location = "http://localhost:9200/ferenda/"
        self.index = FulltextIndex.connect("ELASTICSEARCH", self.location, self.repos)

    def tearDown(self):
        self.index.destroy()


class WhooshBase(unittest.TestCase):
    maxDiff = None
    def setUp(self):
        self.location = mkdtemp()
        self.index = FulltextIndex.connect("WHOOSH", self.location, self.repos)

    def tearDown(self):
        self.index.close()
        try:
            self.index.destroy()
        except WindowsError:
            # this happens on Win32 when doing the following sequence of events:
            #
            # i = FulltextIndex.connect("WHOOSH", ...)
            # i.update(...)
            # i.commit()
            # i.update(...)
            # i.commit()
            # i.destroy()
            #
            # Cannot solve this for now. FIXME:
            pass


#----------------------------------------------------------------
#
# The actual testcase classes -- they use multiple inheritance to gain
# both the backend-specific configurations (and the declaration as
# unittest.TestCase classes), and the actual tests. Generally, they
# can be empty (all the magic happens when the classes derive from two
# classes)

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
        want = whoosh.fields.Schema(
            basefile=whoosh.fields.ID(stored=True),
            dcterms_identifier=whoosh.fields.ID(field_boost=16,stored=True),
            dcterms_issued=whoosh.fields.DATETIME(stored=True),
            dcterms_publisher=whoosh.fields.IDLIST(stored=True),
            dcterms_title=whoosh.fields.TEXT(field_boost=4,stored=True),
            rdf_type=whoosh.fields.ID(stored=True, field_boost=1.1), # corresponds to URI not Label
            repo=whoosh.fields.ID(stored=True),
            text=whoosh.fields.TEXT(stored=True),
            uri=whoosh.fields.ID(unique=True, stored=True)
        )
        self.assertEqual(sorted(want.names()), sorted(got.names()))
        for fld in got.names():
            self.assertEqual((fld,want[fld]),(fld,got[fld]))
            
        # finally, try to create again (opening an existing index
        # instead of creating)
        # need mock docrepo
        self.index = FulltextIndex.connect("WHOOSH", self.location, [DocumentRepository()])

       
class WhooshBasicQuery(BasicQuery, WhooshBase): pass

class ESBasicIndex(BasicIndex, ESBase): pass

class ESBasicQuery(BasicQuery, ESBase): pass

class WhooshCustomIndex(CustomIndex, WhooshBase): pass

class ESCustomIndex(CustomIndex, ESBase): pass

class WhooshCustomQuery(CustomQuery, WhooshBase): pass

class ESCustomQuery(CustomQuery, ESBase): pass
