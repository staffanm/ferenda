#!/usr/bin/env python
# -*- coding: iso-8859-1 -*-
#
# A abstract base class for fetching and parsing documents
# (particularly preparatory works) from regeringen.se
import sys
import os
import re
import datetime
import logging
import datetime
from tempfile import mktemp

from bs4 import BeautifulSoup
from rdflib import Literal, Namespace, URIRef, RDF, RDFS, Graph

from ferenda import DocumentRepository, PDFDocumentRepository, CompositeRepository, Document
from ferenda import util
from ferenda.elements import CompoundElement, Paragraph, Link, Page
from ferenda.pdfreader import PDFReader, Page, Textbox, Textelement
from ferenda.describer import Describer
from . import SwedishLegalSource


class Regeringen(SwedishLegalSource, PDFDocumentRepository):
    KOMMITTEDIREKTIV = 1
    DS = 2
    PROPOSITION = 3
    SKRIVELSE = 4
    SOU = 5
    SO = 6

    start_url = "http://regeringen.se/sb/d/108"
    downloaded_suffix = ".html"
    #xslt_template = "paged.xsl"
    #storage_policy = "dir"

#    @classmethod
#    def basefile_from_path(cls,path):
#        seg = path.split(os.sep)
#        seg = seg[seg.index(cls.module_dir)+2:-1]
#        seg = [x.replace("-","/") for x in seg]
#        # print len(seg)
#        assert 2 <= len(seg) <= 3, "list of segments is too long or too short"
#        # print "path: %s, seg: %r, basefile: %s" % (path,seg,":".join(seg))
#        return ":".join(seg)

    def get_globals(self):
        return globals()

    def parse_basefile(self, basefile):
        soup = self.soup_from_basefile(basefile, self.source_encoding)
        doc = self.parse_from_soup(soup, basefile)
        return doc

    def downloaded_attachment_path(self, basefile, attachment):
        return self.generic_path(basefile + "#" + attachment, 'downloaded', "")

    def download(self):
        refresh = self.get_moduleconfig('refresh', bool, False)

        assert self.document_type is not None
        self.log.info("Starting at %s" % self.start_url)
        self.browser.open(self.start_url)

        # Find the correct form on the page
        for f in self.browser.forms():
            if f.action.endswith("/sb/d/108"):
                self.browser.form = f

        self.browser["contentTypes"] = [str(self.document_type)]
        self.browser.submit()
        done = False
        pagecnt = 1
        existing_cnt = 0
        while not done:
            self.log.info(
                'Result page #%s (%s)' % (pagecnt, self.browser.geturl()))
            mainsoup = BeautifulSoup.BeautifulSoup(self.browser.response())
            for link in mainsoup.findAll(href=re.compile("/sb/d/108/a/")):
                desc = link.findNextSibling(
                    "span", {'class': 'info'}).contents[0]
                tmpurl = urllib.parse.urljoin(
                    self.browser.geturl(), link['href'])

                # use a strict regex first, then a more forgiving
                m = self.re_basefile_strict.search(desc)
                if not m:
                    m = self.re_basefile_lax.search(desc)
                    if not m:
                        # FIXME: Maybe download the document and
                        # see if it contains a DocID?
                        self.log.warning("Can't find Document ID from %s, forced to download doc page" % desc)
                        self.browser.open(tmpurl)
                        subsoup = BeautifulSoup.BeautifulSoup(
                            self.browser.response())
                        self.browser.back()

                        for a in subsoup.find("div", "doc").findAll("li", "pdf"):
                            text = util.element_text(a)
                            m = self.re_basefile_lax.search(text)
                            if m:
                                # self.log.info("Yay i founded it (%s)" % m.group(1))
                                break
                        else:
                            self.log.error(
                                "Cannot possibly find docid for %s" % tmpurl)
                            continue
                    else:
                        self.log.warning("%s (%s) not using preferred form: '%s'" %
                                         (m.group(1), tmpurl, m.group(0)))
                basefile = m.group(1)

                # Extra checking -- sometimes ids like 2003/2004:45
                # are used (should be 2003/04:45)
                if (":" in basefile and "/" in basefile):
                    (y1, y2, o) = re.split("[:/]", basefile)
                    # 1999/2000:45 is a special case
                    if len(y2) == 4 and y1 != "1999":
                        self.log.warning("%s (%s) using incorrect year format, should be '%s/%s:%s'" %
                                         (basefile, tmpurl, y1, y2[2:], o))
                        basefile = "%s/%s:%s" % (y1, y2[2:], o)
                # self.log.info("Basefile %s" % basefile)

                if not refresh and os.path.exists(self.downloaded_path(basefile)):
                    self.log.debug(
                        "%s exists, not calling download_single" % basefile)
                    existing_cnt += 1
                    if existing_cnt >= 5:
                        self.log.info("Last five documents were already downloaded, we're probably done here")
                        return
                    continue

                absolute_url = urllib.parse.urljoin(
                    self.browser.geturl(), link['href'])
                if self.download_single(basefile, refresh, absolute_url):
                    self.log.info("Downloaded %s" % basefile)
                    existing_cnt += 1
                    # return
            try:
                pagecnt += 1
                self.browser.follow_link(text=str(pagecnt))
                #self.browser.follow_link(text='Nästa sida')
            except LinkNotFoundError:
                # self.log.info(u'No next page link found, this was the last page')
                self.log.info('No link titled "%s" found, this was the last page' % str(pagecnt))
                done = True

    def remote_url(self, basefile):
        # do a search to find the proper url for the document
        self.log.info("Starting at %s" % self.start_url)
        self.browser.open(self.start_url)
        for f in self.browser.forms():
            if f.action.endswith("/sb/d/108"):
                self.browser.form = f
        self.browser["contentTypes"] = ["1"]
        self.browser["archiveQuery"] = basefile
        self.browser.submit()
        soup = BeautifulSoup.BeautifulSoup(self.browser.response())
        for link in soup.findAll(href=re.compile("/sb/d/108/a/")):
            desc = link.findNextSibling("span", {'class': 'info'}).text
            if basefile in desc:
                url = urllib.parse.urljoin(self.browser.geturl(), link['href'])
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
        return self.config['url'] + "publ/%s/%s" % (seg[document_type], basefile)

    def download_single(self, basefile, refresh=False, url=None):
        if not url:
            url = self.remote_url(basefile)
            if not url:  # remote_url failed
                return
        filename = self.downloaded_path(basefile)  # just the html page
        if (refresh or
            self.config.get('force', False) or
                not os.path.exists(filename)):
            existed = os.path.exists(filename)
            updated = self.download_if_needed(url, filename)
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

            soup = BeautifulSoup.BeautifulSoup(open(filename))
            cnt = 0
            pdfupdated = False
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
                    pdfurl = "http://regeringen.se/content/1/%s/%s" % (
                        path, pdffile)
                    pdffilename = self.downloaded_attachment_path(
                        basefile, pdffile)
                    if self.download_if_needed(pdfurl, pdffilename):
                        pdfupdated = True
                        self.log.debug(
                            "    %s is new or updated" % pdffilename)
                    else:
                        self.log.debug("    %s is unchanged" % pdffilename)
            else:
                self.log.warning(
                    "%s (%s) has no downloadable PDF files" % (basefile, url))
            if updated or pdfupdated:
                return True  # Successful download of new or changed file
            else:
                self.log.debug("%s and all PDF files are unchanged" % filename)
        else:
            self.log.debug("%s already exists" % (filename))
        return False

    def parse_from_soup(self, soup, basefile):
        doc = self.make_document()
        doc.lang = "sv"
        doc.uri = self.canonical_uri(basefile)
        d = Describer(doc.meta, doc.uri)
        d.rdftype(self.rdf_type)
        d.value(self.ns['prov']['wasGeneratedBy'],
                self.__class__.__module__ + "." + self.__class__.__name__)
        d.rel(self.ns['owl']['sameAs'], self.sameas_uri(doc.uri))

        content = soup.find(id="content")
        title = content.find("h1").string
        d.value(self.ns['dct']['title'], Literal(title, lang=doc.lang))
        identifier = self.sanitize_identifier(
            content.find("p", "lead").text)  # might need fixing up
        d.value(self.ns['dct']['identifier'], identifier)

        definitions = content.find("dl", "definitions")
        if definitions:
            for dt in definitions.findAll("dt"):
                key = dt.text
                value = dt.findNextSibling("dd").text
                if key == "Utgiven:":
                    try:
                        d.value(self.ns['dct'][
                                'published'], self.parse_swedish_date(value))
                    except ValueError as e:
                        self.log.warning(
                            "Could not parse %s as swedish date" % value)
                elif key == "Avsändare:":
                    if value.endswith("departementet"):
                        d.rel(self.ns['rpubl']
                              .departement, self.lookup_resource(value))
                    else:
                        d.rel(self.ns['dct'][
                              'publisher'], self.lookup_resource(value))

        if content.find("h2", text="Sammanfattning"):
            sums = content.find(
                "h2", text="Sammanfattning").parent.findNextSiblings("p")
            # "\n\n" doesn't seem to survive being stuffed in a rdfa
            # content attribute. Replace with simple space.
            summary = " ".join([x.text for x in sums])
            d.value(
                self.ns['dct']['abstract'], Literal(summary, lang=doc.lang))
            # summary = summary[:40]

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
            for listitem in box.findAll("li"):
                if not listitem.find("span", "info"):
                    continue
                infospans = [x.text.strip(
                ) for x in listitem.findAll("span", "info")]

                rel_basefile = None
                identifier = None

                for infospan in infospans:
                    if re_basefile.search(infospan):
                        # scrub identifier ("Dir. 2008:50" -> "2008:50" etc)
                        rel_basefile = re_basefile.search(infospan).group()
                        identifier = infospan

                if not rel_basefile:
                    self.log.warning("Couldn't find rel_basefile (elementid #%s) among %r" % (elementid, infospans))
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
                        self.log.warning("Cannot find out what type of document the linked %s is (#%s)" % (identifier, elementid))
                        self.log.warning("Infospans was %r" % infospans)
                        continue
                elif elementid == "legStep3":
                    subjUri = self.canonical_uri(
                        rel_basefile, self.PROPOSITION)
                d.rel(self.ns['rpubl']['utgarFran'], URIRef(subjUri))

        # find related pages
        related = content.find("h2", text="Relaterat")
        if related:
            for link in related.findParent("div").findAll("a"):
                r = urllib.parse.urljoin(
                    "http://www.regeringen.se/", link["href"])
                d.rel(RDFS.seeAlso, URIRef(r))
                #with d.rel(RDFS.seeAlso, URIRef(r)):
                #    d.value(RDFS.label, util.element_text(link))

        self.infer_triples(d, basefile)
        # print doc.meta.serialize(format="turtle")

        # find pdf file names in order
        pdffiles = self.find_pdf_links(content, basefile)
        if not pdffiles:
            self.log.error(
                "%s: No PDF documents found, can't parse anything" % basefile)
            return None

        doc.body = self.parse_pdfs(basefile, pdffiles)
        return doc

    def sanitize_identifier(self, identifier):
        return identifier

    def find_pdf_links(self, soup, basefile):
        pdffiles = []
        docsection = soup.find('div', 'doc')
        if docsection:
            for li in docsection.findAll("li", "pdf"):
                link = li.find('a')
                m = re.match(r'/download/(\w+\.pdf).*', link['href'])
                if not m:
                    continue
                pdfbasefile = m.group(1)
                pdffiles.append(pdfbasefile)
        return pdffiles

    def parse_pdfs(self, basefile, pdffiles):
        intermediate_dir = os.path.dirname(
            self.generic_path(basefile, 'intermediate', '.foo'))
        if not os.path.exists(intermediate_dir):
            os.makedirs(intermediate_dir)

        doc = CompoundElement()
        for pdffile in pdffiles:
            fullpdffile = intermediate_dir.replace(
                "intermediate", "downloaded") + os.sep + pdffile
            try:
                pdf = self.parse_pdf(fullpdffile, intermediate_dir)
                for page in pdf:
                    pass
                    # page.crop(left=50,top=0,bottom=900,right=700)
                doc.append(pdf)
            except ValueError:
                (exc_type, exc_value, exc_trackback) = sys.exc_info()
                self.log.warning("Ignoring exception %s (%s), skipping PDF %s" % (exc_type, exc_value, pdffile))
        return doc

    def parse_pdf(self, pdffile, intermediatedir):
        if 'log' in self.moduleconfig:
            loglevel = self.moduleconfig['log']
        else:
            loglevel = self.config.get('log', 'INFO')
        self.setup_logger('pdfreader', loglevel)
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
        cssfile = self.generic_path(doc.basefile, 'parsed', '.css')
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
        src_base = os.path.splitext(
            pdf.filename)[0].replace("/downloaded/", "/intermediate/")
        dest_base = self.generic_path(
            doc.basefile + "#background", "parsed", "")
        for pdf in doc.body:
            cnt = 0
            for page in pdf:
                totcnt += 1
                cnt += 1
                src = "%s%03d.png" % (src_base, page.number)
                dest = "%s%04d.png" % (dest_base, totcnt)  # 4 digits, compound docs can be over 1K pages
                if util.copy_if_different(src, dest):
                    self.log.debug("Copied %s to %s" % (src, dest))

                fp.write("#page%03d { background: url('%s');}\n" %
                         (cnt, os.path.basename(dest)))

    def list_external_resources(self, basefile):
        parsed = self.parsed_path(basefile)
        resource_dir = os.path.dirname(parsed)
        for f in [os.path.join(resource_dir, x) for x in os.listdir(resource_dir)
                  if os.path.join(resource_dir, x) != parsed]:
            yield f

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
                print(("    [%dx%d][%dx%d][%s@%s] %s" % (box.top, box.left, box.bottom, box.right, box.getfont()['family'], box.getfont()['size'], str(box))))
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
