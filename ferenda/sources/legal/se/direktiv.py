#!/usr/bin/env python
# -*- coding: iso-8859-1 -*-
#
# A number of different classes each fetching the same data from
# different sources
import sys
import os
import re
import datetime
import logging
import functools
import codecs

from bs4 import BeautifulSoup
from rdflib import Literal, Namespace, URIRef, RDF, RDFS, Graph

from ferenda import util
from ferenda import DocumentRepository, PDFDocumentRepository, CompositeRepository, Document
from ferenda.errors import DocumentRemovedError
from . import SwedishLegalSource, Regeringen, RPUBL
from ferenda.pdfreader import PDFReader, Page, Textbox, Textelement
from ferenda.textreader import TextReader
from ferenda.elements import Paragraph, Heading, UnicodeSubject, ListItem
from ferenda.describer import Describer


class Continuation(object):
    pass


class DirTrips(SwedishLegalSource):
    module_dir = "dirtrips"

    # It's possible to alter the ${MAXPAGE} parameter to get a page
    # with all 3000+ documents. We don't do that, though. It's mean.
    start_url = "http://62.95.69.15/cgi-bin/thw?${HTML}=dir_lst&${OOHTML}=dir_dok&${SNHTML}=dir_err&${MAXPAGE}=26&${TRIPSHOW}=format=THW&${BASE}=DIR"

    # %s can be either on the form "Dir. 2010:51" or just
    # "2010:52". We calculate the URL based on what is listed in the
    # search result, using the optional url parameter to
    # download_single
    document_url = "http://62.95.69.15/cgi-bin/thw?${APPL}=DIR&${BASE}=DIR&${HTML}=dir_dok&${TRIPSHOW}=format=THW&BET=%s"

    rdf_type = RPUBL.Direktiv

    @classmethod
    def basefile_from_path(cls, path):
        # data/dirtrips/downloaded/2011/7.html => 2011:7
        seg = os.path.splitext(path)[0].split(os.sep)
        return ":".join(seg[seg.index(cls.module_dir) + 2:])

    def download(self):
        refresh = self.get_moduleconfig('refresh', bool, False)
        self.log.info("Starting at %s" % self.start_url)
        self.browser.open(self.start_url)
        done = False
        pagecnt = 1
        while not done:
            self.log.info('Result page #%s' % pagecnt)
            for link in self.browser.links(text_regex=r'(\d{4}:\d+)'):
                basefile = re.search('(\d{4}:\d+)', link.text).group(1)
                url = self.document_url % urllib.parse.quote(link.text)
                self.download_single(basefile, refresh=refresh, url=url)
            try:
                self.browser.follow_link(text='Fler poster')
                pagecnt += 1
            except LinkNotFoundError:
                self.log.info(
                    'No next page link found, this was the last page')
                done = True

    def parse_basefile(self, basefile):
        # create an Document instance with an initialized doc.meta RDFLib graph
        doc = self.make_document()
        intermediate_path = self.generic_path(basefile, 'intermediate', '.txt')
        downloaded_path = self.downloaded_path(basefile)
        doc.uri = self.canonical_uri(basefile)
        doc.lang = "sv"
        html = codecs.open(downloaded_path, encoding="iso-8859-1").read()
        header_chunk = util.extract_text(
            html, '<pre>\n   <pre>', '<hr>', strip_tags=False)
        self.make_meta(header_chunk, doc.meta, doc.uri, basefile)
        util.writefile(intermediate_path, util.extract_text(
            html, '<pre>', '</pre>'), encoding="utf-8")
        reader = TextReader(intermediate_path, encoding="utf-8")
        reader.readparagraph()
        self.make_body(reader, doc.body)

        # Iterate through body tree and find things to link to (See
        # EurlexTreaties.process_body for inspiration)
        self.process_body(doc.body, '', doc.uri)
        return doc

    def header_lines(self, header_chunk):
        header = re.compile("([^:]+):\s*<b>([^<]*)</b>")
        for m in header.finditer(header_chunk):
            yield [util.normalize_space(x) for x in m.groups()]

    def make_meta(self, chunk, meta, uri, basefile):
        d = Describer(meta, uri)
        dct = self.ns['dct']
        prov = self.ns['prov']
        owl = self.ns['owl']
        rpubl = RPUBL

        d.rdftype(self.rdf_type)
        d.value(prov.wasGeneratedBy, self.qualified_class_name())

        # predicates maps key strings to corresponsing RDFLib terms,
        # e.g. "Rubrik" -> dct:title
        predicates = {'Dir nr': dct.identifier,
                      'Departement': rpubl.departement,
                      'Beslut vid regeringssammanträde':
                      rpubl.beslutsdatum,
                      'Rubrik': dct.title,
                      'Senast ändrad': dct.changed
                      }
        # munger contains a set of tuples where the first item is a
        # method for converting a plain text into the appropriate
        # RDFLib value, e.g:
        # - "Utredning av foo" => Literal("Utredning av foo",lang="sv")
        # - "1987-02-19" => datetime(1987,2,19)
        # - "Arbetsdepartementet" => URIRef("http://lagen.nu/terms/arbdep")
        # The second item is the Describer method that
        # should be used to add the value to the graph, i.e. .value
        # for Literals and .rel for URIRefs
        munger = {'Dir nr': (self.sanitize_identifier, d.value),  # the RDFLib constructor
                  'Departement': (functools.partial(self.lookup_resource, warn=False), d.rel),
                  'Beslut vid regeringssammanträde': (self.parse_iso_date, d.value),
                  'Rubrik': (self.sanitize_rubrik, d.value),
                  'Senast ändrad': (self.parse_iso_date, d.value)
                  }

        # headerlines wraps a TextReader in an iterator that parses
        # "key:value\n" lines with support for line continuation, eg
        # "long\nkey:long\nvalue\n"
        for (key, val) in self.header_lines(chunk):
            if not val:
                continue
            try:
                pred = predicates[key]
                (transformer, setter) = munger[key]
                setter(pred, transformer(val))
            except (KeyError, ValueError) as e:
                self.log.error("Couldn't munge value '%s' into a proper object for predicate '%s'" % (val, key))

        d.rel(dct.publisher, self.lookup_resource("Regeringskansliet"))
        d.rel(owl.sameAs, self.sameas_uri(uri))
        self.infer_triples(d, basefile)
        # print meta.serialize(format="turtle")

    def sanitize_rubrik(self, rubrik):
        if rubrik == "Utgår":
            raise DocumentRemovedError()

        rubrik = re.sub("^/r2/ ", "", rubrik)
        return Literal(rubrik, lang="sv")

    def sanitize_identifier(self, identifier):
        # "Dir.1994:111" -> "Dir. 1994:111"
        if re.match("Dir.\d+", identifier):
            identifier = "Dir. " + identifier[4:]
        if not identifier.startswith("Dir. "):
            identifier = "Dir. " + identifier
        return Literal(identifier)

    def make_body(self, reader, body):
        current_type = None
        for p in reader.getiterator(reader.readparagraph):
            new_type = self.guess_type(p, current_type)
            #if not new_type == None:
            #    print "Guessed %s for %r" % (new_type.__name__,p[:20])
            if new_type is None:
                pass
            elif new_type == Continuation and len(body) > 0:
                # Don't create a new text node, add this text to the last
                # text node created
                para = body.pop()
                para.append(p)
                body.append(para)
            else:
                if new_type == Continuation:
                    new_type = Paragraph
                body.append(new_type([p]))
                current_type = new_type

    def guess_type(self, p, current_type):
        if not p:  # empty string
            return None
        # complex heading detection heuristics: Starts with a capital
        # or a number, and doesn't end with a period (except in some
        # cases).
        elif ((re.match("^\d+", p)
               or p[0].lower() != p[0])
              and not (p.endswith(".") and
                       not (p.endswith("m.m.") or
                            p.endswith("m. m.") or
                            p.endswith("m.fl.") or
                            p.endswith("m. fl.")))):
            return Heading
        elif p.startswith("--"):
            return ListItem
        elif (p[0].upper() != p[0]):
            return Continuation  # magic value
        else:
            return Paragraph

    def process_body(self, element, prefix, baseuri):
        if isinstance(element, str):
            return
        fragment = prefix
        uri = baseuri
        for p in element:
            self.process_body(p, fragment, baseuri)

    def canonical_uri(self, basefile):
        return self.config['url'] + "publ/dir/" + basefile

    @classmethod
    def tabs(cls, primary=False):
        return [['Förarbeten', '/forarb/']]


class DirAsp(SwedishLegalSource, PDFDocumentRepository):
    module_dir = "dirasp"
    start_url = "http://62.95.69.24/search.asp"
    document_url = "http://62.95.69.24/KOMdoc/%s.PDF"
    source_encoding = "iso-8859-1"
    browser_use_robustfactory = True
    rdf_type = RPUBL.Direktiv

    def get_globals(self):
        return globals()

    def download(self, startyear=2006):
        refresh = self.get_moduleconfig('refresh', bool, False)
        startyear = self.get_moduleconfig('startyear', int, 2006)

        self.browser.open(self.start_url)
        done = False
        pagecnt = 1
        for y in range(startyear, datetime.datetime.today().year + 1):
            self.browser.select_form(nr=0)
            self.browser["kom_nr"] = "%d:*" % y
            self.browser["title"] = ""
            self.browser.submit()
            links = list(self.browser.links(text_regex=r'(\d{4}:\d+)'))
            self.log.info(
                "Searching for year %s, %d results" % (y, len(links)))
            for link in links:
                # convert 2006:02 to 2006:2 for consistency
                segments = re.search("(\d+):(\d+)", link.text).groups()
                basefile = ":".join([str(int(x)) for x in segments])
                # we use link.absolute_url rather than relying on our
                # own basefile -> url code in remote_url. It seems
                # that in least one case the URL formatting rule is
                # not followed by the system...
                self.download_single(
                    basefile, refresh=refresh, url=link.absolute_url)
            self.browser.back()

    def remote_url(self, basefile):
        yy = int(basefile[2:4])
        num = int(basefile[5:])
        segment = "%02d/%02d%04d" % (yy, yy, num)
        return self.document_url % segment

    def canonical_uri(self, basefile):
        return self.config['url'] + "publ/dir/" + basefile

    def parse_from_pdfreader(self, pdfreader, basefile):
        doc = super(DirAsp, self).parse_from_pdfreader(pdfreader, basefile)
        d = Describer(doc.meta, doc.uri)
        self.infer_triples(d, basefile)
        return doc

    @classmethod
    def tabs(cls, primary=False):
        return [['Förarbeten', '/forarb/']]


class DirSou(SwedishLegalSource, PDFDocumentRepository):
    module_dir = "dirsou"
    start_url = "http://www.sou.gov.se/direktiv.htm"
    document_url = "http://www.sou.gov.se/kommittedirektiv/%s.pdf"
    basefile_template = "\d{4}:\d+"
    rdf_type = RPUBL.Direktiv

    # just to make download_single works in standalone mode (where a
    # URL is not provided)
    def remote_url(self, basefile):
        (year, num) = basefile.split(":")
        segment = "%s/dir%s_%s" % (year, year, num)
        return self.document_url % segment

    def get_globals(self):
        return globals()

    def canonical_uri(self, basefile):
        return self.config['url'] + "publ/dir/" + basefile

    def parse_from_pdfreader(self, pdfreader, basefile):
        doc = super(DirSou, self).parse_from_pdfreader(pdfreader, basefile)
        d = Describer(doc.meta, doc.uri)
        self.infer_triples(d, basefile)
        return doc

    @classmethod
    def tabs(cls, primary=False):
        return [['Förarbeten', '/forarb/']]


class DirPolopoly(Regeringen):
    module_dir = "dirpolo"
    cssfiles = ['../ferenda/res/css/pdfview.css']
    jsfiles = ['../ferenda/res/js/pdfviewer.js']
    re_basefile_strict = re.compile(r'Dir\. (\d{4}:\d+)')
    re_basefile_lax = re.compile(r'(?:[Dd]ir\.?|) ?(\d{4}:\d+)')
    rdf_type = RPUBL.Direktiv

    def __init__(self, options):
        super(DirPolopoly, self).__init__(options)
        self.document_type = self.KOMMITTEDIREKTIV

    def sanitize_identifier(self, identifier):
        # "Dir.1994:111" -> "Dir. 1994:111"
        if re.match("Dir.\d+", identifier):
            identifier = "Dir. " + identifier[4:]
        if not identifier.startswith("Dir. "):
            identifier = "Dir. " + identifier
        return Literal(identifier)

# Does parsing, generating etc from base files:


class Direktiv(SwedishLegalSource, CompositeRepository):
    subrepos = DirPolopoly, DirSou, DirAsp, DirTrips
    module_dir = "dir"
    xslt_template = "paged.xsl"
    xslt_template_toc = "toc.xsl"
    storage_policy = "dir"
    rdf_type = RPUBL.Direktiv

    @classmethod
    def basefile_from_path(cls, path):
        seg = path.split(os.sep)
        seg = seg[seg.index(cls.module_dir) + 2:-1]
        seg = [x.replace("-", "/") for x in seg]
        # print len(seg)
        assert 2 <= len(seg) <= 3, "list of segments is too long or too short"
        # print "path: %s, seg: %r, basefile: %s" % (path,seg,":".join(seg))
        return ":".join(seg)

    @classmethod
    def tabs(cls, primary=False):
        return [['Förarbeten', '/forarb/']]
