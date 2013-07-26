#!/usr/bin/env python
# -*- coding: iso-8859-1 -*-
import sys
import os
import re
from datetime import datetime, date

from rdflib import Graph, Namespace, URIRef, Literal, RDF

from ferenda import DocumentRepository
from ferenda.elements import serialize
from .swedishlegalsource import Stycke, Sektion
from ferenda import util
import ferenda.legaluri
from ferenda.legalref import LegalRef, Link


__version__ = (1, 6)
__author__ = "Staffan Malmgren <staffan@tomtebo.org>"


class JK(DocumentRepository):
    module_dir = "jk"

    start_url = "http://www.jk.se/beslut/default.asp"
    document_url = "http://www.jk.se/beslut/XmlToHtml.asp?XML=Files/%s.xml&XSL=../xsl/JK_Beslut.xsl"

    def download_everything(self, cache=False):
        self.browser.open(self.start_url)
        for avd in (self.browser.links(url_regex=r'Default.asp\?Type=\d+')):
            self.log.info(
                "Retrieving section '%s'" % avd.text.decode('iso-8859-1'))
            self.browser.follow_link(avd)
            url = None
            for dok in (self.browser.links(url_regex=r'XmlToHtml.asp\?XML=Files/\d+\w*-\d+-\d+')):
                m = re.search("(\d+\w*-\d+-\d+)", dok.url)
                if m.group(1) != url:
                    url = m.group(1)
                    self.download_single(url, cache)

    def parse_from_soup(self, soup):
        # Step 1: Find out basic metadata
        rubrik = soup.first("title").string
        beslutsdatum = soup.first(
            "meta", {'name': 'SG_Beslutsdatum'})['content']
        # Converting this into a proper date object makes the RDFa
        # statement use a typed literal (xsd:date), which is nice, but
        # the currently released pyRdfa package doesn't support this
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
            text = util.element_text(p)
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


if __name__ == "__main__":
    JK.run()
