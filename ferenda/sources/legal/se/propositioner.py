#!/usr/bin/env python
# -*- coding: iso-8859-1 -*-
import re
import os
from time import time
import codecs

from bs4 import BeautifulSoup
from rdflib import Graph, Literal, Namespace, URIRef, RDF, RDFS

from ferenda import util
from ferenda.elements import UnicodeElement, CompoundElement, \
    MapElement, IntElement, DateElement, PredicateType, \
    UnicodeSubject, Heading, Preformatted, Paragraph, Section, Link, ListItem, \
    serialize
from ferenda import DocumentRepository, CompositeRepository, PDFDocumentRepository, Describer, TextReader, PDFReader
from ferenda.errors import ParseError, DocumentRemovedError
from ferenda.decorators import managedparsing
from . import SwedishLegalSource, Regeringen, Riksdagen, RPUBL


class PropPolo(Regeringen):
    module_dir = "proppolo"
    re_basefile_strict = re.compile(r'Prop. (\d{4}/\d{2,4}:\d+)')
    re_basefile_lax = re.compile(
        r'(?:Prop\.?|) ?(\d{4}/\d{2,4}:\d+)', re.IGNORECASE)
    rdf_type = RPUBL.Proposition

    def __init__(self, options):
        super(PropPolo, self).__init__(options)
        self.document_type = self.PROPOSITION


class PropTrips(SwedishLegalSource, PDFDocumentRepository):
    module_dir = "proptrips"
    downloaded_suffix = ".html"
    rdf_type = RPUBL.Proposition

    base = "THWALLAPROP"
    # base = "PROPARKIV1011"
    # base = "PROPARKIV9495"
    start_url = "http://62.95.69.15/cgi-bin/thw?${HTML}=prop_lst&${OOHTML}=prop_dok&${SNHTML}=prop_err&${MAXPAGE}=26&${HILITE}=1&${TRIPSHOW}=format=THW&${SAVEHTML}=/prop/prop_form.html&${BASE}=%s" % base

    re_basefile_lax = re.compile(
        r'(?:Prop\.?|) ?(\d{4}/\d{2,4}:\d+)', re.IGNORECASE)
    storage_policy = "dir"

    @classmethod
    def basefile_from_path(cls, path):
        seg = path.split(os.sep)
        seg = seg[seg.index(cls.module_dir) + 2:-1]
        seg = [x.replace("-", "/") for x in seg]
        # print len(seg)
        assert 2 <= len(seg) <= 3, "list of segments is too long or too short"
        # print "path: %s, seg: %r, basefile: %s" % (path,seg,":".join(seg))
        return ":".join(seg)

    def download(self):
        refresh = self.get_moduleconfig('refresh', bool, False)
        self.log.info("Starting at %s" % self.start_url)
        resp = self.browser.open(self.start_url)
        done = False
        pagecnt = 1
        # ["PROPARKIV%s%s" % (str(x)[2:],str(x+1)[2:]) for x in
        #  range(1993,datetime.datetime.now().year+1)]
        while not done:
            self.log.info('Result page #%s' % pagecnt)
            basefile = None
            soup = BeautifulSoup(self.browser.response(),
                                 convertEntities=BeautifulSoup.HTML_ENTITIES)
            #for link in self.browser.links(text_regex=r'(\d{4}/\d{2,4}:\d+)'):
            for tr in soup.findAll("tr"):
                if ((not tr.find("a")) or
                        not self.re_basefile_lax.match(tr.find("a").text)):
                    continue
                # First, look at desc (third td):
                descnodes = [util.normalize_space(x) for x
                             in tr.findAll("td")[2]
                             if isinstance(x, str)]
                bilaga = None
                if len(descnodes) > 1:
                    if descnodes[1].startswith("Bilaga:"):
                        bilaga = util.normalize_space(
                            descnodes[0].split(",")[-1])

                desc = "\n".join(descnodes)

                # then, find basefile (second td)
                tds = tr.findAll("td")
                td = tds[1]
                basefile = td.a.text
                assert self.re_basefile_lax.match(basefile)

                basefile = self.sanitize_basefile(basefile)

                if bilaga:
                    basefile += "#%s" % bilaga

                url = urljoin(self.browser.geturl(), td.a['href'])
                self.download_single(basefile, refresh=refresh, url=url)

                # and, if present, extra files (in td 4+5)
                for td in tr.findAll("td")[3:]:
                    if td.a['href'].endswith('msword.application'):
                        # NOTE: We cannot be sure that this is
                        # actually a Word (CDF) file. For older files
                        # it might be a WordPerfect file (.wpd) or a
                        # RDF file, for newer it might be a .docx. We
                        # cannot be sure until we've downloaded it.
                        # So we quickly read the first 4 bytes
                        url = urljoin(self.browser.geturl(), td.a['href'])
                        sig = self.browser.open_novisit(url).read(4)
                        if sig == '\xffWPC':
                            doctype = ".wpd"
                        elif sig == '\xd0\xcf\x11\xe0':
                            doctype = ".doc"
                        elif sig == 'PK\x03\x04':
                            doctype = ".docx"
                        elif sig == '{\\rt':
                            doctype = ".rtf"
                        else:
                            self.log.error("%s: Attached file has signature %r -- don't know what type this is" % (basefile, sig))
                            continue
                    elif td.a['href'].endswith('pdf.application'):
                        doctype = ".pdf"
                    else:
                        self.log.warning("Unknown doc type %s" %
                                         td.a['href'].split("=")[-1])
                        doctype = None
                    filename = self.generic_path(
                        basefile, "downloaded", doctype)
                    url = urljoin(self.browser.geturl(), td.a['href'])

                    if refresh or not os.path.exists(filename):
                        self.log.info("   Also downloading %s as %s" %
                                      (basefile, doctype))
                        self.download_if_needed(url, filename)

            try:
                self.browser.follow_link(text='Fler poster')
                pagecnt += 1
            except LinkNotFoundError:
                self.log.info(
                    'No next page link found, this was the last page')
                done = True

    # Correct some invalid identifiers spotted in the wild:
    # 1999/20 -> 1999/2000
    # 2000/2001 -> 2000/01
    # 1999/98 -> 1999/2000
    def sanitize_basefile(self, basefile):
        (y1, y2, idx) = re.split("[:/]", basefile)
        assert len(
            y1) == 4, "Basefile %s is invalid beyond sanitization" % basefile
        if y1 == "1999" and y2 != "2000":
            sanitized = "1999/2000:" + idx
            self.log.warning("Basefile given as %s, correcting to %s" %
                             (basefile, sanitized))
        elif (y1 != "1999" and
              (len(y2) != 2 or  # eg "2000/001"
               int(y1[2:]) + 1 != int(y2))):  # eg "1999/98

            sanitized = "%s/%02d:%s" % (y1, int(y1[2:]) + 1, idx)
            self.log.warning("Basefile given as %s, correcting to %s" %
                             (basefile, sanitized))
        else:
            sanitized = basefile
        return sanitized

    # For parsing:
    # 1999/94 and 1994/95 has only plaintext
    # 1995/96 to 2006/07 has plaintext + doc
    # 2007/08 onwards has plaintext, doc and pdf
    @managedparsing
    def parse(self, doc):
        doc.uri = self.canonical_uri(doc.basefile)
        d = Describer(doc.meta, doc.uri)
        d.rdftype(self.rdf_type)
        d.value(self.ns['prov'].wasGeneratedBy, self.qualified_class_name())
        self.infer_triples(d, doc.basefile)

        # prefer PDF or Word files over the plaintext-containing HTML files
        pdffile = self.generic_path(doc.basefile, 'downloaded', '.pdf')

        wordfiles = (self.generic_path(doc.basefile, 'downloaded', '.doc'),
                     self.generic_path(doc.basefile, 'downloaded', '.docx'),
                     self.generic_path(doc.basefile, 'downloaded', '.wpd'),
                     self.generic_path(doc.basefile, 'downloaded', '.rtf'))
        wordfile = None
        for f in wordfiles:
            if os.path.exists(f):
                wordfile = f

        # if we lack a .pdf file, use Open/LibreOffice to convert any
        # .wpd or .doc file to .pdf first
        if (wordfile
                and not os.path.exists(pdffile)):
            intermediate_pdf = self.generic_path(
                doc.basefile, "intermediate", ".pdf")
            if not os.path.exists(intermediate_pdf):
                cmdline = "%s --headless -convert-to pdf -outdir '%s' %s" % (self.config.get('soffice', 'soffice'),
                                                                             os.path.dirname(intermediate_pdf),
                                                                             wordfile)
                self.log.debug(
                    "%s: Converting to PDF: %s" % (doc.basefile, cmdline))
                (ret, stdout, stderr) = util.runcmd(
                    cmdline, require_success=True)
            pdffile = intermediate_pdf

        if os.path.exists(pdffile):
            self.log.debug("%s: Using %s" % (doc.basefile, pdffile))
            intermediate_dir = os.path.dirname(
                self.generic_path(doc.basefile, 'intermediate', '.foo'))
            self.setup_logger('pdfreader', self.config.get('log', 'INFO'))
            pdfreader = PDFReader()
            pdfreader.read(pdffile, intermediate_dir)
            self.parse_from_pdfreader(pdfreader, doc)
        else:
            downloaded_path = self.downloaded_path(doc.basefile)
            intermediate_path = self.generic_path(
                doc.basefile, 'intermediate', '.txt')
            self.log.debug("%s: Using %s (%s)" % (doc.basefile,
                           downloaded_path, intermediate_path))
            if not os.path.exists(intermediate_path):
                html = codecs.open(
                    downloaded_path, encoding="iso-8859-1").read()
                util.writefile(intermediate_path, util.extract_text(
                    html, '<pre>', '</pre>'), encoding="utf-8")
            textreader = TextReader(intermediate_path, encoding="utf-8")
            self.parse_from_textreader(textreader, doc)
            # How to represent that one XHTML doc was created from
            # plaintext, and another from PDF? create a bnode
            # representing the source prov:wasDerivedFrom and set its
            # dct:format to correct mime type

    def parse_from_textreader(self, textreader, doc):
        describer = Describer(doc.meta, doc.uri)
        for p in textreader.getiterator(textreader.readparagraph):
            # print "Handing %r (%s)" % (p[:40], len(doc.body))
            if not p.strip():
                continue
            elif not doc.body and 'Obs! Dokumenten i denna databas kan vara ofullständiga.' in p:
                continue
            elif not doc.body and p.strip().startswith("Dokument:"):
                # We already know this
                continue
            elif not doc.body and p.strip().startswith("Titel:"):
                describer.value(
                    self.ns['dct'].title, util.normalize_space(p[7:]))
            else:
                doc.body.append(Preformatted([p]))

    def create_external_resources(self, doc):
        if doc.body and isinstance(doc.body[0], PDFReader):
            super(PropTrips, self).create_external_resources(doc)

    @classmethod
    def tabs(cls, primary=False):
        return [['Förarbeten', '/forarb/']]


class PropRiksdagen(Riksdagen):
    module_dir = "propriksdagen"
    rdf_type = RPUBL.Proposition

    def __init__(self, options):
        super(PropRiksdagen, self).__init__(options)
        self.document_type = self.PROPOSITION

    @classmethod
    def tabs(cls, primary=False):
        return [['Förarbeten', '/forarb/']]


class Propositioner(CompositeRepository):
    subrepos = PropPolo, PropTrips, PropRiksdagen
    module_dir = "prop"
    xslt_template = "paged.xsl"
    storage_policy = "dir"
    rdf_type = RPUBL.Proposition

    @classmethod
    def basefile_from_path(cls, path):
        # data/dirtrips/downloaded/2011/7.html => 2011:7
        seg = os.path.splitext(path)[0].split(os.sep)
        return ":".join(seg[seg.index(cls.module_dir) + 2:])

    @classmethod
    def tabs(cls, primary=False):
        return [['Förarbeten', '/forarb/']]
