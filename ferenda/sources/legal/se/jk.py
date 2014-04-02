# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import re, os
from datetime import datetime, timedelta
from six.moves.urllib_parse import urljoin

import requests
from bs4 import BeautifulSoup

from . import SwedishLegalSource, RPUBL, SwedishCitationParser
from .swedishlegalsource import Stycke
from ferenda.decorators import downloadmax, recordlastdownload, newstate
from ferenda import util
from ferenda import Describer, FSMParser
from ferenda.sources.legal.se.legalref import LegalRef
from ferenda.sources.legal.se import legaluri
from ferenda.elements import Body, CompoundElement

class Sektion(CompoundElement):
    tagname = "div"

class JK(SwedishLegalSource):
    alias = "jk"

    start_url = "http://www.jk.se/Beslut.aspx?query=&type=all&dateFrom=%(date)s&dateTo=2100-01-01&dnr="
    document_url_regex = "http://www.jk.se/Beslut/(?P<kategori>[\w\-]+)/(?P<basefile>\d+\-\d+\-\d+).aspx"
    rdf_type = RPUBL.VagledandeMyndighetsavgorande
    
    @recordlastdownload
    def download(self, basefile=None):
        if self.config.lastdownload and not self.config.refresh:
            # allow for 30 day window between decision date and publishing
            startdate = self.config.lastdownload - timedelta(days=30)
            start_url = self.start_url % {'date':
                                          datetime.strftime(startdate, "%Y-%m-%d")}
        else:
            start_url = self.start_url % {'date':"1998-01-01"}

        for basefile, url in self.download_get_basefiles(start_url):
            self.download_single(basefile, url)

    @downloadmax
    def download_get_basefiles(self, start_url):
        document_url_regex = re.compile("(?P<basefile>\d+\-\d+\-\d+).aspx")
        done = False
        url = start_url
        pagecount = 1
        self.log.debug("Starting at %s" % start_url)
        while not done:
            self.log.debug("Getting page #%s" % pagecount)
            soup = BeautifulSoup(requests.get(url).text)
            for link in soup.find_all("a", href=document_url_regex):
                basefile = document_url_regex.search(link["href"]).group("basefile")
                yield basefile, urljoin(url, link["href"])

            next = soup.find("img", src="/common/images/navigation-pil-grey.png")
            if next and next.find_parent("a") and next.find_parent("a").get("href"):
                url = urljoin(url, next.find_parent("a")["href"])
                pagecount += 1
            else:
                done = True

    def download_is_different(self, existing, new):
        # HTML pages many contain ASP.Net crap (__VIEWSTATE and
        # __EVENTVALIDATION) that differ from request to request. Only
        # compare div#mainContent
        existing_soup = BeautifulSoup(util.readfile(existing, encoding=self.source_encoding))
        new_soup = BeautifulSoup(util.readfile(new, encoding=self.source_encoding))
        return (existing_soup.find("div", id="mainContent") !=
                new_soup.find("div", id="mainContent"))

    def parse_metadata_from_soup(self, soup, doc):
        desc = Describer(doc.meta, doc.uri)
        # 1. static same-for-all metadata
        desc.rdftype(self.rdf_type)
        desc.value(self.ns['prov'].wasGeneratedBy, self.qualified_class_name())
        desc.rel(self.ns['dct'].publisher, self.lookup_resource("Justitiekanslern"))
        # 2. document-level metadata
        desc.value(self.ns['dct'].title,
                   soup.find("h1", "besluttitle").get_text(), lang="sv")
        datestr = soup.find("span", class_="label",
                            text="Beslutsdatum").find_next_sibling("span").get_text()
        desc.value(self.ns['rpubl'].beslutsdatum,
                   datetime.strptime(datestr, '%Y-%m-%d'))
        desc.value(self.ns['rpubl'].diarienummer,
                   soup.find("span", class_="label",
                             text="Diarienummer").find_next_sibling("span").get_text())
        desc.rel(self.ns['owl'].sameAs,
                 legaluri.construct({'type': LegalRef.MYNDIGHETSBESLUT,
                                     'myndighet': 'jk',
                                     'dnr': doc.basefile}))
                
    def parse_document_from_soup(self, soup, doc):

        # 3: Process the actual text of the document

        main = soup.find("div", id="mainContent")
        # remove crap
        main.find("div", id="breadcrumbcontainer").decompose()
        main.find("h1",  class_="besluttitle").decompose()
        main.find("div", class_="beslutmetadatacontainer").decompose()
        # structurize
        parser = self.make_parser()
        # list all tags (x.name) that aren't empty (x.get_text().strip())
        body = parser.parse(main.find_all(lambda x: x.name and x.get_text().strip()))

        # linkify
        self.ref_parser = LegalRef(LegalRef.LAGRUM,
                               LegalRef.KORTLAGRUM,
                               LegalRef.RATTSFALL,
                               LegalRef.FORARBETEN)
        citparser = SwedishCitationParser(self.ref_parser)
        doc.body = citparser.parse_recursive(body)

    @staticmethod
    def make_parser():
        def is_section(parser):
            return parser.reader.peek().name == "h1"
        def is_subsection(parser):
            return parser.reader.peek().name == "h2"
        def is_subsubsection(parser):
            return parser.reader.peek().name == "h3"
        def is_paragraph(parser):
            return True

        @newstate('body')
        def make_body(parser):
            return parser.make_children(Body())

        @newstate('section')
        def make_section(parser):
            s = Sektion(title=parser.reader.next().get_text())
            return parser.make_children(s)

        @newstate('subsection')
        def make_subsection(parser):
            s = Sektion(title=parser.reader.next().get_text())
            return parser.make_children(s)

        @newstate('subsubsection')
        def make_subsubsection(parser):
            s = Sektion(title=parser.reader.next().get_text())
            return parser.make_children(s)

        def make_paragraph(parser):
            # FIXME: this strips out formatting tags
            return Stycke([parser.reader.next().get_text()])
            
        p = FSMParser()
        p.set_recognizers(is_section,
                          is_subsection,
                          is_subsubsection,
                          is_paragraph)
        p.set_transitions({
            ("body", is_section): (make_section, "section"),
            ("section", is_section): (False, None),
            ("section", is_subsection): (make_subsection, "subsection"),
            ("subsection", is_section): (False, None),
            ("subsection", is_subsection): (False, None),
            ("subsection", is_subsubsection): (make_subsection, "subsubsection"),
            ("subsubsection", is_section): (False, None),
            ("subsubsection", is_subsection): (False, None),
            ("subsubsection", is_subsubsection): (False, None),
            (("body", "section", "subsection", "subsubsection"), is_paragraph): (make_paragraph, None)
        })
        p.initial_state = "body"
        p.initial_constructor = make_body
        p.debug = os.environ.get('FERENDA_FSMDEBUG', False)
        return p
             
