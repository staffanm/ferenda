# -*- coding: utf-8 -*-
from __future__ import unicode_literals, print_function

import sys, os
from datetime import datetime, timedelta
from operator import attrgetter, itemgetter

if os.getcwd() not in sys.path: sys.path.insert(0,os.getcwd())
from ferenda.manager import setup_logger; setup_logger('CRITICAL')

import copy
import json
import shutil

from six import text_type as str
from lxml import etree
import rdflib
from rdflib import RDF
from rdflib.namespace import DCTERMS

from ferenda.compat import Mock, MagicMock, patch
from ferenda import util
from ferenda.testutil import RepoTester
from ferenda.elements import Link

# SUT
from ferenda import Facet, Feedset, Feed, DocumentEntry, Describer

# two testcase classes: the first (News) mostly tests handling entries
# and constructing Atom feeds from them. Test data is 25 very similar
# entries.

# The second (Feedsets) mostly tests creating the correct set of feeds
# from given facets and indata. Test data is 4 entries that match data
# from testToc.

class News(RepoTester):

    def setUp(self):
        super(News, self).setUp()
        self.faceted_data = []
        # create a bunch of DocumentEntry objects and save them
        basetime = datetime(2013,1,1,12,0)
        for basefile in range(25):
            v = {'id':self.repo.canonical_uri(basefile),
                 'title':"Doc #%s" % basefile}
            self.faceted_data.append({'uri': v['id'],
                                      'dcterms_title': v['title'],
                                      'rdf_type': 'http://xmlns.com/foaf/0.1/Document'})
            de = DocumentEntry()
            de.orig_created = basetime + timedelta(hours=basefile)
            de.orig_updated = basetime + timedelta(hours=basefile, minutes=10)
            de.orig_checked = basetime + timedelta(hours=basefile, minutes=20)
            de.published    = basetime + timedelta(hours=basefile, minutes=30)
            de.updated      = basetime + timedelta(hours=basefile, minutes=40)
            de.orig_url     = "http://source.example.org/doc/%s" % basefile
            de.title        = v['title']
            de.save(self.repo.store.documententry_path(str(basefile)))

            g = rdflib.Graph()
            desc = Describer(g, self.repo.canonical_uri(basefile))
            dcterms = self.repo.ns['dcterms']
            desc.rdftype(self.repo.ns['foaf'].Document)
            desc.value(dcterms.title, "Invalid title")
            util.ensure_dir(self.repo.store.distilled_path(str(basefile)))
            with open(self.repo.store.distilled_path(str(basefile)), "wb") as fp:
                g.serialize(fp, format="pretty-xml")

            util.ensure_dir(self.repo.store.parsed_path(str(basefile)))
            with open(self.repo.store.parsed_path(str(basefile)), "w") as fp:
                fp.write("""<?xml version='1.0' encoding='utf-8'?>
<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML+RDFa 1.0//EN" "http://www.w3.org/MarkUp/DTD/xhtml-rdfa-1.dtd">
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:dcterms="http://purl.org/dc/terms/" xml:lang="en">
  <head about="%(id)s">
    <title>%(title)s</title>
  </head>
  <body about="%(id)s">
    <h1>%(title)s</h1>
  </body>
</html>""" % v)

            util.ensure_dir(self.repo.store.generated_path(str(basefile)))
            with open(self.repo.store.generated_path(str(basefile)), "w") as fp:
                fp.write("""<!DOCTYPE html>
<html>
  <head>
    <title>%(title)s</title>
  </head>
  <body>
    <h1>%(title)s</h1>
  </body>
</html>""" % v)
    def test_news(self):
        # should test the main method, not the helpers. That'll
        # require mocking most methods.
        self.repo.news_facet_entries = MagicMock()
        self.repo.facets = MagicMock()
        self.repo.news_feedsets = Mock()
        self.repo.news_select_for_feeds = Mock()
        self.repo.news_generate_feeds = Mock()

        # this isn't really a good test -- it only verifies the
        # internal implementation of the main method not the
        # behaviour. But other tests verifieds behaviour of individual
        # methods.
        self.repo.news()
        self.repo.news_facet_entries.assert_called()
        self.repo.facets.assert_called()
        self.repo.news_feedsets.assert_called()
        self.repo.news_select_for_feeds.assert_called()
        self.repo.news_generate_feeds.assert_called()


    def test_news_facet_entries(self):
        # setup makes sure that a bunch of DocumentEntry objects
        # exists, and that repo.faceted_data returns a list of dict
        # that corresponds with this
        self.repo.faceted_data = Mock(return_value=self.faceted_data)
        faceted_entries = self.repo.news_facet_entries()
        self.assertEqual(faceted_entries[0]['title'], "Doc #24")
        self.assertEqual(faceted_entries[-1]['title'], "Doc #0")
        self.assertEqual(faceted_entries[-1]['uri'], "http://localhost:8000/res/base/0")
        self.assertEqual(faceted_entries[-1]['dcterms_title'], "Doc #0")
        self.assertEqual(faceted_entries[-1]['rdf_type'],
                         "http://xmlns.com/foaf/0.1/Document")
        self.assertEqual(faceted_entries[-1]['updated'],
                         datetime(2013, 1, 1, 12, 40))

    def test_news_entries(self):
        unsorted_entries = self.repo.news_entries() # not guaranteed particular order
        # sort so that most recently updated first
        entries = sorted(list(unsorted_entries),
                         key=attrgetter('updated'), reverse=True)
        self.assertEqual(len(entries),25)
        self.assertEqual(entries[0].title, "Doc #24")
        self.assertEqual(entries[-1].title, "Doc #0")

    def test_incomplete_entries(self):
        self.repo.faceted_data = Mock(return_value=self.faceted_data)

        # make our entries incomplete in various ways
        entry = DocumentEntry(self.repo.store.documententry_path("1"))
        entry.published = None
        entry.save()

        # try very hard to remove title from everywhere
        entry = DocumentEntry(self.repo.store.documententry_path("2"))
        del entry.title
        entry.save()
        g = rdflib.Graph().parse(self.repo.store.distilled_path("2"))
        g.remove((rdflib.URIRef("http://localhost:8000/res/base/2"),
                  self.repo.ns['dcterms'].title,
                  rdflib.Literal("Doc #2")))
        with open(self.repo.store.distilled_path("2"), "wb") as fp:
            g.serialize(fp, format="pretty-xml")

        os.unlink(self.repo.store.distilled_path("3"))

        # entries w/o published date and w/o distilled file should not
        # be published, but w/o title is OK
        self.assertEqual(len(list(self.repo.news_entries())),
                         23)

        # also make sure that corresponding faceted_entries do not
        # show these non-published entries
        self.assertEqual(len(self.repo.news_facet_entries()), 23)

    def test_republishsource(self):
        self.repo.config.republishsource = True
        for basefile in range(25):
            util.writefile(self.repo.store.downloaded_path(str(basefile)),
                           "Source content")

        entries = sorted(list(self.repo.news_entries()),
                         key=attrgetter('updated'), reverse=True)
        self.assertEqual(entries[0].content['src'],
                         self.repo.downloaded_url("24"))

    def test_write_atom(self):
        self.repo.faceted_data = Mock(return_value=self.faceted_data)
        self.maxDiff = None
        # facet_entries isn't guaranteed to have any particular
        # ordering
        unsorted_entries = self.repo.news_facet_entries()
        entries = sorted(list(unsorted_entries),
                         key=itemgetter('updated'), reverse=True)

        paths = self.repo.news_write_atom(entries, 'New and updated documents', 'main',
                                  archivesize=6)
        d = self.datadir
        want = [self.p('%s/base/feed/main.atom'%d,False),
                self.p('%s/base/feed/main-archive-1.atom'%d,False),
                self.p('%s/base/feed/main-archive-2.atom'%d,False),
                self.p('%s/base/feed/main-archive-3.atom'%d, False)]
        self.assertEqual(paths, want)
        tree = etree.parse('%s/base/feed/main.atom'%d)
        NS = "{http://www.w3.org/2005/Atom}"
        # main-archive-1 0-5
        # main-archive-2 6-11
        # main-archive-3 12-17
        # main           18-24
        
        # assert that prev-archive points to main-archive-3.atom
        prev_archive = tree.find(NS+"link[@rel='prev-archive']")
        self.assertEqual(prev_archive.get("href"), "main-archive-3.atom")

        # assert that title is 'New and updated documents'
        self.assertEqual(tree.find(NS+"title").text, "New and updated documents")
        # assert that entries 18-24 is in main feed
        entries = tree.findall(NS+"entry")
        self.assertEqual(len(entries),7)

        basedate = datetime(2013,1,1,12,0)
        # assert that first entry is doc #24, has correct <id>,
        # <updated>, <published>, <title>, <content src> <link href>
        self._check_entry(entries[0],
                          entryid="http://localhost:8000/res/base/24",
                          published=basedate + timedelta(hours=24,minutes=30),
                          updated=basedate + timedelta(hours=24,minutes=40),
                          title='Doc #24',
                          contentsrc='../parsed/24.xhtml',
                          linksrc='../distilled/24.rdf')

        # same for last entry (doc #18)
        self._check_entry(entries[-1],
                          entryid="http://localhost:8000/res/base/18",
                          published=basedate + timedelta(hours=18,minutes=30),
                          updated=basedate + timedelta(hours=18,minutes=40),
                          title='Doc #18',
                          contentsrc='../parsed/18.xhtml',
                          linksrc='../distilled/18.rdf')

        # open archive-3, assert 6 entries,
        # prev-archive=main-archive-2, next-archive=main.atom
        tree = etree.parse('%s/base/feed/main-archive-3.atom'%d)
        self.assertEqual(len(tree.findall(NS+"entry")),6)
        self.assertEqual(tree.find(NS+"link[@rel='prev-archive']").get("href"),
                         "main-archive-2.atom")
        self.assertEqual(tree.find(NS+"link[@rel='next-archive']").get("href"),
                         "main.atom")

        # open archive-2, assert 6 entries,
        # prev-archive=main-archive-1, next-archive=main-archive-3
        tree = etree.parse('%s/base/feed/main-archive-2.atom'%d)
        self.assertEqual(len(tree.findall(NS+"entry")),6)
        self.assertEqual(tree.find(NS+"link[@rel='prev-archive']").get("href"),
                         "main-archive-1.atom")
        self.assertEqual(tree.find(NS+"link[@rel='next-archive']").get("href"),
                         "main-archive-3.atom")

        # open archive-1, assert 6 entries, no
        # prev-archive, next-archive=main-archive-2
        tree = etree.parse('%s/base/feed/main-archive-1.atom'%d)
        self.assertEqual(len(tree.findall(NS+"entry")),6)
        self.assertIsNone(tree.find(NS+"link[@rel='prev-archive']"))
        self.assertEqual(tree.find(NS+"link[@rel='next-archive']").get("href"),
                         "main-archive-2.atom")

        # finally , do it all again without any entries and make sure
        # it doesn't blow up
        paths = self.repo.news_write_atom([],
                                          'New and updated documents',
                                          'main',
                                          archivesize=6)


    def test_write_atom_inline(self):
        self.repo.faceted_data = Mock(return_value=self.faceted_data)
        for basefile in range(25):
            de = DocumentEntry(self.repo.store.documententry_path(str(basefile)))
            util.writefile(self.repo.store.parsed_path(str(basefile)),
                           "<html><p>Document #%s</p></html>" % basefile)
            de.set_content(self.repo.store.parsed_path(str(basefile)),
                           self.repo.canonical_uri(str(basefile)),
                           inline=True)
            de.save()

        unsorted_entries = self.repo.news_facet_entries()
        entries = sorted(list(unsorted_entries),
                         key=itemgetter('updated'), reverse=True)
        self.repo.news_write_atom(entries,
                                  'New and updated documents',
                                  'main',
                                  archivesize=6)
        tree = etree.parse('%s/base/feed/main.atom' % self.datadir)
        NS = "{http://www.w3.org/2005/Atom}"
        content = tree.find(".//"+NS+"content")
        self.assertIsNone(content.get("src"))
        self.assertIsNone(content.get("hash"))
        self.assertEqual(content.get("type"), "xhtml")
        self.assertEqualXML(etree.tostring(content[0]),
                              '<html xmlns="http://www.w3.org/2005/Atom" xmlns:le="http://purl.org/atompub/link-extensions/1.0"><p>Document #24</p></html>')
                                             

    def _check_entry(self, entry, entryid, title, published, updated, contentsrc, linksrc):

        NS = "{http://www.w3.org/2005/Atom}"
        self.assertEqual(entry.find(NS+"id").text,entryid)
        self.assertEqual(entry.find(NS+"title").text,title)
        self.assertEqual(entry.find(NS+"published").text,
                         util.rfc_3339_timestamp(published))
        self.assertEqual(entry.find(NS+"updated").text,
                         util.rfc_3339_timestamp(updated))

        content = entry.find(NS+"content")
        self.assertEqual(content.get("src"), contentsrc)
        self.assertEqual(content.get("type"), 'application/html+xml')
        link = entry.find(NS+"link[@rel='alternate']")
        self.assertEqual(link.get("href"), linksrc)
        self.assertEqual(link.get("type"),'application/rdf+xml')


class Feedsets(RepoTester):
    results2 = json.load(open("test/files/datasets/results2-plus-entries.json"),
                         object_hook=util.make_json_date_object_hook(
                             'published', 'updated'))
    results2data = rdflib.Graph().parse(open("test/files/datasets/results2data.ttl"), format="turtle")

    facets = [Facet(rdftype=RDF.type),
              Facet(rdftype=DCTERMS.publisher),
              Facet(rdftype=DCTERMS.issued)]
    
    feedsets = [
        Feedset(label="Sorted by type",
                predicate=RDF.type,
                feeds=[Feed(title="All Book documents",
                            slug="type/book",
                            binding="rdf_type",
                            value="Book")]),
        Feedset(label="Sorted by publisher",
                predicate=DCTERMS.publisher,
                feeds=[Feed(title="Documents published by Analytical Biochemistry",
                            slug="publisher/analytical",
                            binding="dcterms_publisher",
                            value="analytical"),
                       Feed(title="Documents published by Journal of Biological Chemistry",
                            slug="publisher/biochem",
                            binding="dcterms_publisher",
                            value="biochem"),
                       Feed(title="Documents published by Nature",
                            slug="publisher/nature",
                            binding="dcterms_publisher",
                            value="nature")]),
        Feedset(label="All",
                predicate=None,
                feeds=[Feed(title="All documents",  # "... in base" ? 
                            slug="main",
                            binding=None,
                            value=None)])]


    def setUp(self):
        super(Feedsets, self).setUp()
        self.repo.news_facet_entries = Mock(return_value=self.results2)
        self.repo._commondata = self.results2data

    def test_feedsets(self):
        got = self.repo.news_feedsets(self.results2, self.facets)
        want = self.feedsets

        # make sure 3 feedsets were created and their labels
        self.assertEqual(3, len(got))
        self.assertEqual("Sorted by type", got[0].label)
        self.assertEqual("Sorted by publisher", got[1].label)
        self.assertEqual("All", got[2].label)

        # make sure the title of the only feed in the first feedset
        # turned out OK
        self.assertEqual("All Book documents",
                         got[0].feeds[0].title)

        # make sure the publisher feedset has the correct things
        self.assertEqual(3, len(got[1].feeds)) # 3 different journals
        self.assertEqual("publisher/analytical", got[1].feeds[0].slug)
        self.assertEqual("Documents published by Analytical Biochemistry",
                         got[1].feeds[0].title)

        # this test incorporates all of the above
        self.assertEqual(want, got)

    def test_select_for_feeds(self):
        got = self.repo.news_select_for_feeds(self.results2, self.feedsets, self.facets)
        # last feedset (main) should have one single feed and it
        # should contain all entries.
        self.assertEquals(len(got[-1].feeds), 1)
        self.assertEquals(len(got[-1].feeds[0].entries), 4)
        self.assertEquals("http://example.org/articles/pm942051",
                          got[-1].feeds[0].entries[0]['uri'])
        self.assertEquals("http://example.org/articles/pm14907713",
                          got[-1].feeds[0].entries[3]['uri'])

