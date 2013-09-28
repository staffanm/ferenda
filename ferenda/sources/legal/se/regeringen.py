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
from ferenda import Describer
from ferenda import util
from ferenda.elements import Body
from ferenda.elements import Paragraph
from ferenda.elements import Page
from ferenda.pdfreader import PDFReader
from ferenda.pdfreader import Page
from ferenda.decorators import recordlastdownload, downloadmax
from . import SwedishLegalSource


class Regeringen(SwedishLegalSource, PDFDocumentRepository):
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
        pagecnt = 1
        # existing_cnt = 0
        while not done:
            self.log.info('Result page #%s (%s)' % (pagecnt, url))
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

            pagecnt += 1
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
                    self.log.debug(
                        "%s existed, but a new ver was downloaded" % filename)
                else:
                    self.log.debug(
                        "%s is unchanged -- checking PDF files" % filename)
            else:
                self.log.debug(
                    "%s did not exist, so it was downloaded" % filename)

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
                        "Couldn't find rel_basefile (elementid #%s) among %r" % (elementid, infospans))
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
        pdffiles = self.find_pdf_links(soup, doc.basefile)
        if not pdffiles:
            self.log.error(
                "%s: No PDF documents found, can't parse anything" % doc.basefile)
            return None

        doc.body = self.parse_pdfs(doc.basefile, pdffiles)
        return doc

    def sanitize_identifier(self, identifier):
        return identifier

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

    def parse_pdfs(self, basefile, pdffiles):
        doc = Body()
        for pdffile in pdffiles:
            # FIXME: downloaded_path must be more fully mocked
            # (support attachments) by testutil.RepoTester. In the
            # meantime, we do some path munging ourselves

            pdf_path = self.store.downloaded_path(basefile).replace("index.html", pdffile)
            intermediate_path = self.store.intermediate_path(basefile, attachment=pdffile)
            intermediate_dir = os.path.dirname(intermediate_path)
            try:
                pdf = self.parse_pdf(pdf_path, intermediate_dir)
                for page in pdf:
                    pass
                    # page.crop(left=50,top=0,bottom=900,right=700)
                doc.append(pdf)
            except ValueError:
                (exc_type, exc_value, exc_trackback) = sys.exc_info()
                self.log.warning("Ignoring exception %s (%s), skipping PDF %s" %
                                 (exc_type, exc_value, pdffile))
        return doc

    def parse_pdf(self, pdffile, intermediatedir):
        pdf = PDFReader()
        pdf.read(pdffile, intermediatedir)
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
        for pdf in doc.body:
            assert isinstance(pdf, PDFReader)
            for spec in list(pdf.fontspec.values()):
                fp.write(".fontspec%s {font: %spx %s; color: %s;}\n" %
                         (spec['id'], spec['size'], spec['family'], spec['color']))

        # 2 Copy all created png files to their correct locations
        totcnt = 0
        src_base = os.path.dirname(self.store.intermediate_path(doc.basefile))
        for pdf in doc.body:
            pdf_src_base = src_base + "/" + os.path.splitext(os.path.basename(pdf.filename))[0]

            cnt = 0
            for page in pdf:
                totcnt += 1
                cnt += 1
                src = "%s%03d.png" % (pdf_src_base, page.number)
                # 4 digits, compound docs can be over 1K pages
                attachment = "%04d.png" % (totcnt)
                dest = self.store.parsed_path(doc.basefile,
                                              attachment=attachment)

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
