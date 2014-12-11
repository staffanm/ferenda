# -*- coding: utf-8 -*-
from __future__ import unicode_literals, print_function

import sys, os
from datetime import datetime, timedelta
from operator import attrgetter

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
# and constructing Atom feeds from them. The second (Feedsets) mostly
# tests creating the correct set of feeds from given facets and
# indata.
class News(RepoTester):
    def setUp(self):
        super(News, self).setUp()
        # create a bunch of DocumentEntry objects and save them
        basetime = datetime(2013,1,1,12,0)
        for basefile in range(25):
            v = {'id':self.repo.canonical_uri(basefile),
                 'title':"Doc #%s" % basefile}
            de = DocumentEntry()
            de.orig_created = basetime + timedelta(hours=basefile)
            de.orig_updated = basetime + timedelta(hours=basefile,minutes=10)
            de.orig_checked = basetime + timedelta(hours=basefile,minutes=20)
            de.published    = basetime + timedelta(hours=basefile,minutes=30)
            de.updated      = basetime + timedelta(hours=basefile,minutes=40)
            de.orig_url     = "http://source.example.org/doc/%s" % basefile
            de.title        = v['title']
            de.save(self.repo.store.documententry_path(str(basefile)))

            g = rdflib.Graph()
            desc = Describer(g,self.repo.canonical_uri(basefile))
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
        with patch("ferenda.documentrepository.Transformer"):
            # FIXME: To test the entire news() body we need to put it
            # in the fulltext index
            for basefile in range(25):
                self.repo.relate(str(basefile))
            self.repo.news()

    def test_entries(self):
        unsorted_entries = self.repo.news_entries() # not guaranteed particular order
        # sort so that most recently updated first
        entries = sorted(list(unsorted_entries),
                         key=attrgetter('updated'), reverse=True)
        self.assertEqual(len(entries),25)
        self.assertEqual(entries[0].title, "Doc #24")
        self.assertEqual(entries[-1].title, "Doc #0")

    def test_incomplete_entries(self):
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
        self.maxDiff = None
        unsorted_entries = self.repo.news_entries()
        # particular order sort so that most recently updated first
        # (simplified ver of what news() does)
        entries = sorted(list(unsorted_entries),
                         key=lambda x: x.updated, reverse=True)

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
        for basefile in range(25):
            de = DocumentEntry(self.repo.store.documententry_path(str(basefile)))
            util.writefile(self.repo.store.parsed_path(str(basefile)),
                           "<html><p>Document #%s</p></html>" % basefile)
            de.set_content(self.repo.store.parsed_path(str(basefile)),
                           self.repo.canonical_uri(str(basefile)),
                           inline=True)
            de.save()

        unsorted_entries = self.repo.news_entries()
        entries = sorted(list(unsorted_entries),
                         key=lambda x: x.updated, reverse=True)
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

    def test_custom_facet(self):
        self.fail("Implement a test for this to replace the old test_custom_facet")


class Feedsets(RepoTester):
    results2 = json.load(open("test/files/datasets/results2-plus-entries.json"),
                         object_hook=util.make_json_date_object_hook(
                             'published', 'updated'))
    results2data = rdflib.Graph().parse(open("test/files/datasets/results2data.ttl"), format="turtle")

    facets = [Facet(rdftype=RDF.type),
              Facet(rdftype=DCTERMS.publisher),
              Facet(rdftype=DCTERMS.issued)]
    
    feedsets = [Feedset(label="By publisher",
                        feeds=[Feed(title="Books published by Nature",
                                    slug="publisher/nature",
                                    binding="dcterms_publisher",
                                    value="http://example.org/journals/nature"),
                               Feed(title="Books published by Biochem",
                                    slug="publisher/biochem",
                                    binding="dcterms_publisher",
                                    value="http://example.org/journals/biochem"),
                               Feed(title="Books published by Analytical",
                                    slug="publisher/analytical",
                                    binding="dcterms_publisher",
                                    value="http://example.org/journals/analytical")]),
                Feedset(label="By document type",
                        feeds=[Feed(title="bibo:Book",
                                    slug="type/book",
                                    binding="rdf_type",
                                    value="http://purl.org/ontology/bibo/Book")]),
                Feedset(label="main",
                        feeds=[Feed(title="All documents in base",
                                    slug="main",
                                    binding=None,
                                    value=None)])]


    def setUp(self):
        super(News, self).setUp()

    def test_news(self):
        self.repo.news() # gmmm

    def test_feedsets(self):
        got = self.repo.news_feedsets(self.results2, self.facets)
        want = self.feedsets
        self.assertEqual(want, got)

    def test_select_for_feeds(self):
        got = self.repo.news_select_for_feeds(self.results2, self.facets, self.feedsets)
        # first feedset (main) only feed should contain all docs in
        # correct order)
        self.assertEquals(len(got[0].feeds, 1))
        self.assertEquals(len(got[0].feeds[0].entries, 4))
        self.assertEquals(got[0].feeds[0].entries.id, "http://...")
        self.assertEquals(got[0].feeds[3].entries.id, "http://...")

