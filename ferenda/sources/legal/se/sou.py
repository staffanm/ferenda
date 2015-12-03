# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import re
import os
from datetime import datetime
from six.moves.urllib_parse import urljoin

import rdflib
from rdflib.namespace import SKOS, DCTERMS, DC, RDF, XSD
BIBO = rdflib.Namespace("http://purl.org/ontology/bibo/")
from bs4 import BeautifulSoup

from ferenda import PDFAnalyzer, CompositeRepository, DocumentEntry, PDFDocumentRepository
from ferenda import util
from ferenda.pdfreader import StreamingPDFReader
from . import Regeringen, SwedishLegalSource, RPUBL
from .swedishlegalsource import offtryck_gluefunc, offtryck_parser


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

    def canonical_uri(self, basefile):
        year, ordinal = basefile.split(":")
        attrib = {'rpubl:arsutgava': year,
                  'rpubl:lopnummer': ordinal,
                  'rpubl:utrSerie': self.lookup_resource("SOU", SKOS.altLabel),
                  'rdf:type': self.rdf_type}
        resource = self.attributes_to_resource(attrib)
        return self.minter.space.coin_uri(resource) 


class SOUKB(SwedishLegalSource, PDFDocumentRepository):
    alias = "soukb"
    storage_policy = "dir"
    downloaded_suffix = ".pdf"
    basefile_regex = "(?P<basefile>\d{4}:\d+)"
    start_url = "http://regina.kb.se/sou/"
    download_reverseorder = True
    rdf_type = RPUBL.Utredningsbetankande
    urispace_segment = "utr/sou"

    def download_single(self, basefile, url):
        resp = self.session.get(url)
        soup = BeautifulSoup(resp.text)
        pdfurl = soup.find("a", href=re.compile(".*\.pdf$")).get("href")
        
        thumburl = urljoin(url, soup.find("img", "tumnagel").get("src"))
        librisid = url.rsplit("-")[1]
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
            try:
                self.download_if_needed(rdfurl, basefile,
                                        filename=self.store.downloaded_path(
                        basefile, attachment="metadata.rdf"))
                self.download_if_needed(thumburl, basefile,
                                        filename=self.store.downloaded_path(
                        basefile, attachment="thumb.jpg"))
            except requests.exceptions.HTTPError as e:
                self.log.error("Failed to load attachment: %s" % e)
                raise
                
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

    def metadata_from_basefile(self, basefile):
        attrib = super(SOUKB, self).metadata_from_basefile(basefile) 
        year, ordinal = basefile.split(":")
        attrib["rpubl:arsutgava"] = year
        attrib["rpubl:lopnummer"] = ordinal
        attrib["rpubl:utrSerie"] = self.lookup_resource("SOU", SKOS.altLabel)
        return attrib

    def downloaded_to_intermediate(self, basefile):
        intermediate_path = self.store.intermediate_path(basefile)
        intermediate_dir = os.path.dirname(intermediate_path)
        keep_xml = "bz2" if self.config.compress == "bz2" else True
        reader = StreamingPDFReader()
        return reader.convert(filename=self.store.downloaded_path(basefile),
                              workdir=intermediate_dir,
                              images=self.config.pdfimages,
                              keep_xml=keep_xml)

    def extract_head(self, fp, basefile):
        return None  # "rawhead" is never used
        
    def extract_metadata(self, rawhead, basefile):
        sourcegraph = rdflib.Graph().parse(self.store.downloaded_path(
            basefile, attachment="metadata.rdf"))
        rooturi = sourcegraph.value(predicate=RDF.type, object=BIBO.Book)
        title = sourcegraph.value(subject=rooturi, predicate=DC.title)
        issued = sourcegraph.value(subject=rooturi, predicate=DC.date)
        if isinstance(issued, str):
            assert len(issued) == 4, "expected issued date as single 4-digit year, got %s" % issued
            issued = rdflib.Literal(util.gYear(int(issued)), datatype=XSD.gYear)
        attribs = self.metadata_from_basefile(basefile)
        attribs["dcterms:title"] = title
        attribs["dcterms:issued"] = issued
        return attribs

    def extract_body(self, fp, basefile):
        reader = StreamingPDFReader()
        reader.read(fp)
        return reader
        
    def get_parser(self, basefile, sanitized):
        p = offtryck_parser(basefile, preset="dir")
        p.current_identifier = "SOU %s" % basefile
        return p.parse

    def tokenize(self, pdfreader):
        # FIXME: We should probably build a better tokenizer
        return pdfreader.textboxes(offtryck_gluefunc)

    def create_external_resources(self, doc):
        pass

class SOU(CompositeRepository):
    alias = "sou"
    rdf_type = RPUBL.Utredningsbetankande
    subrepos = (SOURegeringen, SOUKB)


