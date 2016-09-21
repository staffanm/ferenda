# -*- coding: utf-8 -*-
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
from builtins import *


import re
import os
import logging
import unicodedata
from datetime import datetime
from urllib.parse import urljoin

from rdflib import URIRef, Literal, Graph, Namespace
from rdflib.namespace import SKOS, DC, RDF, XSD
BIBO = Namespace("http://purl.org/ontology/bibo/")
from bs4 import BeautifulSoup
import lxml.html

from ferenda import (PDFAnalyzer, CompositeRepository, DocumentEntry,
                     PDFDocumentRepository, CompositeStore)
from ferenda import util, decorators
from ferenda.pdfreader import StreamingPDFReader
from . import Regeringen, SwedishLegalSource, SwedishLegalStore, Offtryck, RPUBL


class SOUAnalyzer(PDFAnalyzer):
    # SOU running headers can contain quite a bit of text, 2 %
    header_significance_threshold = 0.02
    # footers less so (but more than the default .2%), 1 %
    footer_significance_threshold = 0.01

    # h1 / h2's can be a bit rare though, particularly in older
    # material which only use different size for h1:s (0.07% is
    # enough)
    style_significance_threshold = 0.0007
    

    def documents(self):
        def titleish(page):
            for textelement in page:
                if textelement.font.size >= 18: # Ds 2009:55 uses size 18. The normal is 26.
                    return textelement
        documents = []
        currentdoc = 'frontmatter'
        for pageidx, page in enumerate(self.pdf):
            # Sanity check: 
            if pageidx > 8 and currentdoc == 'frontmatter':
                logging.getLogger("pdfanalyze").warn("missed the transition from frontmatter to main")
                # act as there never was any frontmatter -- all pages
                # are considered part of the main content.
                currentdoc = "main"
                documents[0][-1] = "main"
            pgtitle = titleish(page)
            if pgtitle is not None:
                pgtitle = str(pgtitle).strip()
                if re.match("(Till [sS]|S)tatsrådet ", pgtitle):
                    currentdoc = "main"
                elif pgtitle in ("Innehåll", "Innehållsförteckning"):
                    currentdoc = "main"
            # update the current document segment tuple or start a new one
            if documents and documents[-1][2] == currentdoc:
                documents[-1][1] += 1
            else:
                documents.append([pageidx, 1, currentdoc])
        return documents


class SOURegeringen(Regeringen):
    alias = "souregeringen"
    re_basefile_strict = re.compile(r'SOU (\d{4}:\d+)')
    re_basefile_lax = re.compile(r'(?:SOU|) ?(\d{4}:\d+)', re.IGNORECASE)
    re_urlbasefile_strict = re.compile("statens-offentliga-utredningar/\d+/\d+/[a-z]*\.?-?(\d{4})(\d+)-?/$")
    re_urlbasefile_lax = re.compile("statens-offentliga-utredningar/\d+/\d+/.*?(\d{4})_?(\d+)")
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


class SOUKB(Offtryck, PDFDocumentRepository):
    alias = "soukb"
    storage_policy = "dir"
    downloaded_suffix = ".pdf"
    basefile_regex = "(?P<basefile>\d{4}:\d+)"
    start_url = "http://regina.kb.se/sou/"
    download_reverseorder = True
    rdf_type = RPUBL.Utredningsbetankande
    urispace_segment = "utr/sou"
    # A bit nonsensical, but required for SwedishLegalSource.get_parser
    document_type = SOU = True
    PROPOSITION = DS = KOMMITTEDIREKTIV = False

    
    def download(self, basefile=None):
        if basefile:
            resp = self.session.get(self.start_url)
            tree = lxml.html.document_fromstring(resp.text)
            tree.make_links_absolute(self.start_url, resolve_base_href=True)
            source = tree.iterlinks()
            # 1. look through download_get_basefiles for basefile
            for (b, url) in self.download_get_basefiles(source):
                if b == basefile:
                    return self.download_single(basefile, url)
            else:
                self.log.error("%s: Couldn't find requested basefile" % basefile)
        else:
             return super(SOUKB, self).download()
         

    @decorators.downloadmax
    def download_get_basefiles(self, source):
        # modified copy of DocumentRepository.download_get_basefiles
        # that also yields the link title, based on the assumption
        # that this is valuable to download_single. 
        yielded = set()
        if self.download_reverseorder:
            source = reversed(list(source))
        for (element, attribute, link, pos) in source:
            # Also makes sure the link is not external (SOU 1997:119
            # links to external site regeringen.se for some reason...)
            if "kb.se/" not in link:
                continue
            basefile = None
            # Two step process: First examine link text to see if
            # basefile_regex match. If not, examine link url to see
            # if document_url_regex
            if (self.basefile_regex and
                element.text and
                    re.search(self.basefile_regex, element.text)):
                m = re.search(self.basefile_regex, element.text)
                basefile = m.group("basefile")
            if basefile and (basefile, link) not in yielded:
                yielded.add((basefile, link))
                yield (basefile, (link, element.tail.strip()))

    def download_single(self, basefile, url):
        # url is really a 2-tuple
        url, title = url
        resp = self.session.get(url)
        soup = BeautifulSoup(resp.text, "lxml")
        pdflink = soup.find("a", href=re.compile(".*\.pdf$"))
        pdfurl = pdflink.get("href")
        thumburl = urljoin(url, soup.find("img", "tumnagel").get("src"))
        librisid = url.rsplit("-")[1]
        rdfurl = "http://data.libris.kb.se/open/bib/%s.rdf" % librisid
        filename = self.store.downloaded_path(basefile)
        created = not os.path.exists(filename)
        if self.download_if_needed(pdfurl, basefile) or self.config.refresh:
            if created:
                self.log.info("%s: downloaded from %s" % (basefile, pdfurl))
            else:
                self.log.info(
                    "%s: downloaded new version from %s" % (basefile, pdfurl))
            updated = True
            try:
                # it appears that certain URLs (like curl
                # http://data.libris.kb.se/open/bib/8351225.rdf)
                # sometimes return an empty response. We should check
                # and warn for this (and infer a minimal RDF by
                # hand from what we can, eg dc:title from the link
                # text)
                rdffilename = self.store.downloaded_path(basefile, attachment="metadata.rdf")
                self.download_if_needed(rdfurl, basefile,
                                        filename=rdffilename)
                if os.path.getsize(rdffilename) == 0:
                    self.log.warning("%s: %s returned 0 response, infer RDF" %
                                     (basefile, rdfurl))
                    base = URIRef("http://libris.kb.se/resource/bib/%s" %
                                  librisid)
                    fakegraph = Graph()
                    fakegraph.bind("dc", str(DC))
                    fakegraph.add((base, DC.title, Literal(title, lang="sv")))
                    year = basefile.split(":")[0] # Libris uses str type
                    fakegraph.add((base, DC.date, Literal(year)))
                    with open(rdffilename, "wb") as fp:
                        fakegraph.serialize(fp, format="pretty-xml")
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
        metadata = util.readfile(self.store.downloaded_path(
            basefile, attachment="metadata.rdf"))
        # For some reason these RDF files might use canonical
        # decomposition form (NFD) which is less optimal. Fix this.
        metadata = unicodedata.normalize("NFC", metadata)
        sourcegraph = Graph().parse(data=metadata)
        rooturi = sourcegraph.value(predicate=RDF.type, object=BIBO.Book)
        title = sourcegraph.value(subject=rooturi, predicate=DC.title)
        issued = sourcegraph.value(subject=rooturi, predicate=DC.date)
        if isinstance(issued, str):
            assert len(issued) == 4, "expected issued date as single 4-digit year, got %s" % issued
            issued = Literal(util.gYear(int(issued)), datatype=XSD.gYear)
        attribs = self.metadata_from_basefile(basefile)
        attribs["dcterms:title"] = title
        attribs["dcterms:issued"] = issued
        return attribs

    def sanitize_metadata(self, props, doc):
        if 'dcterms:title' in props and " : betänkande" in props['dcterms:title']:
            props['dcterms:title'] = props['dcterms:title'].rsplit(" : ")[0]
        return props
            

    def extract_body(self, fp, basefile):
        reader = StreamingPDFReader()
        reader.read(fp)
        return reader
        
    def sanitize_body(self, rawbody):
        sanitized = super(SOUKB, self).sanitize_body(rawbody)
        # Offtryck.sanitize_body will set self.analyzer
        sanitized.analyzer.scanned_source = True  # everything from KB
                                                  # is scanned, even
                                                  # though the PDF
                                                  # includes OCR
                                                  # information, so
                                                  # the real source is
                                                  # not a hOCR file
        return sanitized

#    def get_parser(self, basefile, sanitized):
#        p = offtryck_parser(basefile, preset="dir")
#        p.current_identifier = "SOU %s" % basefile
#        return p.parse
#
#    def tokenize(self, pdfreader):
#        # FIXME: We should probably build a better tokenizer
#        return pdfreader.textboxes(offtryck_gluefunc)

    def create_external_resources(self, doc):
        pass

# inherit list_basefiles_for from CompositeStore, basefile_to_pathfrag
# from SwedishLegalStore)
class SOUStore(CompositeStore, SwedishLegalStore):
    pass

    
class SOU(CompositeRepository):
    alias = "sou"
    rdf_type = RPUBL.Utredningsbetankande
    subrepos = (SOURegeringen, SOUKB)
    urispace_segment = "utr/sou"
    documentstore_class = SOUStore
    xslt_template = "xsl/forarbete.xsl"

    # NB: The same logic as in
    # ferenda.sources.legal.se.{Regeringen,Riksdagen}.metadata_from_basefile
    def metadata_from_basefile(self, basefile):
        a = super(SOU, self).metadata_from_basefile(basefile)
        a["rpubl:arsutgava"], a["rpubl:lopnummer"] = basefile.split(":", 1)
        a["rpubl:utrSerie"] = self.lookup_resource("SOU", SKOS.altLabel)
        return a

