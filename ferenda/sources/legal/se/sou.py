# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import re
import os
from datetime import datetime

from rdflib.namespace import SKOS
from bs4 import BeautifulSoup

from ferenda import PDFAnalyzer, CompositeRepository, DocumentEntry
from . import Regeringen, SwedishLegalSource, RPUBL


# are there other sources? www.sou.gov.se directs here,
# anyway. Possibly
# https://www.riksdagen.se/Webbnav/index.aspx?nid=3282, but it's
# unsure whether they have any more information, or they just import
# from regeringen.se (the data quality suggests some sort of auto
# import). Some initial comparisons cannot find data that riksdagen.se
# has that regeringen.se doesn't

class SOUAnalyzer(PDFAnalyzer):
    # SOU running headers can contain quite a bit of text, 2 %
    header_significance_threshold = 0.02
    # footers less so (but more than the default .2%), 1 %
    footer_significance_threshold = 0.01
    # the first two pages (3+ if the actual cover is included) are
    # atypical. A proper way of determining this would be to scan for
    # the first page stating "Till statsråded XX" using a title-ish
    # font.
    frontmatter = 2

    # don't count anything in the frontmatter - these margins are all off
    def count_vertical_textbox(self, pagenumber, textbox, counters):
        if pagenumber >= self.frontmatter:
            super(SOUAnalyzer, self).count_vertical_textbox(pagenumber, textbox, counters)

    def count_horizontal_textbox(self, pagenumber, textbox, counters):
        if pagenumber >= self.frontmatter:
            super(SOUAnalyzer, self).count_horizontal_textbox(pagenumber, textbox, counters)


class SOURegeringen(Regeringen):
    alias = "souregeringen"
    re_basefile_strict = re.compile(r'SOU (\d{4}:\d+)')
    re_basefile_lax = re.compile(r'(?:SOU|) ?(\d{4}:\d+)', re.IGNORECASE)
    rdf_type = RPUBL.Utredningsbetankande
    document_type = Regeringen.SOU
    sparql_annotations = None  # don't even bother creating an annotation file

    def canonical_uri(self, basefile):
        year, ordinal = basefile.split(":")
        attrib = {'rpubl:arsutgava': year,
                  'rpubl:lopnummer': ordinal,
                  'rpubl:utrSerie': self.lookup_resource("SOU", SKOS.altLabel),
                  'rdf:type': self.rdf_type}
        resource = self.attributes_to_resource(attrib)
        return self.minter.space.coin_uri(resource) 


class SOUKB(SwedishLegalSource):
    alias = "soukb"
    storage_policy = "dir"
    downloaded_suffix = ".pdf"
    basefile_regex = "(?P<basefile>\d{4}:\d+)"
    start_url = "http://regina.kb.se/sou/"

    def download_single(self, basefile, url):
        resp = self.session.get(url)
        soup = BeautifulSoup(resp.text)
        pdfurl = soup.find("a", href=re.compile(".*\.pdf$"))
        thumburl = soup.find("img", "tumnagel")
        librisid = url.rsplit("-")[0]
        rdfurl = "http://data.libris.kb.se/open/bib/%s.rdf" % librisid
        filename = self.store.downloaded_path(basefile)
        created = not os.path.exists(filename)
        if self.download_if_needed(pdfurl, basefile):
            if created:
                self.log.info("%s: downloaded from %s" % (basefile, pdfurl))
            else:
                self.log.info(
                    "%s: downloaded new version from %s" % (basefile, pdfurl))
            updated = True
            self.download_if_needed(rdfurl, basefile,
                                    filename=self.store.downloaded_path(
                                        basefile, attachment="metadata.rdf"))
            self.download_if_needed(thumburl, basefile,
                                    filename=self.store.downloaded_path(
                                        basefile, attachment="thumb.jpg"))
        else:
            self.log.debug("%s: exists and is unchanged" % basefile)

        entry = DocumentEntry(self.store.documententry_path(basefile))
        now = datetime.now()
        entry.orig_url = url  # or pdfurl?
        if created:
            entry.orig_created = now
        if updated:
            entry.orig_updated = now
        entry.orig_checked = now
        entry.save()
        return updated


class SOU(CompositeRepository):
    alias = "sou"
    subrepos = (SOURegeringen, SOUKB)
    
