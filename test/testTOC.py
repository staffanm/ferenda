# -*- coding: utf-8 -*-
from __future__ import unicode_literals, print_function

import sys, os
# from ferenda.compat import unittest
if os.getcwd() not in sys.path: sys.path.insert(0,os.getcwd())
from ferenda.manager import setup_logger; setup_logger('CRITICAL')

import copy
import json
import shutil

from lxml import etree
from rdflib import RDF, Graph
from rdflib.namespace import DCTERMS

from ferenda.compat import Mock, MagicMock, patch
from ferenda import util
from ferenda.testutil import RepoTester
from ferenda.elements import Link

# SUT
from ferenda import Facet, TocPageset, TocPage # , DocumentRepository

class TOC(RepoTester):
    results1 = json.load(open("test/files/datasets/results1.json"))
    results2 = json.load(open("test/files/datasets/results2.json"))
    results2data = Graph().parse(open("test/files/datasets/results2data.ttl"), format="turtle")
    pagesets = [TocPageset('Sorted by title',[
                TocPage('a','Documents starting with "a"','dcterms_title', 'a'),
                TocPage('d','Documents starting with "d"','dcterms_title', 'd'),
                TocPage('h','Documents starting with "h"','dcterms_title', 'h'),
                TocPage('l','Documents starting with "l"','dcterms_title', 'l')
                ], DCTERMS.title),
                TocPageset('Sorted by publication year',[
                TocPage('1791','Documents published in 1791','dcterms_issued', '1791'),
                TocPage('1859','Documents published in 1859','dcterms_issued', '1859'),
                TocPage('1937','Documents published in 1937','dcterms_issued', '1937'),
                TocPage('1939','Documents published in 1939','dcterms_issued', '1939'),
                TocPage('1943','Documents published in 1943','dcterms_issued', '1943'),
                TocPage('1954','Documents published in 1954','dcterms_issued', '1954')
                ], DCTERMS.issued)]

    pagesets2 = [TocPageset('Sorted by publisher',[
        TocPage('Analytical Biochemistry',
                'Documents published in Analytical Biochemistry',
                'dcterms_publisher', 'analytical'),
        TocPage('Journal of Biological Chemistry',
                'Documents published in Journal of Biological Chemistry',
                'dcterms_publisher', 'biochem'),
        TocPage('Nature',
                'Documents published in Nature',
                'dcterms_publisher', 'nature'),
    ], DCTERMS.publisher)]
    
    documentlists = {
        ('dcterms_issued', '1791'): [[Link("Dream of the Red Chamber",uri='http://example.org/books/Dream_of_the_Red_Chamber')]],
        ('dcterms_issued', '1859'): [[Link("A Tale of Two Cities",uri='http://example.org/books/A_Tale_of_Two_Cities')]],
        ('dcterms_issued', '1937'): [[Link("The Hobbit",uri='http://example.org/books/The_Hobbit')]],
        ('dcterms_issued', '1939'): [[Link("And Then There Were None",uri='http://example.org/books/And_Then_There_Were_None')]], 
        ('dcterms_issued', '1943'): [[Link("The Little Prince",uri='http://example.org/books/The_Little_Prince')]],
        ('dcterms_issued', '1954'): [[Link("The Lord of the Rings",uri='http://example.org/books/The_Lord_of_the_Rings')]],
        ('dcterms_title', 'a'): [[Link("And Then There Were None",uri='http://example.org/books/And_Then_There_Were_None')],
                    [Link("A Tale of Two Cities",uri='http://example.org/books/A_Tale_of_Two_Cities')]],
        ('dcterms_title', 'd'): [[Link("Dream of the Red Chamber",uri='http://example.org/books/Dream_of_the_Red_Chamber')]],
        ('dcterms_title', 'h'): [[Link("The Hobbit",uri='http://example.org/books/The_Hobbit')]],
        ('dcterms_title', 'l'): [[Link("The Little Prince",uri='http://example.org/books/The_Little_Prince')],
                    [Link("The Lord of the Rings",uri='http://example.org/books/The_Lord_of_the_Rings')]]
        }

    facets = [Facet(rdftype=RDF.type),
              Facet(rdftype=DCTERMS.title),
              Facet(rdftype=DCTERMS.issued)]


    def setUp(self):
        super(TOC, self).setUp()
        resources = self.datadir+os.sep+"rsrc"+os.sep+"resources.xml"
        util.ensure_dir(resources)
        shutil.copy2("%s/files/base/rsrc/resources.xml"%os.path.dirname(__file__),
                     resources)

    def test_toc(self):
        # tests the main TOC method, not the helper methods (they are
        # tested separately)
        self.repo.facets = MagicMock()
        self.repo.facet_select = MagicMock()
        self.repo.facet_query = MagicMock()
        self.repo.faceted_data = MagicMock()
        self.repo.log = Mock()
        self.repo.toc_pagesets = Mock()
        self.repo.toc_select_for_pages = Mock()
        self.repo.toc_generate_pages = Mock()
        self.repo.toc_generate_first_page = Mock()
        with patch('json.dump'):
            self.repo.toc()

        # assert facet_query was properly called, error and info msg
        # was printed
        self.assertEqual("http://localhost:8000/dataset/base",
                         self.repo.facet_query.call_args[0][0])
        self.assertTrue(self.repo.log.error.called)
        self.assertTrue(self.repo.log.info.called)
        # and that the rest of the methods were NOT called
        self.assertFalse(self.repo.toc_pagesets.called)
        self.assertFalse(self.repo.toc_select_for_pages.called)
        self.assertFalse(self.repo.toc_generate_pages.called)

        # test2: facet_select returns something
        self.repo.faceted_data.return_value = ["fake", "data"]
        with patch('json.load'):
            self.repo.toc()
        # Now all other methods should be called
        self.assertTrue(self.repo.toc_pagesets.called)
        self.assertTrue(self.repo.toc_select_for_pages.called)
        self.assertTrue(self.repo.toc_generate_pages.called)

    def test_toc_pagesets(self):
        got = self.repo.toc_pagesets(self.results1, self.facets)
        want = self.pagesets
        self.assertEqual(2, len(got))
        self.assertEqual(want[0].label, got[0].label)
        self.assertEqual(want[0].pages[0], got[0].pages[0])
        self.assertEqual(want[0], got[0])
        self.assertEqual(want[1], got[1])

        # delete title from one place in self.results1
        res = copy.deepcopy(self.results1)
        del res[0]['dcterms_title']
        del res[1]['dcterms_issued']
        got = self.repo.toc_pagesets(res, self.facets)
        self.assertEqual(5, len(got[1].pages))
        
    def test_select_for_pages(self):
        got = self.repo.toc_select_for_pages(self.results1, self.pagesets, self.facets)
        want = self.documentlists
        self.assertEqual(want, got)

        # delete issued from one place in self.results1
        res = copy.deepcopy(self.results1)
        del res[1]['dcterms_issued']
        # FIXME: this'll go boom!
        # del res[0]['title']
        got = self.repo.toc_select_for_pages(res, self.pagesets, self.facets)
        self.assertEqual(len(got), 9)

    def test_pageset_resourcelabel(self):
        facets = [Facet(DCTERMS.publisher,
                        pagetitle="Documents published in %(selected)s")]
        # FIXME: This is mucking about with internal details...
        self.repo._commondata = self.results2data
        got = self.repo.toc_pagesets(self.results2, facets)
        want = self.pagesets2
        self.assertEqual(want, got)

    def test_generate_page(self):
        path = self.repo.toc_generate_page('dcterms_title','a', self.documentlists[('dcterms_title','a')], self.pagesets)
        # 2. secondly, test resulting HTML file
        self.assertTrue(os.path.exists(path))
        t = etree.parse(path)
        
        #with open(path) as fp:
        #    print(fp.read().decode('utf-8'))

        # Various other tests on a.html
        # 2.1 CSS links, relativized correctly?
        css = t.findall("head/link[@rel='stylesheet']")
        self.assertEqual(len(css),4) # normalize, main, ferenda, and fonts.googleapis.com
        
        self.assertRegex(css[0].get('href'), '^../../../rsrc/css')
        
        # 2.2 JS links, relativized correctly?
        js = t.findall("head/script")
        self.assertEqual(len(js),4) # jquery, modernizr, respond and ferenda
        self.assertRegex(js[0].get('src'), '^../../../rsrc/js')
        # 2.3 <nav id="toc"> correct (c.f 1.2)
        navlinks = t.findall(".//nav[@id='toc']//li/a")
        self.assertEqual(len(navlinks),9)

        self.assertEqual(navlinks[0].get("href"), 'http://localhost:8000/dataset/base?dcterms_title=d')
        self.assertEqual(navlinks[3].get("href"), 'http://localhost:8000/dataset/base?dcterms_issued=1791')
        
        # 2.4 div[@class='main-container']/article (c.f 1.3)
        docs = t.findall(".//ul[@role='main']/li/a")
        self.assertEqual(len(docs),2)
        # "And..." should go before "A Tale..."
        self.assertEqual(docs[0].text, 'And Then There Were None')
        self.assertEqual(docs[0].attrib['href'], 'http://example.org/books/And_Then_There_Were_None')
        
        # 2.5 <header><h1><a> correct?
        header = t.find(".//header/h1/a")
        self.assertEqual(header.text, 'testsite')
       
        # 2.6 div[@class='main-container']/h1 correct?
        header = t.find(".//div[@class='main-container']//h1")
        self.assertEqual(header.text, 'Documents starting with "a"')

    def test_generate_page_staticsite(self):
        self.repo.config.staticsite = True
        path = self.repo.toc_generate_page('dcterms_title','a', 
                                           self.documentlists[('dcterms_title','a')], 
                                           self.pagesets)
        t = etree.parse(path)

        # TOC link should be relativized
        navlinks = t.findall(".//nav[@id='toc']//li/a")
        self.assertEqual('d.html', navlinks[0].get("href"))
        self.assertEqual('../dcterms_issued/1791.html', navlinks[3].get("href"))

        header = t.find(".//header/h1/a")
        # from /base/toc/title/a.html -> /index.html = 3 levels up
        self.assertEqual('../../../index.html', header.get("href"))

        headernavlinks = t.findall(".//header/nav/ul/li/a")    
        self.assertEqual('../index.html', headernavlinks[0].get("href"))

        # docs (which in this case use non-base-repo-contained URIs, should be unaffected
        docs = t.findall(".//ul[@role='main']/li/a")
        self.assertEqual('http://example.org/books/And_Then_There_Were_None', docs[0].get("href"))

    def test_generate_pages(self):
        paths = self.repo.toc_generate_pages(self.documentlists,self.pagesets)
        self.assertEqual(len(paths), 10)
        #print("=============%s====================" % paths[0])
        #with open(paths[0]) as fp:
        #    print(fp.read())
        for path in paths:
            self.assertTrue(os.path.exists(path))

    def test_generate_first_page(self):
        path = self.repo.toc_generate_first_page(self.documentlists,self.pagesets)
        self.assertEqual(path, self.p("base/toc/index.html"))
        self.assertTrue(os.path.exists(path))
        tree = etree.parse(path)
        # check content of path, particularly that css/js refs
        # and pageset links are correct. Also, that the selected
        # indexpage is indeed the first (eg. title/a)
        # (NOTE: the first page in the first pageset (by title/a) isn't linked. The second one (by title/d) is).
        self.assertEqual("http://localhost:8000/dataset/base?dcterms_title=d",
                         tree.find(".//nav[@id='toc']").findall(".//a")[0].get("href"))
        self.assertEqual("../../rsrc/css/normalize-1.1.3.css",
                         tree.find(".//link").get("href"))
                         
        self.assertEqual('Documents starting with "a"',
                         tree.find(".//article/h1").text)
                         
    def test_more(self):
        from ferenda import DocumentRepository
        d = DocumentRepository()
        rows = [{'uri':'http://ex.org/1','dcterms_title':'Abc','dcterms_issued':'2009-04-02'},
                {'uri':'http://ex.org/2','dcterms_title':'Abcd','dcterms_issued':'2010-06-30'},
                {'uri':'http://ex.org/3','dcterms_title':'Dfg','dcterms_issued':'2010-08-01'}]
        from rdflib.namespace import DCTERMS
        facets = [Facet(DCTERMS.title), Facet(DCTERMS.issued)]
        pagesets=d.toc_pagesets(rows,facets)
        expected={('dcterms_title','a'):[[Link('Abc',uri='http://ex.org/1')],
                                         [Link('Abcd',uri='http://ex.org/2')]],
                  ('dcterms_title','d'):[[Link('Dfg',uri='http://ex.org/3')]],
                  ('dcterms_issued','2009'):[[Link('Abc',uri='http://ex.org/1')]],
                  ('dcterms_issued','2010'):[[Link('Abcd',uri='http://ex.org/2')],
                                             [Link('Dfg',uri='http://ex.org/3')]]}
        got = d.toc_select_for_pages(rows, pagesets, facets)
        self.assertEqual(expected, got)
        
