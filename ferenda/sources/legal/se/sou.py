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

import requests.exceptions
from rdflib import URIRef, Literal, Graph, Namespace
from rdflib.namespace import SKOS, DC, RDF, XSD, DCTERMS
BIBO = Namespace("http://purl.org/ontology/bibo/")
from bs4 import BeautifulSoup
import lxml.html
from cached_property import cached_property

from ferenda import (PDFAnalyzer, CompositeRepository, DocumentEntry,
                     PDFDocumentRepository, CompositeStore, Facet, DocumentStore)
from ferenda import util, decorators, errors
from ferenda.pdfreader import StreamingPDFReader
from . import Regeringen, SwedishLegalSource, FixedLayoutSource, SwedishLegalStore, Offtryck, RPUBL
from .swedishlegalsource import lazyread

def sou_sanitize_identifier(identifier):
    if not identifier:
        return identifier # allow infer_identifier to do it's magic later
    if not re.match("SOU (19|20)\d{2}:[1-9]\d{0,2}$", identifier):
        raise ValueError("Irregular identifier %s (after mangling)" %  identifier)
    return Literal(identifier)

class SOUAnalyzer(PDFAnalyzer):
    # SOU running headers can contain quite a bit of text, 3% (60 chars for avg page of 2000)
    header_significance_threshold = 0.03
    # footers less so (but more than the default .2%), 1 %
    footer_significance_threshold = 0.01

    # h1 / h2's can be a bit rare though, particularly in older
    # material which only use different size for h1:s (0.07% is
    # enough)
    style_significance_threshold = 0.0007

    gluefunc = None

    def guess_pagenumber(self, page, probable_pagenumber=1):
        if self.scanned_source:
            # KB scans have predictable page numbering -- the first
            # three pdf pages are fake cover, real cover and inlay --
            # the logical page 1 starts at physical page 4. Assume no
            # breaks in pagination, since the actual pagenumber is
            # rarely (if ever) included in the actual OCR information
            # (!)
            if probable_pagenumber == 4 and not hasattr(self, 'paginate_cover_accounted'):
                self.paginate_cover_accounted = True
                return 1
            else:
                return None
        else:
            return super(SOUAnalyzer, self).guess_pagenumber(page, probable_pagenumber)
            

    @cached_property
    def documents(self):
        def titleish(pageidx):
            # return the largest text element found on the page (first
            # one in case of a tie) -- that's probably the title on
            # the page
            iterator = self.pdf.textboxes(self.gluefunc, startpage=pageidx, pagecount=1) if self.gluefunc else self.pdf[pageidx]
            candidate = None
            for te in iterator:
                if candidate is None or str(te)[0].isupper() and te.font.size > candidate.font.size:
                    candidate = te
            return candidate
        documents = []
        currentdoc = 'frontmatter'
        prev_pagesrc = None
        pageidx_offset = 0
        for pageidx, page in enumerate(self.pdf):
            # FIXME: Generalize this way of detecting a multi-volume
            # document (as opposed to a single document split into
            # multiple PDF files).
            if page.src != prev_pagesrc and 'del-2' in page.src:
                if currentdoc == 'endregister' and len(page.as_plaintext()) < 1000:
                    # this is probably a single document split into two
                    currentdoc = 'main' # maybe 
                else:
                    # this is probably a multi-volume document 
                    currentdoc = 'frontmatter'
                pageidx_offset = pageidx
            # Sanity check: 
            if pageidx - pageidx_offset > 8 and currentdoc == 'frontmatter':
                logging.getLogger("pdfanalyze").warning("missed the transition from frontmatter to main")
                # act as there never was any frontmatter -- all pages
                # are considered part of the main content.
                currentdoc = "main"
                documents[0][-1] = "main"
            pgtitle = titleish(pageidx)
            if pgtitle is not None:
                pgtitle = str(pgtitle).strip()
                if re.match("(Till [sS]|S)tatsrådet ", pgtitle):
                    currentdoc = "main"
                elif pgtitle in ("Innehåll", "Innehållsförteckning", "Innehåll del 2"):
                    currentdoc = "main"
                elif re.match("Statens offentliga utredningar \d+", str(pgtitle).strip()):
                    currentdoc = 'endregister'
            styles = self.count_styles(pageidx, 1)
            # find the most dominant style on the page. If it uses the
            # EU font (even if it's the second most dominant), it's a
            # separate section.
            if styles and [s for s in self.count_styles(pageidx, 1).most_common(2) if s[0][0].startswith("EUAlbertina")]:
                currentdoc = 'eudok'
            elif currentdoc == "eudok":
                currentdoc == "main" ## CONTINUE
            
            # update the current document segment tuple or start a new one
            if documents and documents[-1][2] == currentdoc:
                documents[-1][1] += 1
            else:
                documents.append([pageidx, 1, currentdoc])
            prev_pagesrc = page.src
        return documents


class SOURegeringen(Regeringen):
    alias = "souregeringen"
    re_basefile_strict = re.compile(r'SOU (\d{4}:\d+)')
    re_basefile_lax = re.compile(r'(?:SOU|) ?(\d{4}:\d+)', re.IGNORECASE)
    re_urlbasefile_strict = re.compile("statens-offentliga-utredningar/\d+/\d+/[a-z]*\.?-?(\d{4})(\d+)-?/$")
    re_urlbasefile_lax = re.compile("statens-offentliga-utredningar/\d+/\d+/.*?(\d{4})_?(\d+)")
    rdf_type = RPUBL.Utredningsbetankande
    document_type = Regeringen.SOU
    def canonical_uri(self, basefile, version=None):
        year, ordinal = basefile.split(":")
        attrib = {'rpubl:arsutgava': year,
                  'rpubl:lopnummer': ordinal,
                  'rpubl:utrSerie': self.lookup_resource("SOU", SKOS.altLabel),
                  'rdf:type': self.rdf_type}
        resource = self.attributes_to_resource(attrib)
        return self.minter.space.coin_uri(resource) 

    def sanitize_identifier(self, identifier):
        return sou_sanitize_identifier(identifier)

class SOUKBStore(SwedishLegalStore):
    downloaded_suffixes = [".pdf", ".rdf"]

class SOUKB(Offtryck, PDFDocumentRepository):
    alias = "soukb"
    storage_policy = "dir"
    downloaded_suffix = ".pdf"
    basefile_regex = "(?P<basefile>\d{4}:\d+)"
    start_url = "http://regina.kb.se/sou/"
    download_reverseorder = True
    rdf_type = RPUBL.Utredningsbetankande
    urispace_segment = "sou"
    # A bit nonsensical, but required for SwedishLegalSource.get_parser
    document_type = SOU = True
    PROPOSITION = DS = KOMMITTEDIREKTIV = False
    documentstore_class = SOUKBStore
    
    @classmethod
    def get_default_options(cls):
        opts = super(SOUKB, cls).get_default_options()
        opts['ocr'] = True
        return opts
    
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
                params = {'uri': link,
                          'title': element.tail.strip()}
                yielded.add((basefile, link))
                yield (basefile, params)

    def download_single(self, basefile, url):
        if self.get_parse_options(basefile) == "skip":
            raise errors.DocumentSkippedError("%s should not be downloaded according to options.py" % basefile)
        rdffilename = self.store.downloaded_path(basefile, attachment="index.rdf")
        if self.get_parse_options(basefile) == "metadataonly" and os.path.exists(rdffilename) and (not self.config.refresh):
            # it is kind of bad that we can even get here in these
            # cases (if a rdffile exists, and a empty index.pdf
            # exists, shouldn't download() skip that file? Right now
            # it ignores empty files and passes them to
            # download_single.
            return False
        
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
        updated = False
        
        # download rdf metadata before actual content
        try:
            # it appears that URLs like
            # http://data.libris.kb.se/open/bib/8351225.rdf now
            # returns empty responses. Until we find out the proper
            # RDF endpoint URLs, we should check and warn for this
            # (and infer a minimal RDF by hand from what we can, eg
            # dc:title from the link text)
            self.download_if_needed(rdfurl, basefile,
                                    filename=rdffilename,
                                    archive=False)
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
        except requests.exceptions.HTTPError as e:
            self.log.error("Failed to load attachment: %s" % e)
            raise

        if self.get_parse_options(basefile) == "metadataonly":
            self.log.debug("%s: Marked as 'metadataonly', not downloading actual PDF file" % basefile)
            with self.store.open_downloaded(basefile, "w") as fp:
                pass
        else:
            if self.download_if_needed(pdfurl, basefile) or self.config.refresh:
                if created:
                    self.log.info("%s: download OK from %s" % (basefile, pdfurl))
                else:
                    self.log.info(
                        "%s: download OK (new version) from %s" % (basefile, pdfurl))
                updated = True
                try:
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

    def source_url(self, basefile):
        # this source does not have any predictable URLs, so we try to
        # find if we made a note on the URL when we ran download()
        # FIXME: This code is repeated in jk.py and regeringen.py --
        # maybe we should let the default impl of source_url try this
        # strategy if eg self.remote_url is None?
        entry = DocumentEntry(self.store.documententry_path(basefile))
        return entry.orig_url

    def metadata_from_basefile(self, basefile):
        attrib = super(SOUKB, self).metadata_from_basefile(basefile) 
        year, ordinal = basefile.split(":")
        attrib["rpubl:arsutgava"] = year
        attrib["rpubl:lopnummer"] = ordinal
        attrib["rpubl:utrSerie"] = self.lookup_resource("SOU", SKOS.altLabel)
        return attrib

    @lazyread
    def downloaded_to_intermediate(self, basefile, attachment=None):
        intermediate_path = self.store.intermediate_path(basefile)
        intermediate_dir = os.path.dirname(intermediate_path)
        keep_xml = "bz2" if self.config.compress == "bz2" else True
        reader = StreamingPDFReader()
        kwargs = {'filename': self.store.downloaded_path(basefile, attachment=attachment),
                  'workdir': intermediate_dir,
                  'images': self.config.pdfimages,
                  'keep_xml': keep_xml}
        if self.config.ocr:
            kwargs['ocr_lang'] = 'swe'
        return reader.convert(**kwargs)

    def extract_head(self, fp, basefile):
        return None  # "rawhead" is never used
        
    def extract_metadata(self, rawhead, basefile):
        metadata = util.readfile(self.store.downloaded_path(
            basefile, attachment="index.rdf"))
        # For some reason these RDF files might use canonical
        # decomposition form (NFD) which is less optimal. Fix this.
        metadata = unicodedata.normalize("NFC", metadata)
        sourcegraph = Graph().parse(data=metadata)
        rooturi = sourcegraph.value(predicate=RDF.type, object=BIBO.Book)
        if rooturi is None:
            # then just try to identify the main uri and use that 
            subjects = set(sourcegraph.subjects())
            if len(subjects) == 1:
                rooturi = next(iter(subjects))
        title = sourcegraph.value(subject=rooturi, predicate=DC.title)
        issued = sourcegraph.value(subject=rooturi, predicate=DC.date)
        if isinstance(issued, str):
            # sometimes dc:date is weird like "1976[1974]" (SOU 1974:42)
            if len(issued) != 4:
                self.log.warning("expected issued date as single 4-digit year, got %s" % issued)
                # fall back on an approximation based on the basefile
                issued = basefile.split(":")[0]
            issued = Literal(util.gYear(int(issued)), datatype=XSD.gYear)
                
        attribs = self.metadata_from_basefile(basefile)
        attribs["dcterms:title"] = title
        if issued:
            attribs["dcterms:issued"] = issued
        return attribs

    def sanitize_metadata(self, props, doc):
        if props.get('dcterms:title') and " : betänkande" in props['dcterms:title']:
            props['dcterms:title'] = props['dcterms:title'].rsplit(" : ")[0]
        return props

    def sanitize_identifier(self, identifier):
        return sou_sanitize_identifier(identifier)
    
    def extract_body(self, fp, basefile):
        reader = StreamingPDFReader()
        parser = "ocr" if self.config.ocr else "xml"
        reader.read(fp, parser=parser)
        for page in reader:
            page.src = "index.pdf"  # FIXME: don't hardcode the filename
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

    def create_external_resources(self, doc):
        pass

# inherit list_basefiles_for from CompositeStore, basefile_to_pathfrag
# from SwedishLegalStore)
class SOUStore(CompositeStore, SwedishLegalStore):
    pass


    
class SOU(CompositeRepository, FixedLayoutSource):
    alias = "sou"
    rdf_type = RPUBL.Utredningsbetankande
    subrepos = (SOURegeringen, SOUKB)
    urispace_segment = "sou"
    urispace_segments = ["sou", "utr/sou"]
    documentstore_class = SOUStore
    xslt_template = "xsl/forarbete.xsl"
    sparql_annotations = "sparql/describe-with-subdocs.rq"
    sparql_expect_results = False

    # NB: The same logic as in
    # ferenda.sources.legal.se.{Regeringen,Riksdagen}.metadata_from_basefile
    def metadata_from_basefile(self, basefile):
        a = super(SOU, self).metadata_from_basefile(basefile)
        a["rpubl:arsutgava"], a["rpubl:lopnummer"] = basefile.split(":", 1)
        a["rpubl:utrSerie"] = self.lookup_resource("SOU", SKOS.altLabel)
        return a

    def facets(self):
        return super(SOU, self).facets() + [Facet(DCTERMS.title)]
