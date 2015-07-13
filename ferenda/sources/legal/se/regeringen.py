# -*- coding: utf-8 -*-
from __future__ import unicode_literals
# A abstract base class for fetching and parsing documents
# (particularly preparatory works) from regeringen.se
import sys
import os
import re
import codecs
import json
from operator import itemgetter
from datetime import datetime, timedelta, date
from time import sleep
from six import text_type as str
from six.moves.urllib_parse import urljoin, urlencode

import requests
import lxml.html
from bs4 import BeautifulSoup
from rdflib import URIRef
from rdflib.namespace import SKOS

from ferenda import Describer
from ferenda import DocumentEntry
from ferenda import PDFAnalyzer
from ferenda import util
from ferenda.decorators import recordlastdownload, downloadmax, action, managedparsing
from ferenda.elements import Section, Link
from ferenda.pdfreader import PDFReader, Textbox

from . import SwedishLegalSource, RPUBL
from .swedishlegalsource import offtryck_parser, offtryck_gluefunc, PreambleSection, UnorderedSection


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

    def _parse_xml(self, xmlfp, xmlfilename):
        super(FontmappingPDFReader, self)._parse_xml(xmlfp, xmlfilename)
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
    DS = 1
    KOMMITTEDIREKTIV = 2
    LAGRADSREMISS = 3
    PRESSMEDDELANDE = 4
    PROMEMORIA = 5
    PROPOSITION = 6
    MINISTERRADSMOTE = 7
    SKRIVELSE = 8
    SOU = 9
    SO = 10
    WEBBUTSANDNING = 11
    OVRIGA = 12

    document_type = None  # subclasses must override
    start_url = "http://www.regeringen.se/sb/d/107/a/136"
    downloaded_suffix = ".html"  # override PDFDocumentRepository
    source_encoding = "latin-1"
    storage_policy = "dir"
    alias = "regeringen"
    xslt_template = "res/xsl/forarbete.xsl"

    session = None

    @recordlastdownload
    def download(self, basefile=None):
        if basefile:
            return self.download_single(basefile)

        self.session = requests.session()
        resp = self.session.get(self.start_url)
        searchpage = lxml.html.fromstring(resp.content)
        searchpage.make_links_absolute(self.start_url)
        # try to find the form we want -- might just as well use
        # .forms[1]
        for form in searchpage.forms:
            if form.get('id') == "advancedSearch":
                break
        today = datetime.today()

        if 'lastdownload' in self.config and not self.config.refresh:
            last = self.config.lastdownload - timedelta(days=1)
            self.log.debug("Only downloading documents published on or after %s"
                           % last)
            form.fields['dateRange'] = '4'  # specify date interval
            form.fields['dateRangeFromDay'] = str(last.day)
            form.fields['dateRangeFromMonth'] = str(last.month)
            # the dateRange{From,To}Year is a select list where the
            # year 2000 is value 1, 2001 is 2 and so on (ie a offset
            # of 1999). Therefore we subtract the offset from our
            # initial starting year to arrive at the proper value
            form.fields['dateRangeFromYear'] = str(last.year - 1999)
        form.fields['docTypes'] = [str(self.document_type)]
        params = urlencode(form.form_values())

        searchurl = form.action + "?" + params
        self.log.info("Searching using %s" % searchurl)
        # this'll take us to an intermediate page (showing results
        # from both HTML pages and the "document database") -- we
        # select the link that only gives us documents
        resp = self.session.get(searchurl)
        intermediatepage = lxml.html.fromstring(resp.content)
        intermediatepage.make_links_absolute(searchurl)
        realstarturl = searchurl
        for elem, attrib, value, foo in intermediatepage.iterlinks():
            if elem.get('class') == 'more' and 'publikationer' in elem.text:
                realstarturl = elem.get('href')
        for basefile, url in self.download_get_basefiles(realstarturl):
            self.download_single(basefile, url)

    @downloadmax
    def download_get_basefiles(self, url):
        done = False
        pagecount = 1
        while not done:
            self.log.info('Result page #%s (%s)' % (pagecount, url))

            # sometimes the search service returns a blank page when
            # it shouldn't.
            tries = 5
            while tries:
                resp = self.session.get(url)
                # FIXME: this uses BeautifulSoup while the main download()
                # uses lxml.html -- this is inconsistent.
                mainsoup = BeautifulSoup(resp.text)
                # check if there is any text (there should always be)
                if mainsoup.find(id="body").get_text().strip():
                    tries = 0  # ok we have good mainsoup now
                else:
                    self.log.warning(
                        'Result page #%s was blank, waiting and retrying' %
                        pagecount)
                    tries -= 1
                    if tries:
                        sleep(5 - tries)

            for link in mainsoup.find_all(href=re.compile("/sb/d/108/a/")):
                desc = link.find_next_sibling("span", "info").get_text(strip=True)
                tmpurl = urljoin(url, link['href'])

                # use a strict regex first, then a more forgiving
                m = self.re_basefile_strict.search(desc)
                if not m:
                    m = self.re_basefile_lax.search(desc)
                    if not m:
                        self.log.warning(
                            "Can't find Document ID from %s, forced to download doc page" % desc)
                        resp = requests.get(tmpurl)
                        subsoup = BeautifulSoup(resp.text)

                        for a in subsoup.find("div", "doc").find_all("li", "pdf"):
                            text = a.get_text(strip=True)
                            m = self.re_basefile_lax.search(text)
                            if m:
                                break
                        else:
                            self.log.error("Cannot possibly find docid for %s" % tmpurl)
                            continue
                    else:
                        self.log.warning(
                            "%s (%s) not using preferred form: '%s'" % (m.group(1), tmpurl, m.group(0)))
                basefile = m.group(1)

                # Extra checking -- sometimes ids like 2003/2004:45
                # are used (should be 2003/04:45)
                if (":" in basefile and "/" in basefile):
                    (y1, y2, o) = re.split("[:/]", basefile)
                    # 1999/2000:45 is a special case
                    if len(y2) == 4 and y1 != "1999":
                        self.log.warning(
                            "%s (%s) using incorrect year format, should be '%s/%s:%s'" %
                            (basefile, tmpurl, y1, y2[2:], o))
                        basefile = "%s/%s:%s" % (y1, y2[2:], o)

                yield basefile, urljoin(url, link['href'])

            pagecount += 1
            next = mainsoup.find("a", text="Nästa sida")
            # next = mainsoup.find("a", text=str(pagecount))
            if next:
                url = urljoin(url, next['href'])
            else:
                done = True

    def remote_url(self, basefile):
        # do a search to find the proper url for the document
        templ = "http://www.regeringen.se/sb/d/107/a/136?query=%(basefile)s&docTypes=%(doctype)s&type=advanced&action=search"
        searchurl = templ % {'doctype': self.document_type,
                             'basefile': basefile}
        soup = BeautifulSoup(requests.get(searchurl).text)
        docurl = None
        for link in soup.find_all(href=re.compile("/sb/d/108/a/")):
            desc = link.find_next_sibling("span", {'class': 'info'}).text
            if basefile in desc:
                docurl = urljoin(searchurl, link['href'])
        if not docurl:
            self.log.error(
                "Could not find document with basefile %s" % basefile)
        return docurl

#    def canonical_uri(self, basefile, document_type=None):
#        # basefile eg 2014:158 => [] a rpubl:For ; rpubl:arsutgava "2014"; rpubl:lopnummer "158";
#        if not document_type:
#            document_type = self.document_type
#        seg = {self.KOMMITTEDIREKTIV: "dir",
#               self.DS: "utr/ds",
#               self.PROPOSITION: "prop",
#               self.SKRIVELSE: "skr",
#               self.SOU: "utr/sou",
#               self.SO: "so"}
#        return self.config.url + "res/%s/%s" % (seg[document_type], basefile)

    def basefile_from_uri(self, uri):
        # make sure this function is the reverse of the canonical_uri
        # *in our subrepos* by special-handling the sou/ds cases
        if "utr/ds" in uri or "utr/sou" in uri:
            uri = uri.replace("/utr/", "/")
        return super(Regeringen, self).basefile_from_uri(uri)

    def download_single(self, basefile, url=None):
        if not url:
            url = self.remote_url(basefile)
            if not url:  # remote_url failed
                return
        filename = self.store.downloaded_path(basefile)  # just the html page
        updated = pdfupdated = False
        created = not os.path.exists
        if (not os.path.exists(filename) or self.config.force):
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

            soup = BeautifulSoup(codecs.open(filename, encoding=self.source_encoding))
            cnt = 0
            pdffiles = self.find_pdf_links(soup, basefile)
            if pdffiles:
                for pdffile in pdffiles:
                    # note; the pdfurl goes to a redirect script; however that
                    # part of the URL tree (/download/*) is off-limits for
                    # robots. But we can figure out the actual URL anyway!
                    if len(docid) > 4:
                        path = "c6/%02d/%s/%s" % (
                            int(docid[:-4]), docid[-4:-2], docid[-2:])
                    else:
                        path = "c4/%02d/%s" % (int(docid[:-2]), docid[-2:])
                    pdfurl = "http://www.regeringen.se/content/1/%s/%s" % (
                        path, pdffile)
                    pdffilename = self.store.downloaded_path(basefile, attachment=pdffile)
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
            self.log.debug("%s already exists" % (filename))

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

    def extract_head(self, fp, basefile):
        parser = 'lxml'
        soup = BeautifulSoup(fp.read(), parser)
        self._rawbody = soup.body
        return self._rawbody.find(id="content")

    def extract_metadata(self, rawhead, basefile):
        content = rawhead
        title = list(content.find("h1").children)[0].string
        identifier = content.find("span", "h1-vignette").text
        # the <time> element has a datetime attrib with
        # machine-readable timestamp, but this has unwarranted high
        # precision. We'd like to capture just a xsd:YearMonth if
        # "Publicerad: April 1994", not a datetime(1994, 4, 1, 0, 0,
        # 0). So we just grab text and use parse_swedish_date later
        # on.
        utgiven = content.find("span", "published").time.text
        ansvarig = content.find("p", "media--publikations__sender").a.text
        s = content.find("div", "has-wordExplanation")
        for a in s.find_all("a"):  # links in summary are extra tacked-on bogus
            a.decompose()
        sammanfattning = " ".join(s.strings)

        # look for related items:
        linkfrags = {self.KOMMITTEDIREKTIV: [],
                     self.DS: ["/komittedirektiv/"],
                     self.PROPOSITION: ["/kommittedirektiv/",
                                        "/departementsserien-och-promemorior/",
                                        "/statens-offentliga-utredningar"],
                    self.SOU: ["/kommittedirektiv/"]
        }[self.document_type]

        utgarFran = []
        island = content.find("div", "island")
        for linkfrag in linkfrags:
            regex = re.compile(".*"+linkfrag)
            for link in island.find_all("a", href=regex):
                # from a relative link on the form
                # "/rattsdokument/kommittedirektiv/2012/01/dir.-20123/",
                # extract
                # {'rdf:type': RPUBL.Kommittedirektiv,
                #  'rpubl:arsutgava': 2012,
                #  'rpubl:lopnummer} -> attributes_to_resource -> coin_uri
                (doctype, year, ordinal) = re.search("/(\w+)\.?-(\d{4})(\d+)/$",
                                                 link["href"]).groups()
                attribs = {"rpubl:arsutgava": year,
                           "rpubl:lopnummer": ordinal}
                if doctype == "dir":
                    attribs["rdf:type"] = RPUBL.Kommittedirektiv
                else:
                    attribs["rdf:type"] = RPUBL.Utredningsbetankande
                    # lookup on skos:altLabel, but with "Ds" and "SOU"
                    # as keys (not "ds" and "sou")
                    altlabel = doctype.upper() if doctype == "sou" else doctype.capitalize()
                    attribs["rpubl:utrSerie"] = self.lookup_resource(altlabel, SKOS.altLabel)
                uri = self.minter.space.coin_uri(self.attributes_to_resource(attribs, for_self=False))
                utgarFran.append(uri)
        return {'dcterms:title': title,
                'dcterms:identifier': identifier,
                'dcterms:issued': utgiven,
                'dcterms:abstract': sammanfattning,
                'rpubl:utgarFran': utgarFran,
                'rpubl:departement': ansvarig
        }

    def sanitize_metadata(self, a, basefile):
        # trim space
        for k in ("dcterms:title", "dcterms:abstract"):
            a[k] = util.normalize_space(a[k])
        # trim identifier
        a["dcterms:identifier"] = a["dcterms:identifier"].replace("ID-nummer: ", "")
        # save for later
        self._identifier = a["dcterms:identifier"]
        a["rpubl:departement"] = self.lookup_resource(a["rpubl:departement"])
        # remove empty utgarFran list
        if a["rpubl:utgarFran"]:
            a["rpubl:utgarFran"] = [URIRef(x) for x in a["rpubl:utgarFran"]]
        else:
            del a["rpubl:utgarFran"]
        a["rdf:type"] = self.rdf_type
        # split basefile into rpubl:arsutgava + rpubl:lopnummer
        a["rpubl:arsutgava"], a["rpubl:lopnummer"] = basefile.split(":")

        # FIXME: possibly derive utrSerie from self.document_type?
        return a

    # FIXME: This is hackish -- need to rethink which parts of the
    # parse steps needs access to which data
    def polish_metadata(self, sane_attribs):
        resource = super(Regeringen, self).polish_metadata(sane_attribs)
        self._resource = resource
        return resource

    def sanitize_body(self, rawbody):
        return super(Regeringen, self).sanitize_body(rawbody)

    def get_parser(self, basefile, sanitized_body):
        if self.document_type == self.PROPOSITION:
            preset = 'proposition'
        elif self.document_type == self.SOU:
            preset = 'sou'
        elif self.document_type == self.DS:
            preset = 'ds'
        elif self.document_type == self.KOMMITTEDIREKTIV:
            preset = 'dir'
        else:
            preset = 'default'
        parser = offtryck_parser(metrics=sanitized_body.metrics, preset=preset)
        parser.debug = os.environ.get('FERENDA_FSMDEBUG', False)
        parser.current_identifier = self._identifier
        return parser.parse

    def tokenize(self, pdfreader):
        return pdfreader.textboxes(offtryck_gluefunc, pageobjects=True)
    
    def extract_body(self, fp, basefile):
        # reset global state
        PreambleSection.counter = 0
        UnorderedSection.counter = 0
        pdffiles = self.find_pdf_links(self._rawbody, basefile)
        if not pdffiles:
            self.log.error(
                "%s: No PDF documents found, can't parse anything" % basefile)
            return None
        
        # read a list of pdf files and return a contatenated PDFReader
        # object (where do we put metrics? On the PDFReader itself?
        return self.read_pdfs(basefile, pdffiles, self._identifier)

    parse_types = []

    # This could theoretically be written as visitor functions, but
    # since the code requires access to doc.meta and just iterates
    # over top-level elements of doc.body (no recursing) it's easier
    # to just override parse_body and do our postprocessing at the
    # end.
    def parse_body(self, fp, basefile):

        def _check_differing(describer, predicate, newval):
            if describer.getvalue(predicate) != newval:
                self.log.warning("%s: HTML page: %s is %r, document: it's %r" %
                                 (basefile,
                                  d.graph.qname(predicate),
                                  describer.getvalue(predicate),
                                  newval))
                # remove old val
                d.graph.remove((d._current(),
                                predicate,
                                d.graph.value(d._current(), predicate)))
                d.value(predicate, newval)

        body = super(Regeringen, self).parse_body(fp, basefile)

        # loop through leading  textboxes and try to find dcterms:identifier,
        # dcterms:title and dcterms:issued (these should already be present
        # in doc.meta, but the values in the actual document should take
        # precendence
        d = Describer(self._resource.graph, self._resource.identifier)
        title_found = identifier_found = issued_found = False
        for idx, element in enumerate(body):
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
                pubdate = self.parse_swedish_date(str_element[13:])
                _check_differing(d, self.ns['dcterms'].issued, pubdate)
                issued_found = True

            if title_found and identifier_found and issued_found:
                break
        return body


    # FIXME: Hook this up as a visitor function
    def visit_find_commentary(self, node, state):
        # Look for a section named Författningskommentar
        # (or similar), identify each section and which proposed new
        # regulation it refers to)
        for i, element in enumerate(node):
            if isinstance(element, Section) and (element.title == "Författningskommentar"):
                for j, subsection in enumerate(element):
                    if hasattr(subsection, 'title'):
                        # well, find out the id (URI) from the title -- possibly using
                        # legalref
                        law = subsection.title
                        for k, p in enumerate(subsection):
                            # find out individual paragraphs, create uris for
                            # them, and annotate the first textbox that might
                            # contain commentary (ideally, identify set of
                            # textboxes that comment on a particular
                            # identifiable section and wrap them in a
                            # CommentaryOn container)
                            pass
                            # print("%s,%s,%s: %s" % (i,j,k,repr(p)))
        # then maybe look for inline references ("Övervägandena finns
        # i avsnitt 5.1 och 6" using CitationParser)

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

    def find_pdf_links(self, soup, basefile):
        pdffiles = []
        docsection = soup.find('ul', 'list--Block--icons')
        pdflink = re.compile("/contentassets/")
        if docsection:
            for link in docsection.find_all("a", href=pdflink):
                pdffiles.append((link["href"] + ".pdf", link.string))
        selected = self.select_pdfs(pdffiles)
        self.log.debug(
            "selected %s out of %d pdf files" %
            (", ".join(selected), len(pdffiles)))
        return selected

    def select_pdfs(self, pdffiles):
        """Given a list of (pdffile, linktext) tuples, return only those pdf
        files we need to parse (by filtering out duplicates etc).
        """
        cleanfiles = []

        # 1. Simplest case: One file obviously contains all of the text
        for pdffile, linktext in pdffiles:
            if "hela dokumentet" in linktext or "hela betänkandet" in linktext:
                return [pdffile]  # we're immediately done

        # 2. Filter out obviously extraneous files
        for pdffile, linktext in pdffiles:
            if "hela dokumentet" in linktext or "hela betänkandet" in linktext:
                return [pdffile]  # we're immediately done
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
                    return [pdffile]

        # 4. Base case: We return it all
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
        return pdf

    # returns a list of (PDFReader, metrics) tuples, one for each PDF
    # file.
    def read_pdfs(self, basefile, pdffiles, identifier=None):
        metrics_path = self.store.intermediate_path(basefile,
                                                    attachment="metrics.json")
        pdfdebug_path = self.store.intermediate_path(basefile,
                                                     attachment="debug.pdf")
        if os.environ.get("FERENDA_PLOTANALYSIS"):
            plot_path = self.store.intermediate_path(basefile,
                                                    attachment="plot.png")
        else:
            plot_path = None

        reader = None
        for pdffile in pdffiles:
            basepath = pdffile.split("/")[-1]
            pdf_path = self.store.downloaded_path(basefile, attachment=basepath)
            intermediate_path = self.store.intermediate_path(basefile, attachment=basepath)
            intermediate_dir = os.path.dirname(intermediate_path)
            if not reader:
                reader = self.parse_pdf(pdf_path, intermediate_dir)
            else:
                # FIXME: PDF Reader object must be able to be combined
                # (implement __iadd__)
                reader += self.parse_pdf(pdf_path, intermediate_dir)
            
        # Grab correct analyzer class
        if self.document_type == self.KOMMITTEDIREKTIV:
            from ferenda.sources.legal.se.direktiv import DirAnalyzer
            analyzer = DirAnalyzer(reader)
        elif self.document_type == self.SOU:
            from ferenda.sources.legal.se.sou import SOUAnalyzer
            analyzer = SOUAnalyzer(reader)
        else:
            analyzer = PDFAnalyzer(reader)

        metrics = analyzer.metrics(metrics_path, plot_path, force=self.config.force)
        if os.environ.get("FERENDA_DEBUGANALYSIS"):
            analyzer.drawboxes(pdfdebug_path, offtryck_gluefunc, metrics=metrics)
        reader.metrics = metrics
        return reader
            
    def parse_pdfs(self, basefile, pdffiles, identifier=None):
        body = None
        gluefunc = offtryck_gluefunc
        for pdffile in pdffiles:
            pdf_path = self.store.downloaded_path(basefile, attachment=pdffile)
            intermediate_path = self.store.intermediate_path(basefile, attachment=pdffile)
            intermediate_dir = os.path.dirname(intermediate_path)
            # case 1: intermediate path does not exist and that's ok
            # case 2: intermediate path exists alongside downloaded_path
            pdf = self.parse_pdf(pdf_path, intermediate_dir)

            metrics_path = self.store.intermediate_path(basefile,
                                                        attachment=os.path.splitext(os.path.basename(pdf_path))[0] + ".metrics.json")
            if os.environ.get("FERENDA_PLOTANALYSIS"):
                plot_path = metrics_path.replace(".metrics.json", ".plot.png")
            else:
                plot_path = None
            pdfdebug_path = metrics_path.replace(".metrics.json", ".debug.pdf")
            # 1. Grab correct analyzer class
            if self.document_type == self.KOMMITTEDIREKTIV:
                from ferenda.sources.legal.se.direktiv import DirAnalyzer
                analyzer = DirAnalyzer(pdf)
            elif self.document_type == self.SOU:
                from ferenda.sources.legal.se.sou import SOUAnalyzer
                analyzer = SOUAnalyzer(pdf)
            else:
                analyzer = PDFAnalyzer(pdf)

            metrics = analyzer.metrics(metrics_path, plot_path, force=self.config.force)
            if os.environ.get("FERENDA_DEBUGANALYSIS"):
                analyzer.drawboxes(pdfdebug_path, offtryck_gluefunc, metrics=metrics)
            # metrics = json.loads(util.readfile(metrics_path))

            if self.document_type == self.PROPOSITION:
                preset = 'proposition'
            elif self.document_type == self.SOU:
                preset = 'sou'
            elif self.document_type == self.DS:
                preset = 'ds'
            elif self.document_type == self.KOMMITTEDIREKTIV:
                preset = 'dir'
            else:
                preset = 'default'
            parser = offtryck_parser(metrics=metrics, preset=preset)
            parser.debug = os.environ.get('FERENDA_FSMDEBUG', False)
            parser.current_identifier = identifier
            tbs = list(pdf.textboxes(gluefunc, pageobjects=True))
            body = parser.parse(tbs)
            pdf[:] = body[:]
            pdf.tagname = "body"
        return pdf

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

    def tabs(self, primary=False):
        if self.config.tabs:
            label = {self.DS: "Ds:ar",
                     self.KOMMITTEDIREKTIV: "Kommittédirektiv",
                     self.PROPOSITION: "Propositioner",
                     self.SOU: "SOU:er"}.get(self.document_type, "Förarbete")
            return [(label, self.dataset_uri())]
        else:
            return []
