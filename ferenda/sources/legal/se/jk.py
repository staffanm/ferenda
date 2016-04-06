# -*- coding: utf-8 -*-
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
from builtins import *

import re
import os
from datetime import datetime, timedelta
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from rdflib.namespace import SKOS, DCTERMS

from . import SwedishLegalSource, SwedishLegalStore, RPUBL
from .elements import *
from .swedishlegalsource import AnonStycke
from ferenda import FSMParser
from ferenda import util
from ferenda.decorators import downloadmax, recordlastdownload, newstate
from ferenda.elements import Body


class JKStore(SwedishLegalStore):
    def basefile_to_pathfrag(self, basefile):
        # store data using years as top-level dir by extracting the
        # year from the middle of the diarienummer:
        # "3541-97-21" => "1997/3541-97-21"
        # "3497-06-40" => "2006/3497-06-40"
        if "-" not in basefile:
            return super(JKStore, self).basefile_to_pathfrag(basefile)
        no, year, dtype = basefile.split("-")
        if int(year) > 50:  # arbitrary cutoff
            year = "19" + year
        else:
            year = "20" + year
        return "%s/%s" % (year, basefile)

    def pathfrag_to_basefile(self, pathfrag):
        # "1997/3541-97-21" => "3541-97-21"
        # "2006/3497-06-40" => "3497-06-40"
        year, basefile = pathfrag.split(os.sep)
        return basefile


class JK(SwedishLegalSource):
    alias = "jk"

    start_url = "http://www.jk.se/Beslut.aspx?query=&type=all&dateFrom=%(date)s&dateTo=2100-01-01&dnr="
    document_url_regex = "http://www.jk.se/Beslut/(?P<kategori>[\w\-]+)/(?P<basefile>\d+\-\d+\-\d+).aspx"
    rdf_type = RPUBL.VagledandeMyndighetsavgorande
    documentstore_class = JKStore
    urispace_segment = "avg/jk"
    
    @recordlastdownload
    def download(self, basefile=None):
        self.session = requests.session()
        if ('lastdownload' in self.config and
                self.config.lastdownload and
                not self.config.refresh):

            # allow for 30 day window between decision date and publishing
            startdate = self.config.lastdownload - timedelta(days=30)
            start_url = self.start_url % {'date':
                                          datetime.strftime(startdate, "%Y-%m-%d")}
        else:
            start_url = self.start_url % {'date': "1998-01-01"}

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
            soup = BeautifulSoup(requests.get(url).text, "lxml")
            for link in soup.find_all("a", href=document_url_regex):
                basefile = document_url_regex.search(link["href"]).group("basefile")
                yield basefile, urljoin(url, link["href"])

            next = soup.find("img", src="/common/images/navigation-pil-grey.png")
            if next and next.find_parent("a") and next.find_parent("a").get("href"):
                url = urljoin(url, next.find_parent("a")["href"])
                pagecount += 1
            else:
                done = True


# Removed for now -- JK decisions ought to be immutable?
#                
#    def download_is_different(self, existing, new):
#        # HTML pages many contain ASP.Net crap (__VIEWSTATE and
#        # __EVENTVALIDATION) that differ from request to request. Only
#        # compare div#mainContent
#        existing_soup = BeautifulSoup(
#            util.readfile(
#                existing,
#                encoding=self.source_encoding), "lxml")
#        new_soup = BeautifulSoup(util.readfile(new, encoding=self.source_encoding), "lxml")
#        return (existing_soup.find("div", id="mainContent") !=
#                new_soup.find("div", id="mainContent"))


    def metadata_from_basefile(self, basefile):
        attribs = super(JK, self).metadata_from_basefile(basefile)
        attribs["rpubl:diarienummer"] = basefile
        attribs["dcterms:publisher"] = self.lookup_resource(
                    'JK', SKOS.altLabel)
        return attribs

    def extract_head(self, fp, basefile):
        return BeautifulSoup(fp.read(), "lxml")

    def infer_identifier(self, basefile):
        return "JK %s" % basefile

    def extract_metadata(self, soup, basefile):
        title = soup.find("h1", "besluttitle").get_text()
        beslutsdatum = soup.find("span", class_="label",
                                 text="Beslutsdatum").find_next_sibling("span").get_text()
        diarienummer = soup.find("span", class_="label",
                                 text="Diarienummer").find_next_sibling("span").get_text()
        a = self.metadata_from_basefile(basefile)
        a.update({"dcterms:title": title,
                  "dcterms:publisher": self.lookup_resource("JK", SKOS.altLabel),
                  "rpubl:beslutsdatum": beslutsdatum,
                  "dcterms:issued": beslutsdatum,
                  "rpubl:diarienummer": diarienummer,
                  "dcterms:identifier": self.infer_identifier(diarienummer)})
        return a
    
    def extract_body(self, fp, basefile):
        # NB: extract_head already did this (so the fp will have been
        # read to the end -- need to seek(0)
        fp.seek(0)
        soup = BeautifulSoup(fp.read(), "lxml")
        main = soup.find("div", id="mainContent")
        # remove crap -- FIXME: after the first .decompose(), further
        # calls to find() seem to fail with beautifulsoup4 4.4.0?
        # 
        # main.find("div", id="breadcrumbcontainer").decompose()
        # main.find("h1", "besluttitle").decompose()
        # main.find("div", "beslutmetadatacontainer").decompose()
        return main

    def tokenize(self, main):
        # list all tags (x.name) that aren't empty (x.get_text().strip())
        return main.find_all(lambda x: x.name and x.get_text().strip())


    def get_parser(self, basefile, sanitized_body):
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
            s = AnonSektion(title=parser.reader.next().get_text())
            return parser.make_children(s)

        @newstate('subsection')
        def make_subsection(parser):
            s = AnonSektion(title=parser.reader.next().get_text())
            return parser.make_children(s)

        @newstate('subsubsection')
        def make_subsubsection(parser):
            s = AnonSektion(title=parser.reader.next().get_text())
            return parser.make_children(s)

        def make_paragraph(parser):
            # FIXME: this strips out formatting tags NB: Now this is a
            # SFS stycke that has fragment_label, id/uri and other
            # crap. Let's see if it still works!
            return AnonStycke([parser.reader.next().get_text()])

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
        return p.parse

    _default_creator = "Justitiekanslern"

    def _relate_fulltext_value_rootlabel(self, desc):
        return desc.getvalue(DCTERMS.identifier)

    def tabs(self):
        if self.config.tabs:
            return [("JK", self.dataset_uri())]
        else:
            return []
