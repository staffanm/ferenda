#!/usr/bin/env python
# -*- coding: iso-8859-1 -*-
"""Hanterar beslut från Riksdagens Ombudsmän, www.jo.se

Modulen hanterar hämtande av beslut från JOs webbplats samt
omvandlande av dessa till XML.
"""
# From python stdlib
import unittest
import sys
import time
import re
import os
import datetime
import xml.etree.cElementTree as ET  # Python 2.5 spoken here
import logging
# 3rd party modules
from bs4 import BeautifulSoup

# My own stuff
from ferenda import DocumentRepository
from ferenda import util

__version__ = (0, 1)
__author__ = "Staffan Malmgren <staffan@tomtebo.org>"
__shortdesc__ = "Beslut från JO"
__moduledir__ = "jo"
log = logging.getLogger(__moduledir__)


class JO(DocumentRepository):

    def __init__(self, baseDir="data"):
        self.dir = baseDir + "/jo/downloaded"
        if not os.path.exists(self.dir):
            util.mkdir(self.dir)
        self.ids = {}

    def DownloadAll(self):
        """Hämtar alla avgöranden"""
        # we should think about clearing (part of) the cache here, or
        # make noncached requests -- a stale index page would not be
        # good. Alternatively just request descisions for the current
        # year or similar.
        html = Robot.Get("http://www.jo.se/Page.aspx?MenuId=106&MainMenuId=106&Language=sv&ObjectClass=DynamX_SFS_Decisions&Action=Search&Reference=&Category=0&Text=&FromDate=&ToDate=&submit=S%F6k")
        soup = BeautifulSoup.BeautifulSoup(html)
        self._downloadDecisions(soup)
        self._saveIndex()

    def DownloadNew(self):
        pass

    def _downloadDecisions(self, soup):
        re_descPattern = re.compile(
            'Beslutsdatum: (\d+-\d+-\d+) Diarienummer: (.*)')
        for result in soup.first('div', {'class': 'SearchResult'}):
            if result.a['href']:
                url = urllib.basejoin("http://www.jo.se/", result.a['href'])
                # Seems to be a bug in BeautifulSoup - properly
                # escaped & entities are not de-escaped
                url = url.replace('&amp;', '&')
                desc = result.contents[-1].string
                m = re_descPattern.match(desc)
                beslutsdatum = m.group(1)
                id = m.group(2)
                filename = id.replace('/', '-') + ".html"

                resource = LegalSource.DownloadedResource(id)
                resource.url = url
                resource.localFile = filename
                log.info('Storing %s as %s' % (url, filename))
                Robot.Store(url, None, self.dir + "/" +
                            id.replace('/', '-') + ".html")
                resource.fetched = time.localtime()
                if id in self.ids:
                    log.warn('replacing URL of id %s to %s (was %s)' %
                             (id, url, self.ids[id].url))
                self.ids[id] = resource

#class JOParser(LegalSource.Parser):

    def __init__(self, id, file, baseDir):
        self.id = id
        self.dir = baseDir + "/jo/parsed"
        if not os.path.exists(self.dir):
            util.mkdir(self.dir)
        self.file = file
        log.info('Loading file %s' % file)

    def Parse(self):
        import codecs
        soup = BeautifulSoup.BeautifulSoup(codecs.open(
            self.file, encoding="iso-8859-1", errors='replace').read())

        root = ET.Element("Beslut")
        meta = ET.SubElement(root, "Metadata")
        arendenummer = ET.SubElement(meta, "Ärendenummer")
        arendenummer.text = soup.first('h2').b.i.string.strip()
        titel = ET.SubElement(meta, "Titel")
        titel.text = soup.first('h3').string.strip()
        arendemening = ET.SubElement(meta, "Ärendemening")
        arendemening.text = soup.firstText("Ärendemening: ").parent.parent.parent.parent.contents[1].string.strip()
        avdelning = ET.SubElement(meta, "Avdelning")
        avdelning.text = soup.firstText('Avdelning: ').parent.parent.parent.parent.contents[1].string.strip()
        beslutsdatum = ET.SubElement(meta, "Beslutsdatum")
        beslutsdatum.text = soup.firstText('Beslutsdatum: ').parent.parent.parent.parent.contents[1].string.strip()
        beslut = ET.SubElement(meta, "Beslut")
        beslut.text = soup.firstText(
            'Beslut: ').parent.parent.parent.parent.contents[1].string.strip()

        referat = ET.SubElement(root, "Referat")

        node = soup.firstText('Referat:').parent.parent.parent.nextSibling

        while node.name == 'p':
            stycke = ET.SubElement(referat, "Stycke")
            stycke.text = node.string
            node = node.nextSibling

        tree = ET.ElementTree(root)
        tree.write(self.dir + "/" + self.id + ".xml", encoding="iso-8859-1")

# class JOManager(LegalSource.Manager):
    def _get_module_dir(self):
        return __moduledir__

    def DownloadNew(self):
        log.info('DownloadNew not implemented')

    def ParseAll(self):
        log.info('ParseAll not implemented')
        return

    def IndexAll(self):
        log.info('JO: IndexAll not implemented')
        return

    def GenerateAll(self):
        log.info('JO: GenerateAll not implemented')
        return

    def RelateAll(self):
        log.info('JO: RelateAll not implemented')
        return


class TestJOCollection(unittest.TestCase):
    baseDir = "testdata"

    def testDownloadAll(self):
        c = JODownloader(self.baseDir)
        c.DownloadAll()
        # FIXME: come up with some actual tests

    def testParse(self):
        p = JOParser("1997-2944",
                     "testdata/jo/downloaded/1997-2944.html", self.baseDir)
        p.parse()
        # FIXME: come up with actual test (like comparing the
        # resulting XML file to a known good file)

if __name__ == "__main__":
    # unittest.main()
    import logging.config
    logging.config.fileConfig('etc/log.conf')
    suite = unittest.defaultTestLoader.loadTestsFromName(
        "JO.TestJOCollection.testDownloadAll")
    unittest.TextTestRunner(verbosity=2).run(suite)
