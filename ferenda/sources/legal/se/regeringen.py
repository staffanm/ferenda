# -*- coding: utf-8 -*-
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
from builtins import *

# A abstract base class for fetching and parsing documents
# (particularly preparatory works) from regeringen.se
import os
import re
import codecs
from datetime import datetime
from urllib.parse import urljoin, urlencode

import requests
import lxml.html
from bs4 import BeautifulSoup
from rdflib import URIRef
from rdflib.namespace import SKOS
from cached_property import cached_property

from ferenda import Describer, DocumentEntry, PDFAnalyzer
from ferenda import util
from ferenda.decorators import recordlastdownload, downloadmax
from ferenda.elements import Section, Link, Body, CompoundElement
from ferenda.pdfreader import PDFReader, Textbox
from ferenda.errors import DocumentRemovedError
from . import SwedishLegalSource, RPUBL
from .legalref import LegalRef
from .swedishlegalsource import offtryck_gluefunc
from .elements import PreambleSection, UnorderedSection, Lagrumskommentar, Sidbrytning, VerbatimSection

class FontmappingPDFReader(PDFReader):
    # Fonts in Propositioner get handled wierdly by pdf2xml
    # -- sometimes they come out as "Times New
    # Roman,Italic", sometimes they come out as
    # "TimesNewRomanPS-ItalicMT". Might be caused by
    # differences in the tool chain that creates the PDFs.
    # Sizes seem to be consistent though.
    #
    # This subclass maps one class of fontnames to another by
    # postprocessing the result of parse_xml

    def _parse_xml(self, xmlfp):
        super(FontmappingPDFReader, self)._parse_xml(xmlfp)
        for key, val in self.fontspec.items():
            if 'family' in val:
                # Times New Roman => TimesNewRomanPSMT
                # Times New Roman,Italic => TimesNewRomanPS-ItalicMT
                if val['family'] == "Times New Roman":
                    val['family'] = "TimesNewRomanPSMT"
                if val['family'] == "Times New Roman,Italic":
                    val['family'] = "TimesNewRomanPS-ItalicMT"
                # Not 100% sure abt these last two
                if val['family'] == "Times New Roman,Bold":
                    val['family'] = "TimesNewRomanPS-BoldMT"
                if val['family'] == "Times New Roman,BoldItalic":
                    val['family'] = "TimesNewRomanPS-BoldItalicMT"


class Regeringen(SwedishLegalSource):
    RAPPORT = 1341
    DS = 1325
    FORORDNINGSMOTIV = 1326
    KOMMITTEDIREKTIV = 1327
    LAGRADSREMISS = 2085
    PROPOSITION = 1329
    SKRIVELSE = 1330
    SOU = 1331
    SO = 1332
    
    document_type = None  # subclasses must override
    start_url = "http://www.regeringen.se/Filter/GetFilteredItems"
    start_url = "http://www.regeringen.se/Filter/RssFeed"
    downloaded_suffix = ".html"  # override PDFDocumentRepository
    storage_policy = "dir"
    alias = "regeringen"
    xslt_template = "xsl/forarbete.xsl"
    download_accept_404 = True
    session = None

    @cached_property
    def urlmap(self):
        urlmap_path = self.store.path("urls", "downloaded", ".map", storage_policy="file")
        urlmap = {}
        if os.path.exists(urlmap_path):
            with codecs.open(urlmap_path, encoding="utf-8") as fp:
                for line in fp:
                    if "\t" not in line:
                        continue
                    url, identifier = line.split("\t")
                    urlmap[url] = identifier
        return urlmap


    @recordlastdownload
    def download(self, basefile=None):
        params = {'filterType': 'Taxonomy',
                  'filterByType': 'FilterablePageBase',
                  'preFilteredCategories': '1324',
                  'rootPageReference': '0',
                  'filteredContentCategories': self.document_type
                  }
        if 'lastdownload' in self.config and not self.config.refresh:
            params['fromDate'] = self.config.lastdownload.strftime("%Y-%m-%d")
        self.log.debug("Loading documents starting from %s" %
                       params.get('fromDate', "the beginning"))
        try: 
            for basefile, url in self.download_get_basefiles(params):
                try:
                    self.download_single(basefile, url)
                except requests.exceptions.HTTPError as e:
                    if self.download_accept_404 and e.response.status_code == 404:
                        self.log.error("%s: %s %s" % (basefile, url, e))
                        ret = False
                    else:
                        raise e
        finally:
            urlmap_path = self.store.path("urls", "downloaded", ".map", storage_policy="file")
            with codecs.open(urlmap_path, "w", encoding="utf-8") as fp:
                for url, identifier in self.urlmap.items():
                    fp.write("%s\t%s\n" % (url, identifier))


                    
    def attribs_from_url(self, url):
        # Neither search results nor RSS feeds from regeringen.se
        # contain textual information about the identifier
        # (eg. "Prop. 2015/16:64") of each document. However, the URL
        # (eg. http://www.regeringen.se/rattsdokument/proposition/2015/12/prop.-20151664/
        # often contains the same information. But not always...
        year = ordinal = None
        m = self.re_urlbasefile_strict.search(url)
        if m and (1900 < int(m.group(1)[:4]) < 2100):
            (year, ordinal) = m.groups()
        else:
            m = self.re_urlbasefile_lax.search(url)
            if m: 
                (year, ordinal) = m.groups()
                year = year.replace("_", "")
        if year and ordinal:
            return {'rdf:type': self.urispace_segment.split("/")[-1],
                    'rpubl:arsutgava': year,
                    'rpubl:lopnummer': ordinal}
        elif url in self.urlmap:
            identifier = [self.urlmap[url]]
            doclabels = []
        else:
            self.log.warning("Can't find out doc attribs from url %s itself, downloading it..." % url)
            soup = BeautifulSoup(self.session.get(url).text, "lxml")
            identifier = []
            identifier_node = soup.find("span", "h1-vignette")
            if identifier_node:
                identifier = [identifier_node.text]
                self.urlmap[url] = identifier_node.text
            else:
                self.urlmap[url] = None
            doclabels = [x[1] for x in self.find_pdf_links(soup, None, labels=True)]
        for candidate in identifier + doclabels:
            m = self.re_basefile_strict.search(candidate)
            if m: 
                (year, ordinal) = m.group(1).split(":")
            else:
                m = self.re_basefile_lax.search(candidate)
                if m:
                    (year, ordinal) = m.group(1).split(":")
            if year and ordinal:
                return {'rdf:type': self.urispace_segment.split("/")[-1],
                        'rpubl:arsutgava': year,
                        'rpubl:lopnummer': ordinal}
        raise ValueError("Can't find doc attribs from either url %s or the page at that url" % url)

    @downloadmax
    def download_get_basefiles(self, params):
        done = False
        while not done:
            qsparams = urlencode(params)
            searchurl = self.start_url + "?" + qsparams
            self.log.debug("Loading page #%s" % params.get('page', 1))
            resp = self.session.get(searchurl)
            tree = lxml.etree.fromstring(resp.text)
            done = True
            for item in tree.findall(".//item"):
                done = False
                url = item.find("link").text
                try:
                    attribs = self.attribs_from_url(url)
                    basefile = "%s:%s" % (attribs['rpubl:arsutgava'], attribs['rpubl:lopnummer'])
                    basefile = self.sanitize_basefile(basefile)
                    self.log.debug("%s: <- %s" % (basefile, url))
                    yield basefile, url
                except ValueError as e:
                    self.log.error(e)
            params['page'] = params.get('page', 1) + 1

    # Correct some invalid identifiers spotted in the wild:
    # 1999/20 -> 1999/2000
    # 2000/2001 -> 2000/01
    # 1999/98 -> 1999/2000
    # 2007/20:08123 -> 2007/08:123
    def sanitize_basefile(self, basefile):
        if self.document_type == self.PROPOSITION:
            (y1, y2, idx) = re.split("[:/]", basefile)
            assert len(
                y1) == 4, "Basefile %s is invalid beyond sanitization" % basefile
            assert idx.isdigit(), "Basefile %s has a non-numeric ordinal" % basefile
            idx = int(idx) # remove any leading zeroes
            if y1 == "1999" and y2 != "2000":
                sanitized = "1999/2000:%s" % idx
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
        else:  # KOMMITTEDIREKTIV, SOU, DS
            y, idx = basefile.split(":")
            assert len(y) == 4, "Basefile %s is invalid beyond sanitization" % basefile
            assert 1900 < int(y) < 2100, "Basefile %s has improbable year %s" % (basefile, y)
            sanitized = basefile
        return sanitized

    @property
    def urispace_segment(self):
        return {self.PROPOSITION: "prop",
                self.DS: "utr/ds",
                self.SOU: "utr/sou",
                self.KOMMITTEDIREKTIV: "dir"}.get(self.document_type)

    def download_single(self, basefile, url=None):
        if not url:
            url = self.remote_url(basefile)
            if not url:  # remote_url failed
                return
        filename = self.store.downloaded_path(basefile)  # just the html page
        updated = pdfupdated = False
        created = not os.path.exists
        if (not os.path.exists(filename) or self.config.refresh):
            existed = os.path.exists(filename)
            updated = self.download_if_needed(url, basefile, filename=filename)
            docid = url.split("/")[-1]
            if existed:
                if updated:
                    self.log.info("%s: updated from %s" % (basefile, url))
                else:
                    self.log.debug("%s: %s is unchanged, checking PDF files" %
                                   (basefile, filename))
            else:
                self.log.info("%s: downloaded from %s" % (basefile, url))

            soup = BeautifulSoup(codecs.open(filename, encoding=self.source_encoding), "lxml")
            cnt = 0
            pdffiles = self.find_pdf_links(soup, basefile)
            if pdffiles:
                for pdffile in pdffiles:
                    pdfurl = urljoin(url, pdffile)
                    basepath = pdffile.split("/")[-1]
                    pdffilename = self.store.downloaded_path(basefile, attachment=basepath)
                    if not pdffilename.lower().endswith(".pdf"):
                        pdffilename += ".pdf"
                    if self.download_if_needed(pdfurl, basefile, filename=pdffilename):
                        pdfupdated = True
                        self.log.debug(
                            "    %s is new or updated" % pdffilename)
                    else:
                        self.log.debug("    %s is unchanged" % pdffilename)
            else:
                self.log.warning(
                    "%s (%s) has no downloadable PDF files" % (basefile, url))
            if updated or pdfupdated:
                pass
            else:
                self.log.debug("%s and all PDF files are unchanged" % filename)
        else:
            self.log.debug("%s: %s already exists" % (basefile, filename))

        entry = DocumentEntry(self.store.documententry_path(basefile))
        now = datetime.now()
        entry.orig_url = url
        if created:
            entry.orig_created = now
        if updated or pdfupdated:
            entry.orig_updated = now
        entry.orig_checked = now
        entry.save()

        return updated or pdfupdated

    def metadata_from_basefile(self, basefile):
        a = super(Regeringen, self).metadata_from_basefile(basefile)
        a["rpubl:arsutgava"], a["rpubl:lopnummer"] = basefile.split(":", 1)
        return a

    blacklist = set([(SOU, "2008:35"),  # very atypical report
                     (DS, "2002:34"),   # 2-column report, uninteresting
                     (SOU, "2002:11"),  # -""-
                    ])

    def extract_head(self, fp, basefile):
        # Some documents are just beyond usable and/or completely
        # uninteresting from a legal information point of view. We
        # keep a hardcoded black list to skip these. This is the
        # earliest point at which we can check against that blacklist.
        # FIXME: we should have a no-semantic-parse fallback that does
        # no analysis, just attempts to create a viewable
        # page-oriented HTML representation of the PDF. Maybe that
        # fallback should even be part of
        # ferenda.PDFDocumentRepository
        if (self.document_type, basefile) in self.blacklist:
            raise DocumentRemovedError("%s is blacklisted" % basefile,
                                       dummyfile=self.store.parsed_path(basefile))
        soup = BeautifulSoup(fp.read(), "lxml")
        self._rawbody = soup.body
        return self._rawbody.find(id="content")

    def extract_metadata(self, rawhead, basefile):
        content = rawhead
        title = list(content.find("h1").children)[0].string.strip()
        # in some cases, the <h1> is not a real title but rather an
        # identifier. Do a simple sanity check for this
        # the longest possible id, "Prop. 1999/2000:123", is 19 chars
        if len(title) < 20 and title.endswith(basefile):
            identifier = title
            title = ""  # FIXME: hunt for title amongst the PDF file links
        else:
            identifier_node = content.find("span", "h1-vignette")
            if identifier_node:
                identifier = identifier_node.text
            else:
                identifier = ""  # infer_metadata calls
                                 # infer_identifier if this is falsy,
                                 # which will be good enough. No need
                                 # to warn.
                
        # the <time> element has a datetime attrib with
        # machine-readable timestamp, but this has unwarranted high
        # precision. We'd like to capture just a xsd:YearMonth if
        # "Publicerad: April 1994", not a datetime(1994, 4, 1, 0, 0,
        # 0). So we just grab text and use parse_swedish_date later
        # on.
        utgiven = content.find("span", "published").time.text
        try:
            ansvarig = content.find("p", "media--publikations__sender").a.text
        except AttributeError:
            self.log.warning("%s: No ansvarig departement found" % basefile)
            ansvarig = None
        s = content.find("div", "has-wordExplanation")
        for a in s.find_all("a"):  # links in summary are extra tacked-on bogus
            a.decompose()
        sammanfattning = " ".join(s.strings)

        # look for related items. We need to import some subclasses of
        # this class in order to do that, which we need to do inline.
        from .direktiv import DirRegeringen
        from .ds import Ds
        from .sou import SOURegeringen
        linkfrags = {self.KOMMITTEDIREKTIV: [],
                     self.DS: [("/komittedirektiv/", DirRegeringen)],
                     self.PROPOSITION: [("/kommittedirektiv/", DirRegeringen),
                                        ("/departementsserien-och-promemorior/", Ds),
                                        ("/statens-offentliga-utredningar", SOURegeringen)],
                     self.SOU: [("/kommittedirektiv/", DirRegeringen)]
        }[self.document_type]
        utgarFran = []
        island = content.find("div", "island")
        for linkfrag, cls in linkfrags:
            inst = cls(datadir=self.config.datadir)
            regex = re.compile(".*"+linkfrag)
            for link in island.find_all("a", href=regex):
                # from a relative link on the form
                # "/rattsdokument/kommittedirektiv/2012/01/dir.-20123/",
                # extract
                # {'rdf:type': RPUBL.Kommittedirektiv,
                #  'rpubl:arsutgava': 2012,
                #  'rpubl:lopnummer} -> attributes_to_resource -> coin_uri
                try:
                    attribs = inst.attribs_from_url(urljoin(inst.start_url, link["href"]))
                except ValueError:
                    self.log.warning("%s: Can't find out properties for linked resource %s" % (basefile, link["href"]))
                    continue
                if attribs["rdf:type"] == "dir":
                    attribs["rdf:type"] = RPUBL.Kommittedirektiv
                else:
                    # lookup on skos:altLabel, but with "Ds" and "SOU"
                    # as keys (not "ds" and "sou")
                    altlabel = attribs["rdf:type"].upper() if attribs["rdf:type"] == "sou" else attribs["rdf:type"].capitalize()
                    attribs["rpubl:utrSerie"] = self.lookup_resource(altlabel, SKOS.altLabel)
                    attribs["rdf:type"] = RPUBL.Utredningsbetankande
                uri = self.minter.space.coin_uri(self.attributes_to_resource(attribs))
                utgarFran.append(uri)
        a = self.metadata_from_basefile(basefile)
        a.update({'dcterms:title': title,
                  'dcterms:identifier': identifier,
                  'dcterms:issued': utgiven,
                  'dcterms:abstract': sammanfattning,
                  'rpubl:utgarFran': utgarFran,
                  'rpubl:departement': ansvarig
        })
        return a

    def sanitize_metadata(self, a, basefile):
        # trim space
        for k in ("dcterms:title", "dcterms:abstract"):
            if k in a:
                a[k] = util.normalize_space(a[k])
        # trim identifier
        a["dcterms:identifier"] = a["dcterms:identifier"].replace("ID-nummer: ", "")
        # save for later
        self._identifier = a["dcterms:identifier"]
        # it's rare, but in some cases a document can be published by
        # two different departments (eg dir. 2011:80). Convert string
        # to a list in these cases (SwedishLegalSource.polish_metadata
        # will handle that)
        if a["rpubl:departement"] and ", " in a["rpubl:departement"]:
            a["rpubl:departement"] = a["rpubl:departement"].split(", ")
        # remove empty utgarFran list
        if a["rpubl:utgarFran"]:
            a["rpubl:utgarFran"] = [URIRef(x) for x in a["rpubl:utgarFran"]]
        else:
            del a["rpubl:utgarFran"]

        # FIXME: possibly derive utrSerie from self.document_type?
        if self.rdf_type == RPUBL.Utredningsbetankande:
            altlabel = "SOU" if self.document_type == Regeringen.SOU else "Ds"
            a["rpubl:utrSerie"] = self.lookup_resource(altlabel, SKOS.altLabel)
        return a

    def polish_metadata(self, sane_attribs):
        resource = super(Regeringen, self).polish_metadata(sane_attribs)
        # FIXME: This is hackish -- need to rethink which parts of the
        # parse steps needs access to which data
        self._resource = resource
        return resource

    def sanitize_body(self, rawbody):
        sanitized = super(Regeringen, self).sanitize_body(rawbody)
        sanitized.analyzer = self.get_pdf_analyzer(sanitized)
        return sanitized

    def extract_body(self, fp, basefile):
        if (self.document_type == self.PROPOSITION and
            basefile.split(":")[1] in ("1", "100")):
            self.log.warning("%s: Will only process metadata, creating"
                             " placeholder for body text" % basefile)
            # means vår/höstbudget. Create minimal placeholder text
            return ["Dokumentttext saknas (se originaldokument)"]
        # reset global state
        PreambleSection.counter = 0
        UnorderedSection.counter = 0
        pdffiles = [x + ("" if x.lower().endswith(".pdf") else ".pdf") for x in self.find_pdf_links(self._rawbody, basefile)]
        if not pdffiles:
            self.log.error(
                "%s: No PDF documents found, can't parse anything" % basefile)
            return ["[Dokumenttext saknas]"]
        
        # read a list of pdf files and return a contatenated PDFReader
        # object (where do we put metrics? On the PDFReader itself?
        return self.read_pdfs(basefile, pdffiles, self._identifier)

    parse_types = (LegalRef.RATTSFALL,
                   LegalRef.LAGRUM,
                   LegalRef.KORTLAGRUM,
                   LegalRef.FORARBETEN,
#                   LegalRef.EULAGSTIFTNING,
#                   LegalRef.EURATTSFALL
    )

    def parse_body(self, fp, basefile):
        # this version knows how to use an appropriate analyzer to
        # segment documents into subdocs and use the appropritate
        # parsing method on each subdoc. FIXME: This should really be
        # available to all classes that make use of
        # SwedishLegalSource.offtryck_{parser,gluefunc}. NOTE: this
        # requires that sanitize_body has set up a PDFAnalyzer
        # subclass instance as a property on the sanitized object
        # (normally a PDFReader or StreamingPDFReader)
        rawbody = self.extract_body(fp, basefile)
        sanitized = self.sanitize_body(rawbody)
        allbody = Body()
        initialstate = {'pageno': 1}
        documents = sanitized.analyzer.documents()
        if len(documents) > 1:
            self.log.warning("%s: segmented into docs %s" % (basefile, documents))
        for (startpage, pagecount, tag) in documents:
            if tag == 'main':
                initialstate['pageno'] -= 1  # argh....
                parser = self.get_parser(basefile, sanitized, initialstate)
                tokenstream = sanitized.textboxes(offtryck_gluefunc,
                                                  pageobjects=True,
                                                  startpage=startpage,
                                                  pagecount=pagecount)
                body = parser(tokenstream)
                for func, initialstate in self.visitor_functions(basefile):
                    # could be functions for assigning URIs to particular
                    # nodes, extracting keywords from text etc. Note: finding
                    # references in text with LegalRef is done afterwards
                    self.visit_node(body, func, initialstate)
                # print("%s: self.config.parserefs: %s, self.parse_types: %s" % (basefile, self.config.parserefs, self.parse_types))
                if self.config.parserefs and self.parse_types:
                    body = self.refparser.parse_recursive(body)
            else:
                # copy pages verbatim -- make no attempt to glue
                # textelements together, parse references etc. In
                # effect, this will yield pages full of absolute
                # positioned textboxes that don't reflow etc
                s = VerbatimSection()
                for relidx, page in enumerate(sanitized[startpage:startpage+pagecount]):
                    sb = Sidbrytning()
                    sb.ordinal = initialstate['pageno']+relidx
                    s.append(sb)
                    s.append(page)
                body = Body([s])
            # regardless of wether we used real parsing or verbatim
            # copying, we need to update the current page number
            lastpagebreak = self._find_subnode(body, Sidbrytning)
            initialstate['pageno'] = lastpagebreak.ordinal + 1
            allbody += body[:]
        return allbody

    def _find_subnode(self, node, cls, reverse=True):
        # Finds the first (or last if reversed=True) subnode of a
        # certain type in the given node, recursively
        if isinstance(node, cls):
            return node
        elif isinstance(node, (CompoundElement, list)):
            if reverse:
                iterable = reversed(node)
            else:
                iterable = node
            for subnode in iterable:
                res = self._find_subnode(subnode, cls)
                if isinstance(res, cls):
                    return res

    def postprocess_doc(self, doc):
        # loop through leading  textboxes and try to find dcterms:identifier,
        # dcterms:title and dcterms:issued (these should already be present
        # in doc.meta, but the values in the actual document should take
        # precendence
        def _check_differing(describer, predicate, newval):
            if describer.getvalue(predicate) != newval:
                self.log.debug("%s: HTML page: %s is %r, document: it's %r" %
                               (doc.basefile,
                                doc.meta.qname(predicate),
                                describer.getvalue(predicate),
                                newval))
                # remove old val
                d.graph.remove((d._current(),
                                predicate,
                                d.graph.value(d._current(), predicate)))
                d.value(predicate, newval)

        def helper(node, meta):
            for subnode in list(node):
                if isinstance(subnode, Textbox):
                    pass
                elif isinstance(subnode, list):
                    helper(subnode, meta)
        helper(doc.body, doc.meta)
        # the following postprocessing code is so far only written for
        # Propositioner
        if self.rdf_type != RPUBL.Proposition:
            return doc.body

        d = Describer(self._resource.graph, self._resource.identifier)
        title_found = identifier_found = issued_found = False
        for idx, element in enumerate(doc.body):
            if not isinstance(element, Textbox):
                continue
            str_element = str(element).strip()

            # dcterms:identifier
            if not identifier_found:
                m = self.re_basefile_lax.search(str_element)
                if m:
                    _check_differing(
                        d,
                        self.ns['dcterms'].identifier,
                        "Prop. " +
                        m.group(1))
                    identifier_found = True

            # dcterms:title FIXME: The fontsize comparison should be
            # done with respect to the resulting metrics (which we
            # don't have a reference to here, since they were
            # calculated in parse_pdf....)
            if not title_found and element.font.size == 20:
                # sometimes part of the the dcterms:identifier (eg " Prop."
                # or " 2013/14:51") gets mixed up in the title
                # textbox. Remove those parts if we can find them.
                if " Prop." in str_element:
                    str_element = str_element.replace(" Prop.", "").strip()
                if self.re_basefile_lax.search(str_element):
                    str_element = self.re_basefile_lax.sub("", str_element)
                _check_differing(d, self.ns['dcterms'].title, str_element)
                title_found = True

            # dcterms:issued
            if not issued_found and str_element.startswith("Stockholm den"):
                datestr = str_element[13:]
                if datestr.endswith("."):
                    datestr = datestr[:-1]
                pubdate = self.parse_swedish_date(datestr)
                _check_differing(d, self.ns['dcterms'].issued, pubdate)
                issued_found = True

            if title_found and identifier_found and issued_found:
                break

    # FIXME: Hook this up as a visitor function. Also needs to be
    # callable form
    def visitor_functions(self, basefile):
        sharedstate = {'basefile': basefile}
        return [(self.find_primary_law, sharedstate),
                (self.find_commentary, sharedstate)]

    def find_primary_law(self, node, state):
        if not isinstance(node, Section) or not node.title.startswith("Förslag till lag om ändring i"):
            if isinstance(node, Body):
                return state
            else:
                return None  # visit_node won't call any subnode
        state['primarylaw'] = self._parse_uri_from_text(node.title, state['basefile'])
        self.log.info("%s: find_primary_law finds %s" % (
            state['basefile'], state['primarylaw']))
        return None

    def find_commentary(self, node, state):
        if not isinstance(node, Section) or (node.title != "Författningskommentar"):
            if isinstance(node, Body):
                if 'commented_paras' not in state:
                    state['commented_paras'] = {}
                return state
            else:
                return None  # visit_node won't call any subnode
        commentary = []
        for subsection in node:
            if hasattr(subsection, 'title'):
                # find out which laws this proposition proposes to
                # change (can be new or existing)
                if re.match("Förslag(|et) till lag om ändring i", subsection.title):
                    uri = self._parse_uri_from_text(subsection.title, state['basefile'])
                elif re.match("Förslag(|et) till", subsection.title):
                    # create a reference that could pass for a real
                    # SFS-id, but with the name (the only identifying
                    # information we have at this point) encoded into
                    # it. FIXME: the numslug could be shorter if we'd
                    # make sure to only allow lower-case a-z and to a
                    # base26 conversion into an integer
                    lawname = subsection.title.split(" ", 2)[-1]
                    slug = re.sub('\W+', '', lawname).lower()
                    slug = slug.replace("å", "aa").replace("ä", "ae").replace("ö", "oe").replace("é", "e")
                    numslug = util.base26encode(slug)
                    assert util.base26decode(numslug) == slug
                    tmptext = "Fejklag (0000:%s)" % numslug
                    uri =self._parse_uri_from_text(tmptext, state['basefile'])
                commentary.append((uri, subsection))
                    
        if commentary == []:  # no subsecs, ie the prop changes a single law
            if 'primarylaw' in state:
                commentary.append((state['primarylaw'], node))
            else:
                self.log.warning("%s: Författningskommentar does not specify name of law and find_primary_law didn't find it either" % state['basefile'])
        for law, section in commentary:
            paras = []
            para = None
            for idx, subnode in enumerate(section):
                text = str(subnode).strip()
                if len(text) < 20 and text.endswith(" kap."):
                    # subsection heading indicating the start of a new
                    # chapter. alter the parsing context from law to
                    # chapter in law
                    law = self._parse_uri_from_text(text, state['basefile'], law)
                    if para is None:
                        paras.append(subnode)
                    else:
                        para.append(subnode)
                elif len(text) < 20 and text.endswith("§"):
                    comment_on = self._parse_uri_from_text(text, state['basefile'], law)
                    page = self._find_subnode(section[idx:], Sidbrytning, reverse=False)
                    if page:
                        pageno = page.ordinal - 1 
                    else:
                        pageno = None
                    if comment_on not in state['commented_paras']:
                        para = Lagrumskommentar(title=text,
                                                comment_on=comment_on,
                                                uri=None)
                        # the URI to the above Lagrumskommentar is
                        # dynamically constructed in
                        # Lagrumskommentar.as_xhtml
                        paras.append(para)
                        state['commented_paras'][comment_on] = pageno
                    else:
                        self.log.warning("Found another comment on %s at p %s (previous at %s), ignoring" % (comment_on, pageno, state['commented_paras'][comment_on]))
                        if para is None:
                            paras.append(subnode)
                        else:
                            para.append(subnode)
                else:
                    if para is None:
                        paras.append(subnode)
                    else:
                        para.append(subnode)
            # this is kinda risky but wth...
            section[:] = paras[:]
                        
    def _parse_uri_from_text(self, text, basefile, baseuri=None):
        if baseuri:
            prevuri = self.refparser._currenturl
            self.refparser._currenturl = baseuri
            prevallow = self.refparser._allow_relative
            self.refparser._allow_relative = True
        res = self.refparser.parse_string(text)
        links = [n for n in res if isinstance(n, Link)]
        if len(links) != 1:
            self.log.warning("%s: _parse_uri_from_text found %s links in '%s',"
                             "expected single link" %
                             (basefile, len(links), text))
            return None
        if baseuri:
            self.refparser._currenturl = prevuri
            self.refparser._allow_relative = prevallow
        return links[0].uri
            
    def sanitize_identifier(self, identifier):
        pattern = {self.KOMMITTEDIREKTIV: "%s. %s:%s",
                   self.DS: "%s %s:%s",
                   self.PROPOSITION: "%s. %s/%s:%s",
                   self.SKRIVELSE: "%s. %s/%s:%s",
                   self.SOU: "%s %s:%s",
                   self.SO: "%s %s:%s"}

        try:
            parts = re.split("[\.:/ ]+", identifier.strip())
            return pattern[self.document_type] % tuple(parts)
        except:
            self.log.warning("Couldn't sanitize identifier %s" % identifier)
            return identifier

    def sourcefiles(self, basefile, resource=None):
        with self.store.open_downloaded(basefile, "rb") as fp:
            soup = BeautifulSoup(fp.read(), "lxml")
        # FIXME: We might want to trim the labels here, eg to shorten
        # "En digital agenda, SOU 2014:13 (del 2 av 2) (pdf 1,4 MB)"
        # to something more display friendly.
        return self.find_pdf_links(soup, basefile, labels=True)

    def source_url(self, basefile):
        # this source does not have any predictable URLs, so we try to
        # find if we made a note on the URL when we ran download()
        entry = DocumentEntry(self.store.documententry_path(basefile))
        return entry.orig_url

    def find_pdf_links(self, soup, basefile, labels=False):
        pdffiles = []
        docsection = soup.find('ul', 'list--Block--icons')
        pdflink = re.compile("/contentassets/")
        if docsection:
            for link in docsection.find_all("a", href=pdflink):
                pdffiles.append((link["href"], link.string))
        selected = self.select_pdfs(pdffiles, labels)
        if not labels:
            self.log.debug(
                "selected %s out of %d pdf files" %
                (", ".join(selected), len(pdffiles)))
        return selected

    def select_pdfs(self, pdffiles, labels=False):
        """Given a list of (pdffile, linktext) tuples, return only those pdf
        files we need to parse (by filtering out duplicates etc).
        """
        cleanfiles = []

        # 1. Simplest case: One file obviously contains all of the text
        for pdffile, linktext in pdffiles:
            if "hela dokumentet" in linktext or "hela betänkandet" in linktext:
                if labels:
                    pdffile = pdffile, linktext
                return [pdffile]  # we're immediately done

        # 2. Filter out obviously extraneous files
        for pdffile, linktext in pdffiles:
            if (linktext.startswith("Sammanfattning ") or
                    linktext.startswith("Remissammanställning") or
                    linktext.startswith("Sammanställning över remiss") or
                    "remissinstanser" in linktext):
                pass  # don't add to cleanfiles
            else:
                cleanfiles.append((pdffile, linktext))

        # 3. Attempt to see if we have one complete file + several
        # files with split-up content
        linktexts = [x[1] for x in cleanfiles]
        commonprefix = os.path.commonprefix(linktexts)
        if commonprefix:
            for pdffile, linktext in cleanfiles:
                # strip away the last filetype + size paranthesis
                linktext = re.sub(" \(pdf [\d\,]+ [kM]B\)", "", linktext)
                # and if we remove the commonprefix, do we end up with nothing?
                if linktext.replace(commonprefix, "") == "":
                    # then this is probably a complete file
                    if labels:
                        pdffile = pdffile, linktext
                    return [pdffile]

        # 4. Base case: We return it all
        if labels:
            return cleanfiles
        else:
            return [x[0] for x in cleanfiles]

    def parse_pdf(self, pdffile, intermediatedir):
        # By default, don't create and manage PDF backgrounds files
        # (takes forever, we don't use them yet)
        if self.config.compress == "bz2":
            keep_xml = "bz2"
        else:
            keep_xml = True
        pdf = FontmappingPDFReader(filename=pdffile,
                                   workdir=intermediatedir,
                                   images=self.config.pdfimages,
                                   keep_xml=keep_xml)
        if pdf.is_empty():
            self.log.warning("PDF file %s had no textcontent, trying OCR" % pdffile)
            # No use using the FontmappingPDFReader, since OCR:ed
            # files lack the same fonts as that reader can handle.
            pdf = PDFReader(filename=pdffile,
                            workdir=intermediatedir,
                            images=self.config.pdfimages,
                            keep_xml=keep_xml,
                            ocr_lang="swe")

            
        return pdf

    # returns a list of (PDFReader, metrics) tuples, one for each PDF
    def read_pdfs(self, basefile, pdffiles, identifier=None):
        reader = None
        for pdffile in pdffiles:
            basepath = pdffile.split("/")[-1]
            pdf_path = self.store.downloaded_path(basefile,
                                                  attachment=basepath)
            intermed_path = self.store.intermediate_path(basefile,
                                                         attachment=basepath)
            intermediate_dir = os.path.dirname(intermed_path)
            if not reader:
                reader = self.parse_pdf(pdf_path, intermediate_dir)
            else:
                reader += self.parse_pdf(pdf_path, intermediate_dir)
        return reader

    def get_pdf_analyzer(self, reader):
        if self.document_type == self.KOMMITTEDIREKTIV:
            from ferenda.sources.legal.se.direktiv import DirAnalyzer
            analyzer = DirAnalyzer(reader)
        elif self.document_type == self.SOU:
            from ferenda.sources.legal.se.sou import SOUAnalyzer
            analyzer = SOUAnalyzer(reader)
        elif self.document_type == self.PROPOSITION:
            from ferenda.sources.legal.se.propositioner import PropAnalyzer
            analyzer = PropAnalyzer(reader)
        else:
            analyzer = PDFAnalyzer(reader)
        return analyzer

    def create_external_resources(self, doc):
        """Optionally create external files that go together with the
        parsed file (stylesheets, images, etc). """
        if len(doc.body) == 0:
            self.log.warning(
                "%s: No external resources to create", doc.basefile)
            return
        if not isinstance(doc.body, PDFReader):
            # The body is processed enough that we won't need to
            # create a CSS file w/ fontspecs etc
            return
        # Step 1: Create CSS
        # 1.1 find css name
        cssfile = self.store.parsed_path(doc.basefile, attachment='index.css')
        # 1.2 create static CSS
        fp = open(cssfile, "w")
        # 1.3 create css for fontspecs and pages
        # for pdf in doc.body:
        pdf = doc.body
        # this is needed to get fontspecs and other things
        for spec in list(pdf.fontspec.values()):
            fp.write(".fontspec%s {font: %spx %s; color: %s;}\n" %
                     (spec['id'], spec['size'], spec['family'], spec['color']))

        # 2 Copy all created png files to their correct locations
        totcnt = 0
        src_base = os.path.dirname(self.store.intermediate_path(doc.basefile))

        pdf_src_base = src_base + "/" + os.path.splitext(os.path.basename(pdf.filename))[0]

        cnt = 0
        for page in pdf:
            totcnt += 1
            cnt += 1
            # src = "%s%03d.png" % (pdf_src_base, page.number)
            src = "%s%03d.png" % (pdf_src_base, cnt)

            # 4 digits, compound docs can be over 1K pages
            attachment = "%04d.png" % (totcnt)
            dest = self.store.parsed_path(doc.basefile,
                                          attachment=attachment)

            # If running under RepoTester, the source PNG files may not exist.
            if os.path.exists(src):
                if util.copy_if_different(src, dest):
                    self.log.debug("Copied %s to %s" % (src, dest))

            fp.write("#page%03d { background: url('%s');}\n" %
                     (cnt, os.path.basename(dest)))

    def toc_item(self, binding, row):
        return [row['dcterms_identifier'] + ": ",
                Link(row['dcterms_title'],  # yes, ignore binding
                     uri=row['uri'])]

    def toc(self, otherrepos=None):
        self.log.debug(
            "Not generating TOC (let ferenda.sources.legal.se.Forarbeten do that instead")
        return

    def tabs(self):
        if self.config.tabs:
            label = {self.DS: "Ds:ar",
                     self.KOMMITTEDIREKTIV: "Kommittédirektiv",
                     self.PROPOSITION: "Propositioner",
                     self.SOU: "SOU:er"}.get(self.document_type, "Förarbete")
            return [(label, self.dataset_uri())]
        else:
            return []
