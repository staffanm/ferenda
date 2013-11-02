# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import re
from datetime import datetime
from six.moves.urllib_parse import urljoin

from rdflib import Graph
from rdflib import URIRef
from rdflib import Literal
from rdflib import RDF
import requests
from bs4 import BeautifulSoup

from . import SwedishLegalSource
from .swedishlegalsource import Stycke, Sektion
from ferenda.decorators import downloadmax, recordlastdownload
from ferenda import util
from ferenda.sources.legal.se.legalref import LegalRef, Link


class JK(SwedishLegalSource):
    alias = "jk"

    start_url = "http://www.jk.se/Beslut.aspx?query=&type=all&dateFrom=1998-01-01&dateTo=2100-01-01&dnr="
    document_url_regex = "http://www.jk.se/Beslut/(?P<kategori>[\w\-]+)/(?P<basefile>\d+\-\d+\-\d+).aspx"

    @recordlastdownload
    def download(self, basefile=None):
        for basefile, url in self.download_get_basefiles(self.start_url):
            self.download_single(basefile, url)

    @downloadmax
    def download_get_basefiles(self, start_url):
        document_url_regex = re.compile("(?P<basefile>\d+\-\d+\-\d+).aspx")
        done = False
        url = start_url
        pagecount = 1
        while not done:
            self.log.info("Getting page #%s" % pagecount)
            soup = BeautifulSoup(requests.get(url).text)
            for link in soup.find_all("a", href=document_url_regex):
                basefile = document_url_regex.search(link["href"]).group("basefile")
                yield basefile, urljoin(url, link["href"])

            next = soup.find("img", src="/common/images/navigation-pil-grey.png").find_parent("a")
            if next:
                url = urljoin(url, next["href"])
                pagecount += 1
            else:
                done = True

    def parse_from_soup(self, soup):
        # Step 1: Find out basic metadata
        rubrik = soup.first("title").string
        beslutsdatum = soup.first(
            "meta", {'name': 'SG_Beslutsdatum'})['content']

        beslutsdatum = datetime.strptime(beslutsdatum, "%Y-%m-%d").date()
        diarienummer = soup.first(
            "meta", {'name': 'SG_Dokumentbet'})['content']
        arendetyp = soup.first("meta", {'name': 'Subject'})['content']
        # the keywords for a documents is contained in a metatag
        # formatted like:
        #    <meta name="Keywords" content="hets_mot_folkgrupp\nmeddelarfrihet\åklagare">
        #
        # Transform this into an array like:
        #    [u'http://lagen.nu/concept/Hets_mot_folkgrupp',
        #     u'http://lagen.nu/concept/Meddelarfrihet',
        #     u'http://lagen.nu/concept/Åklagare']
        nyckelord = soup.first("meta", {'name': 'Keywords'})['content']
        begrepp = ['http://lagen.nu/concept/%s' % util.ucfirst(
            x).strip().replace(" ", "_") for x in nyckelord.split("\n")]

        # Step 2: Using the metadata, construct the canonical URI for this document
        uri = LegalURI.construct({'type': LegalRef.MYNDIGHETSBESLUT,
                                  'myndighet': 'jk',
                                  'dnr': diarienummer})
        # self.log.debug("URI: %s" % uri)

        # Step 3: Create a RDF graph of all our metadata (so far)
        g = Graph()
        g.bind('dct', self.ns['dct'])
        g.bind('rinfo', self.ns['rinfo'])
        g.bind('rinfoex', self.ns['rinfoex'])
        g.bind('xsd', util.ns['xsd'])
        g.add((
            URIRef(uri), self.ns['dct']['title'], Literal(rubrik, lang="sv")))
        g.add((URIRef(uri), self.ns['rinfo']['beslutsdatum'],
              Literal(beslutsdatum, lang="sv")))
        g.add((URIRef(uri), self.ns['rinfo']['diarienummer'],
              Literal(diarienummer, lang="sv")))
        g.add((URIRef(uri), self.ns['rinfoex']['arendetyp'],
              Literal(arendetyp, lang="sv")))
        for s in begrepp:
            g.add((URIRef(uri), self.ns['dct']['subject'], URIRef(s)))

        g.add((URIRef(uri), self.ns['dct']['identifier'], Literal(
            "JK %s" % diarienummer, lang="sv")))
        g.add((URIRef(uri), RDF.type, self.rdf_type))

        # Step 4: Process the actual text of the document
        self.parser = LegalRef(LegalRef.LAGRUM,
                               LegalRef.KORTLAGRUM,
                               LegalRef.RATTSFALL,
                               LegalRef.FORARBETEN)

        # newer documents have a semantic structure with h1 and h2
        # elements. Older have elements like <p class="Rubrik_1">. Try
        # to determine which one we're dealing with?
        tag = soup.find('a', {'name': "Start"})
        if tag:
            # self.log.debug("Using new-style document structure")
            elements = tag.parent.findAllNext()
        else:
            # self.log.debug("Using old-style document structure")
            elements = soup.findAll("p")
        # self.log.debug("Found %d elements" % len(elements))
        from collections import deque
        elements = deque(elements)
        body = self.make_sektion(elements, "Referat av beslut")

        # Step 5: Combine the metadata and the document, and return it
        doc = {'meta': g,
               'body': body,
               'lang': 'sv',
               'uri': uri}
        return doc

    def make_sektion(self, elements, heading, level=0):
        sekt = Sektion(**{"rubrik": heading,
                          "niva": level})
        self.log.debug(
            "%sCreated sektion(%d): '%s'" % ("  " * level, level, heading))
        baseuri = None
        while True:
            try:
                p = elements.popleft()
            except IndexError:
                return sekt
            text = p.get_text(strip=True)
            # self.log.debug("%sp.name: %s, p['class']: %s, 'class' in p.attrs: %s" % ("  "*level,p.name,p['class'], (u'class' in p.attrs[0])))
            new_level = None
            if p.name == "h1":
                new_level = 1
            elif p.name == "h2":
                new_level = 2
            elif p.name == "h3":
                new_level = 3
            elif ((p.name == "p") and
                  (len(p.attrs) > 0) and
                  ('class' in p.attrs[0]) and
                  (p['class'].startswith("Rubrik_"))):
                # self.log.debug("%sp.class: %s" % ("  "*level,p['class']))
                new_level = int(p['class'][7:])

            if new_level:
                if new_level > level:
                    sekt.append(self.make_sektion(elements, text, new_level))
                else:
                    elements.appendleft(p)
                    return sekt
            else:
                if text:
                    nodes = self.parser.parse(text,
                                              baseuri=baseuri,
                                              predicate="dct:references")
                    for node in nodes:
                        # Use possible SFS references as the the
                        # baseuri for subsequent paragraphs
                        if isinstance(node, Link) and node.uri.startswith("http://rinfo.lagrummet.se/publ/sfs/"):
                            baseuri = node.uri

                    stycke = Stycke(nodes)
                    # self.log.debug("%sCreated stycke: '%s'" % ("  "*level,stycke))
                    sekt.append(stycke)
