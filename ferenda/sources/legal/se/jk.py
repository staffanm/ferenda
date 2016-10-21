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
from ferenda import FSMParser, DocumentEntry
from ferenda import util, errors
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

    start_url = "http://www.jk.se/beslut-och-yttranden/"
    document_url_regex = "http://www.jk.se/Beslut/(?P<kategori>[\w\-]+)/(?P<basefile>\d+\-\d+\-\d+).aspx"
    rdf_type = RPUBL.VagledandeMyndighetsavgorande
    documentstore_class = JKStore
    urispace_segment = "avg/jk"
    download_iterlinks = False
    xslt_template = "xsl/avg.xsl"
    sparql_annotations = "sparql/avg-annotations.rq"
    sparql_expect_results = False


    @recordlastdownload
    def download(self, basefile=None, reporter=None):
        if basefile:
            from pudb import set_trace; set_trace()
            resp = self.session.post(self.start_url, data={'diarienummer': basefile})
            soup = BeautifulSoup(resp.text, "lxml")
            link = soup.find("div", "ruling-results").find("a", href=re.compile("/beslut-och-yttranden/"))
            if not link:
                raise errors.DownloadFileNotFoundError(basefile)
            url = urljoin(self.start_url, link["href"])
            return self.download_single(basefile, url)
        else:
            return super(JK, self).download(basefile, reporter)
        
    def download_get_first_page(self):
        data = {'page': '9999'}   # this'll yield a single page with
                                  # every descision ever. This is
                                  # inefficient, but their webdevs
                                  # have broken pagination, so...
        self.log.debug("Starting at %s" % self.start_url)
        resp = requests.post(self.start_url, data=data)
        return resp
                
    @downloadmax
    def download_get_basefiles(self, source):
        document_url_regex = re.compile("/(?P<basefile>\d+\-\d+\-\d+)/$")
        soup = BeautifulSoup(source, "lxml")
        for link in soup.find_all("a", href=document_url_regex):
            basefile = document_url_regex.search(link["href"]).group("basefile")
            yield basefile, urljoin(self.start_url, link["href"])

    def source_url(self, basefile):
        # this source does not have any predictable URLs, so we try to
        # find if we made a note on the URL when we ran download()
        entry = DocumentEntry(self.store.documententry_path(basefile))
        return entry.orig_url

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
        content = soup.find("div", "content")
        title = content.find("h2").get_text()
        metadata = content.find("div", "date").get_text()
        # eg "Diarienr: 4008-16-31 / Beslutsdatum: 12 jul 2016"
        diarienummer = metadata.split()[1]
        beslutsdatum = metadata.rsplit(": ", 1)[1]
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
        main = soup.find("div", "content")
        main.find("div", "actions").extract()
        main.find("div", "date").extract()
        main.find("h2").extract()
        return main

    def tokenize(self, main):
        # list all tags (x.name) that aren't empty (x.get_text().strip())
        return main.find_all(lambda x: x.name and x.get_text().strip())


    def get_parser(self, basefile, sanitized_body):
        # a typical decision structure:

        # [h1] Justitiekanslerns beslut
        #    ... text ...
        #    [h2] Ärendet (h3)
        #        [h3] Bakgrund (p/em)
        #        ... text ...
        #        [h3] Anspråket
        #        ... text ...
        #        [h3 class="reglering"] Rättslig reglering m.m. (p/strong)
        #    [h2] Justitiekanslerns bedömning
        #        [h3] Skadestånd
        #        [h3] Tillsyn
        def is_section(parser):
            return parser.reader.peek().name == "h3"

        def is_subsection(parser):
            chunk = parser.reader.peek()
            return chunk.name == "p" and list(chunk.children)[0].name == "em"

        def is_special_subsection(parser):
            chunk = parser.reader.peek()
            return chunk.name == "p" and list(chunk.children)[0].name == "strong"

        def is_subsubsection(parser):
            chunk = parser.reader.peek()
            return chunk.name == "p" and list(chunk.children)[0].name == "u"

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

        @newstate('special_subsection')
        def make_special_subsection(parser):
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
            ("section", is_special_subsection): (make_special_subsection, "special_subsection"),
            ("subsection", is_section): (False, None),
            ("subsection", is_subsection): (False, None),
            ("subsection", is_special_subsection): (False, None),
            ("subsection", is_subsubsection): (make_subsection, "subsubsection"),
            ("special_subsection", is_section): (False, None),
            ("special_subsection", is_subsection): (False, None),
            ("special_subsection", is_subsubsection): (make_subsubsection, "subsubsection"),
            ("subsubsection", is_section): (False, None),
            ("subsubsection", is_special_subsection): (False, None),
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
