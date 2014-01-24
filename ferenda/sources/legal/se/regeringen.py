# -*- coding: utf-8 -*-
from __future__ import unicode_literals
# A abstract base class for fetching and parsing documents
# (particularly preparatory works) from regeringen.se
import sys
import os
import re
import codecs
from datetime import datetime
from six import text_type as str
from six.moves.urllib_parse import urljoin

import requests
from bs4 import BeautifulSoup
from rdflib import URIRef
from rdflib import RDFS

from ferenda import PDFDocumentRepository
from ferenda import DocumentEntry
from ferenda import FSMParser
from ferenda import Describer
from ferenda import util
from ferenda.elements import Body, Paragraph, Section, CompoundElement, SectionalElement
from ferenda.pdfreader import PDFReader
from ferenda.pdfreader import Page, Textbox
from ferenda.decorators import recordlastdownload, downloadmax
from . import SwedishLegalSource

class PreambleSection(CompoundElement):
    tagname = "div"
    classname = "preamblesection"
    counter = 0
    uri = None
    def as_xhtml(self, uri):
        if not self.uri:
            self.__class__.counter += 1
            self.uri = uri + "#PS%s" % self.__class__.counter
        element = super(PreambleSection, self).as_xhtml(uri)
        element.set('property', 'dct:title')
        element.set('content', self.title)
        element.set('typeof', 'bibo:DocumentPart')
        return element

class UnorderedSection(CompoundElement):
    tagname = "div"
    classname = "unorderedsection"
    counter = 0
    uri = None
    def as_xhtml(self, uri):
        if not self.uri:
            self.__class__.counter += 1
            # note that this becomes a document-global running counter
            self.uri = uri + "#US%s" % self.__class__.counter
        element = super(UnorderedSection, self).as_xhtml(uri)
        element.set('property', 'dct:title')
        element.set('content', self.title)
        element.set('typeof', 'bibo:DocumentPart')
        return element

class Appendix(SectionalElement): 
    tagname = "div"
    classname = "appendix"
    def as_xhtml(self, uri):
        if not self.uri:
            self.uri = uri + "#B%s" % self.ordinal

        return super(Appendix, self).as_xhtml(uri)

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
    start_url = None
    start_url_template = "http://www.regeringen.se/sb/d/107/a/136/action/showAll/sort/byDate/targetDepartment/archiveDepartment?d=107&action=search&query=&type=advanced&a=136&sort=byDate&docTypes=%(doctype)s"
    downloaded_suffix = ".html"  # override PDFDocumentRepository
    source_encoding = "latin-1"

    @recordlastdownload
    def download(self, basefile=None):
        if basefile:
            return self.download_single(basefile)

        # if self.config.lastdownloaded:
        #     FIXME: use this to create a time-filtered start_url
        start_url = self.start_url_template % {'doctype': self.document_type}
        self.log.info("Starting at %s" % start_url)
        for basefile, url in self.download_get_basefiles(start_url):
            self.download_single(basefile, url)

    @downloadmax
    def download_get_basefiles(self, url):
        done = False
        pagecount = 1
        # existing_cnt = 0
        while not done:
            self.log.info('Result page #%s (%s)' % (pagecount, url))
            resp = requests.get(url)
            mainsoup = BeautifulSoup(resp.text)
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
            if next:
                url = urljoin(url, next['href'])
            else:
                done = True

    def remote_url(self, basefile):
        # do a search to find the proper url for the document
        templ = "http://www.regeringen.se/sb/d/107/a/136?query=s(basefile)&docTypes=s(doctype)&type=advanced&action=search"
        url = templ % {'doctype': self.document_type,
                       'basefile': basefile}
        soup = BeautifulSoup(requests.get(url).text)
        for link in soup.find_all(href=re.compile("/sb/d/108/a/")):
            desc = link.find_next_sibling("span", {'class': 'info'}).text
            if basefile in desc:
                url = urljoin(url, link['href'])
        if not url:
            self.log.error(
                "Could not find document with basefile %s" % basefile)
        return url

    def canonical_uri(self, basefile, document_type=None):
        if not document_type:
            document_type = self.document_type
        seg = {self.KOMMITTEDIREKTIV: "dir",
               self.DS: "utr/ds",
               self.PROPOSITION: "prop",
               self.SKRIVELSE: "skr",
               self.SOU: "utr/sou",
               self.SO: "so"}
        return self.config.url + "res/%s/%s" % (seg[document_type], basefile)

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
        
    def parse_metadata_from_soup(self, soup, doc):
        # print("Regeringen.parse_metadata_from_soup: %s %s (%s)" % (self.__class__.__name__, id(self), len(list(self.config))))
        doc.lang = "sv"
        d = Describer(doc.meta, doc.uri)
        d.rdftype(self.rdf_type)
        d.value(self.ns['prov'].wasGeneratedBy, self.qualified_class_name())
        sameas = self.sameas_uri(doc.uri)
        if sameas:
            d.rel(self.ns['owl'].sameAs, sameas)

        content = soup.find(id="content")
        title = content.find("h1").string
        d.value(self.ns['dct'].title, title, lang=doc.lang)
        identifier = self.sanitize_identifier(
            content.find("p", "lead").text)  # might need fixing up
        d.value(self.ns['dct'].identifier, identifier)

        definitions = content.find("dl", "definitions")
        if definitions:
            for dt in definitions.find_all("dt"):
                key = dt.get_text(strip=True)
                value = dt.find_next_sibling("dd").get_text(strip=True)
                if key == "Utgiven:":
                    try:
                        d.value(self.ns['dct'].published,
                                self.parse_swedish_date(value))
                    except ValueError as e:
                        self.log.warning(
                            "Could not parse %s as swedish date" % value)
                elif key == "Avsändare:":
                    if value.endswith("departementet"):
                        d.rel(self.ns['rpubl'].departement,
                              self.lookup_resource(value))
                    else:
                        d.rel(self.ns['dct'].publisher,
                              self.lookup_resource(value))

        if content.find("h2", text="Sammanfattning"):
            sums = content.find("h2", text="Sammanfattning").find_next_siblings("p")
            # "\n\n" doesn't seem to survive being stuffed in a rdfa
            # content attribute. Replace with simple space.
            summary = " ".join([x.get_text(strip=True) for x in sums])
            d.value(self.ns['dct'].abstract,
                    summary, lang=doc.lang)

        # find related documents
        re_basefile = re.compile(r'\d{4}(|/\d{2,4}):\d+')
        # legStep1=Kommittedirektiv, 2=Utredning, 3=lagrådsremiss,
        # 4=proposition. Assume that relationships between documents
        # are reciprocal (ie if the page for a Kommittedirektiv
        # references a Proposition, the page for that Proposition
        # references the Kommittedirektiv.
        elements = {self.KOMMITTEDIREKTIV: [],
                    self.DS: ["legStep1"],
                    self.PROPOSITION: ["legStep1", "legStep2"],
                    self.SOU: ["legStep1"]}[self.document_type]

        for elementid in elements:
            box = content.find(id=elementid)
            for listitem in box.find_all("li"):
                if not listitem.find("span", "info"):
                    continue
                infospans = [x.text.strip(
                ) for x in listitem.find_all("span", "info")]

                rel_basefile = None
                identifier = None

                for infospan in infospans:
                    if re_basefile.search(infospan):
                        # scrub identifier ("Dir. 2008:50" -> "2008:50" etc)
                        rel_basefile = re_basefile.search(infospan).group()
                        identifier = infospan

                if not rel_basefile:
                    self.log.warning(
                        "%s: Couldn't find rel_basefile (elementid #%s) among %r" % (doc.basefile, elementid, infospans))
                    continue
                if elementid == "legStep1":
                    subjUri = self.canonical_uri(
                        rel_basefile, self.KOMMITTEDIREKTIV)
                elif elementid == "legStep2":
                    if identifier.startswith("SOU"):
                        subjUri = self.canonical_uri(rel_basefile, self.SOU)
                    elif identifier.startswith(("Ds", "DS")):
                        subjUri = self.canonical_uri(rel_basefile, self.DS)
                    else:
                        self.log.warning(
                            "Cannot find out what type of document the linked %s is (#%s)" % (identifier, elementid))
                        self.log.warning("Infospans was %r" % infospans)
                        continue
                elif elementid == "legStep3":
                    subjUri = self.canonical_uri(
                        rel_basefile, self.PROPOSITION)
                d.rel(self.ns['rpubl'].utgarFran, subjUri)

        # find related pages
        related = content.find("h2", text="Relaterat")
        if related:
            for link in related.findParent("div").find_all("a"):
                r = urljoin(
                    "http://www.regeringen.se/", link["href"])
                d.rel(RDFS.seeAlso, URIRef(r))
                # with d.rel(RDFS.seeAlso, URIRef(r)):
                #    d.value(RDFS.label, link.get_text(strip=True))

        self.infer_triples(d, doc.basefile)
        # print doc.meta.serialize(format="turtle")

        # find pdf file names in order

    def parse_document_from_soup(self, soup, doc):
        def _check_differing(describer, predicate, newval):
            if describer.getvalue(predicate) != newval:
                self.log.warning("%s: HTML page: %s is %r, document: it's %r" %
                                 (doc.basefile,
                                  d.graph.qname(predicate),
                                  describer.getvalue(predicate),
                                  newval))
                # remove old val
                d.graph.remove((d._current(),
                                predicate,
                                d.graph.value(d._current(), predicate)))
                d.value(predicate, newval)

        # reset global state
        PreambleSection.counter = 0
        UnorderedSection.counter = 0

        pdffiles = self.find_pdf_links(soup, doc.basefile)
        if not pdffiles:
            self.log.error(
                "%s: No PDF documents found, can't parse anything" % doc.basefile)
            return None
            
        doc.body = self.parse_pdfs(doc.basefile, pdffiles)

        # do some post processing. First loop through leading
        # textboxes and try to find dct:identifier, dct:title and
        # dct:published (these should already be present in doc.meta,
        # but the values in the actual document should take
        # precendence
        d = Describer(doc.meta, doc.uri)
        title_found = False
        for idx, element in enumerate(doc.body):
            if not isinstance(element, Textbox):
                continue
            str_element = str(element).strip()
            # print("examining %s..." % str_element[:40])
            # dct:identifier
            m = self.re_basefile_lax.search(str_element)
            if m:
                _check_differing(d, self.ns['dct'].identifier, "Prop. " + m.group(1))
            # dct:title
            if element.getfont()['size'] == '20' and not title_found:
                # sometimes part of the the dct:identifer (eg " Prop."
                # or " 2013/14:51") gets mixed up in the title
                # textbox. Remove those parts if we can find them.
                if " Prop." in str_element:
                    str_element = str_element.replace(" Prop.", "").strip()
                if self.re_basefile_lax.search(str_element):
                    str_element = self.re_basefile_lax.sub("", str_element)
                _check_differing(d, self.ns['dct'].title, str_element)
                title_found = True
            # dct:published
            if str_element.startswith("Stockholm den"):
                pubdate = self.parse_swedish_date(str_element[13:])
                _check_differing(d, self.ns['dct'].published, pubdate)

        # then maybe look for the section named Författningskommentar
        # (or similar), identify each section and which proposed new
        # regulation it refers to)
        for i, element in enumerate(doc.body):
            if isinstance(element, Section) and (element.title == "Författningskommentar"):
                for j, subsection in enumerate(element):
                    if hasattr(subsection, 'title'):
                        law = subsection.title # well, find out the id (URI) from the title -- possibly using legalref
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
                
        return doc

    def sanitize_identifier(self, identifier):
        try: 
            (doctype, y1, y2, num) = re.split("[\.:/ ]+", identifier)
            return "%s. %s/%s:%s" % (doctype, y1, y2, num)
        except:
            self.log.warning("Couldn't sanitize identifier %s" % identifier)
            return identifer

    def find_pdf_links(self, soup, basefile):
        pdffiles = []
        docsection = soup.find('div', 'doc')
        if docsection:
            for li in docsection.find_all("li", "pdf"):
                link = li.find('a')
                m = re.match(r'/download/(\w+\.pdf).*', link['href'], re.IGNORECASE)
                if not m:
                    continue
                pdfbasefile = m.group(1)
                pdffiles.append(pdfbasefile)
        return pdffiles

    def parse_pdf(self, pdffile, intermediatedir):
        pdf = PDFReader()
        pdf.read(pdffile, intermediatedir)
        return pdf

    # this glues together textboxes in a smart way
    def iter_textboxes(self, pdf):
        textbox = None
        prevbox = None
        for page in pdf:
            yield page # will include all raw textbox objects -- should possibly be shallow-cloned
            for nextbox in page:
                linespacing = int(nextbox.getfont()['size']) / 2
                parindent = int(nextbox.getfont()['size'])
                if (textbox and
                    textbox.getfont()['size'] == nextbox.getfont()['size'] and
                    textbox.getfont()['family'] == nextbox.getfont()['family'] and
                    textbox.top + textbox.height + linespacing > nextbox.top and
                    ((prevbox.top + prevbox.height == nextbox.top + nextbox.height) or # compare baseline, not topline
                     (prevbox.left == nextbox.left) or
                     (parindent * 2 >= (prevbox.left - nextbox.left) >= parindent)
                     )):
                    textbox += nextbox
                else:
                    # print("Yielding new: %s %s" % (repr(textbox), repr(nextbox)))
                    if textbox:
                        yield textbox
                    textbox = nextbox
                prevbox = nextbox
            # before every new page, flush existing textbox (glueing
            # textboxes together across pages is harder bc .top can be
            # anywhere)
            if textbox:
                yield textbox
                textbox = None


    @staticmethod
    def get_parser(basefile="0"):
        # a mutable variable, which is accessible from the nested
        # functions
        state = {'pageno': 0,
                 'appendixno': None}
        metrics = {'footer': 920,
                   'leftmargin': 160,
                   'rightmargin': 628,
                   'headingsize': 20,
                   'subheadingsize': 17,
                   'subsubheadingsize': 15,
                   'textsize': 13}
                   
        def is_pagebreak(parser):
            return isinstance(parser.reader.peek(), Page)
        
        # page numbers, headings.
        def is_nonessential(parser):
            chunk = parser.reader.peek()
            if chunk.top > metrics['footer']:
                return True  # page numbers
            if (int(chunk.getfont()['size']) <= metrics['textsize'] and
                    (chunk.left < metrics['leftmargin'] or chunk.left > metrics['rightmargin']) and
                (15 <= len(str(chunk)) <= 29)): # matches both "Prop. 2013/14:1" and "Prop. 1999/2000:123 Bilaga 12"
                return True
                
        def is_preamblesection(parser):
            chunk = parser.reader.peek()
            txt = str(chunk).strip()
            fontsize = int(chunk.getfont()['size'])
            return (metrics['subheadingsize'] <= fontsize <= metrics['headingsize']
                    and txt in ('Propositionens huvudsakliga innehåll',
                                'Innehållsförteckning'))

        def is_section(parser):
            (ordinal, title) = analyze_sectionstart(parser)
            if ordinal:
                return ordinal.count(".") == 0

        def is_subsection(parser):
            (ordinal, title) = analyze_sectionstart(parser)
            if ordinal:
                return ordinal.count(".") == 1
                
        def is_unorderedsection(parser):
            # Subsections in "Författningskommentar" sections are
            # not always numbered. As a backup, check fontsize as well
            chunk = parser.reader.peek()
            return int(chunk.getfont()['size']) == metrics['subheadingsize']

        def is_subsubsection(parser):
            (ordinal, title) = analyze_sectionstart(parser)
            if ordinal:
                return ordinal.count(".") == 2

        def is_appendix(parser):
            chunk = parser.reader.peek()
            txt = str(chunk).strip()
            if (chunk.getfont()['size'] == metrics['headingsize'] and txt.startswith("Bilaga ")):
                return True
            elif (int(chunk.getfont()['size']) == metrics['textsize'] and
                  (chunk.left < metrics['leftmargin'] or
                   chunk.left > metrics['rightmargin'])):
                if len(chunk) > 1 and chunk[1].startswith("Bilaga "):
                    # note: we need to check wether the appendix
                    # ordinal ISN'T the same as an appendix number
                    # we're already dealing with
                    ordinal = int(re.search("Bilaga (\d)", str(chunk)).group(1))
                    if ordinal != state['appendixno']:
                        return True

        def is_paragraph(parser):
            return True

        def make_body(parser):
            return p.make_children(Body())
        setattr(make_body, 'newstate', 'body')

        def make_paragraph(parser):
            # if "Regeringen beslutade den 8 april 2010 att" in str(parser.reader.peek()):
            #     raise ValueError("OK DONE")
            return parser.reader.next()

        def make_preamblesection(parser):
            s = PreambleSection(title=str(parser.reader.next()).strip())
            if s.title == "Innehållsförteckning":
                parser.make_children(s) # throw away
                return None
            else:
                return parser.make_children(s)
        setattr(make_preamblesection, 'newstate', 'preamblesection')


        def make_unorderedsection(parser):
            s = UnorderedSection(title=str(parser.reader.next()).strip())
            return parser.make_children(s)
        setattr(make_unorderedsection, 'newstate', 'unorderedsection')

        def make_appendix(parser):
            # now, an appendix can begin with either the actual
            # headline-like title, or by the sidenote in the
            # margin. Find out which it is, and plan accordingly.
            done = False
            while not done:
                chunk = parser.reader.next()
                if isinstance(chunk, Page):
                    continue
                m = re.search("Bilaga (\d)", str(chunk))
                if m:
                    state['appendixno'] = int(m.group(1))
                if int(chunk.getfont()['size']) >= metrics['subheadingsize']:
                    done = True
            s = Appendix(title=str(chunk).strip(),
                         ordinal=str(state['appendixno']),
                         uri=None)
            return parser.make_children(s)
        setattr(make_appendix, 'newstate', 'appendix')

        # this is used for subsections and subsubsections as well --
        # probably wont work due to the newstate property
        def make_section(parser):
            ordinal, title = analyze_sectionstart(parser, parser.reader.next())
            if ordinal:
                identifier = "Prop. %s, avsnitt %s" % (basefile, ordinal)
                s = Section(ordinal=ordinal, title=title)
            else:
                s = Section(title=str(title))
            return parser.make_children(s)
        setattr(make_section, 'newstate', 'section')

        def skip_nonessential(parser):
            parser.reader.next()
            return None

        def skip_pagebreak(parser):
            # increment pageno
            state['pageno'] += 1
            parser.reader.next()
            return None
            
        re_sectionstart = re.compile("^(\d[\.\d]*) +(.*[^\.])$").match
        def analyze_sectionstart(parser, textbox=None):
            if not textbox:
                textbox = parser.reader.peek()
            if not (metrics['headingsize'] >= int(textbox.getfont()['size']) >= metrics['subsubheadingsize']):
                return (None, textbox)
            txt = str(textbox)
            m = re_sectionstart(txt)
            if m:
                ordinal = m.group(1).rstrip(".")
                title = m.group(2)
                return (ordinal, title.strip())
            else:
                return (None, textbox)

        p = FSMParser()

        p.set_recognizers(is_pagebreak,
                          is_appendix,
                          is_nonessential,
                          is_section,
                          is_subsection,
                          is_subsubsection,
                          is_preamblesection,
                          is_unorderedsection,
                          is_paragraph)
        commonstates = ("body","preamblesection","section", "subsection", "unorderedsection", "subsubsection", "appendix")
        p.set_transitions({(commonstates, is_nonessential): (skip_nonessential, None),
                           (commonstates, is_pagebreak): (skip_pagebreak, None),
                           (commonstates, is_unorderedsection): (make_unorderedsection, None),
                           (commonstates, is_paragraph): (make_paragraph, None),
                           ("body", is_preamblesection): (make_preamblesection, "preamblesection"),
                           ("preamblesection", is_preamblesection): (False, None),
                           ("preamblesection", is_section): (False, None),
                           ("body", is_section): (make_section, "section"),
                           ("section", is_section): (False, None),
                           ("section", is_subsection): (make_section, "subsection"),
#                           ("section", is_unorderedsection): (make_unorderedsection, "unorderedsection"), # covered by commonstates transtions
                           ("unorderedsection", is_section): (False, None),
                           ("unorderedsection", is_appendix): (False, None),
                           ("subsection", is_subsection): (False, None),
                           ("subsection", is_section): (False, None),
                           ("subsection", is_subsubsection): (make_section, "subsubsection"),
                           ("subsubsection", is_subsubsection): (False, None),
                           ("subsubsection", is_subsection): (False, None),
                           ("subsubsection", is_section): (False, None),
                           ("body", is_appendix): (make_appendix, "appendix"),
                           (("appendix","subsubsection", "subsection", "section"), is_appendix):
                           (False, None)
                           })

        p.initial_state = "body"
        p.initial_constructor = make_body

        return p
                
    def parse_pdfs(self, basefile, pdffiles):
        
        body = None

        for pdffile in pdffiles:
            pdf_path = self.store.downloaded_path(basefile, attachment=pdffile)
            intermediate_path = self.store.intermediate_path(basefile, attachment=pdffile)
            intermediate_dir = os.path.dirname(intermediate_path)
            # case 1: intermediate path does not exist and that's ok
            # case 2: intermediate path exists alongside downloaded_path
            pdf = self.parse_pdf(pdf_path, intermediate_dir)

            debug = False
            if debug:
                # test code - draw a rectangle around every textbox
                from PyPDF2 import PdfFileWriter, PdfFileReader
                import StringIO
                from reportlab.pdfgen import canvas
                packet = None
                output = PdfFileWriter()
                existing_pdf = PdfFileReader(open(pdf_path, "rb"))
                pageidx = 0
                sf = 2/3.0 # scaling factor
                dirty = False
                for tb in self.iter_textboxes(pdf):
                    if isinstance(tb, Page):
                        if dirty:
                            can.save()
                            packet.seek(0)
                            new_pdf = PdfFileReader(packet)
                            print("Getting page %s from existing pdf" % pageidx)
                            page = existing_pdf.getPage(pageidx)
                            page.mergePage(new_pdf.getPage(0))
                            output.addPage(page)
                            pageidx += 1
                        pagesize = (tb.width*sf, tb.height*sf)
                        # print("pagesize %s x %s" % pagesize)
                        packet = StringIO.StringIO()
                        can = canvas.Canvas(packet, pagesize=pagesize,
                                            bottomup=False)
                        can.setStrokeColorRGB(0.2,0.5,0.3)
                        can.translate(0,0)
                    else:
                        dirty = True
                        x = repr(tb)
                        print(x)
                        can.rect(tb.left*sf, tb.top*sf,
                                 tb.width*sf, tb.height*sf)

                packet.seek(0)
                can.save()
                new_pdf = PdfFileReader(packet)
                print("Getting last page %s from existing pdf" % pageidx)
                page = existing_pdf.getPage(pageidx)
                page.mergePage(new_pdf.getPage(0))
                output.addPage(page)

                outputfile = pdf_path+".marked.pdf"
                outputStream = open(outputfile, "wb")
                output.write(outputStream)
                outputStream.close()
                print("wrote %s" % outputfile)
                return pdf
            else: # not debug
                # FIXME: we should probably initialize the parser with
                # dct:identifier instead of doc.basefile
                parser = self.get_parser(basefile)
                if hasattr(self.config, 'debug'):
                    parser.debug = self.config.debug 
                body = parser.parse(self.iter_textboxes(pdf))
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
        # Step 1: Create CSS
        # 1.1 find css name
        cssfile = self.store.parsed_path(doc.basefile, attachment='index.css')
        # 1.2 create static CSS
        fp = open(cssfile, "w")
        # 1.3 create css for fontspecs and pages
        # for pdf in doc.body:
        pdf = doc.body
        assert isinstance(pdf, PDFReader) # this is needed to get fontspecs and other things
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

    # Not used right now
    def parse_pdf_complex(self, pdffile, intermediatedir):
        pdf = PDFReader()
        pdf.read(pdffile, intermediatedir)
        res = CompoundElement
        cnt = 0
        for srcpage in pdf:
            cnt += 1
            # Page is a wonderful and magical class. Read the comments
            # to find out exactly how awesome it is.
            tgtpage = Page(ordinal=cnt)
            # TODO: use magic to find the bounding box of actual page
            # content. 510 is a rough cutoff that might not be
            # appropriate for all page layouts.
            boxes = srcpage.boundingbox(right=510)
            for box in boxes:
                print((box.getfont()))
                print(("    [%dx%d][%dx%d][%s@%s] %s" %
                      (box.top, box.left, box.bottom, box.right, box.getfont()['family'], box.getfont()['size'], str(box))))
                # Heuristic: If something is in large type, it's a heading.
                if int(box.getfont()['size']) > 12:
                    if isinstance(ctx, Heading):
                        if vertical_space(box, boxes.previous()) > 10:
                            # Page.new closes the current context and
                            # creates a new context of the given class
                            tgtpage.new(Heading)

                    # Heading is a DimensionedElement with top,
                    # left, width, height props. Page.set creates a new
                    # context, but only if needed.
                    txtpage.set(Heading)

                    # calls the current context's append() method. If
                    # it's a DimensionedElement (it should be), it's
                    # implementation of append() expands the bounding
                    # box as new stuff is added (provided they have
                    # top/left+width/height attribs
                    txtpage.write(box)

                    continue

                # add more heuristicts here...

                # Last resort: Everything that is not something else is a Paragraph
                page.set(Paragraph)
                if horizontal_diff(box, boxes.previous()) > 0:  # maybe something like 4-5
                    page.new(Paragraph)
                if vertical_space(box.boxes.previous()) > 5:
                    page.new(Paragraph)

        print((pdf.median_box_width(threshold=0)))

    def find_resource(self, label):
        # a number of possible implementation (in order of increasing
        # coolness and effort)
        #
        # 1) Mangle resourcelabel into a URI
        # 2) Lookup resourcelabel from a n3 file
        # 3) Lookup resourcelabel from SPARQL db

        # 1)
        # label = label.replace(u"å","aa").replace(u"ä","ae").replace(u"ö","oe")
        # return "http://rinfo.lagrummet.se/org/%s" % label.lower()

        # 2)
        return self.lookup_resource(label)

    @classmethod
    def tabs(cls, primary=False):
        return [['Förarbeten', '/forarb/']]
