# -*- coding: utf-8 -*-
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
from builtins import *
from future import standard_library
standard_library.install_aliases()

# A abstract base class for fetching documents from data.riksdagen.se
from collections import OrderedDict
from io import StringIO
import os
import re
import json

import requests
import requests.exceptions
from bs4 import BeautifulSoup
from lxml import etree

from ferenda import util, errors
from ferenda.decorators import downloadmax, recordlastdownload
from ferenda.elements import Body, Paragraph, Preformatted
from ferenda.pdfreader import StreamingPDFReader
from .fixedlayoutsource import FixedLayoutSource, FixedLayoutStore
from . import Offtryck
from .legalref import LegalRef


class RiksdagenStore(FixedLayoutStore):
    downloaded_suffix = ".xml"
    doctypes = OrderedDict([(".xml", b''),
                            (".pdf", b''),
                            (".html", b'')])

    def intermediate_path(self, basefile, version=None, attachment=None):
        candidate = None
        if attachment:
            return super(RiksdagenStore, self).intermediate_path(basefile, version, attachment)
        
        for suffix in (".hocr.html.bz2", ".hocr.html", ".xml", ".xml.bz2"):
            candidate = self.path(basefile, "intermediate", suffix)
            if os.path.exists(candidate):
                break
        if not candidate:
            return self.path(basefile, "intermediate", ".xml")
        else:
            return candidate.replace(".bz2", "")

class Riksdagen(Offtryck, FixedLayoutSource):
    BILAGA = "bilaga"
    DS = "ds"
    DIREKTIV = "dir"
    EUNAMND_KALLELSE = "kf-lista"
    EUNAMND_PROT = "eunprot"
    EUNAMND_DOK = "eundok"
    EUNAMND_BILAGA = "eunbil"
    FAKTAPROMEMORIA = "fpm"
    FRAMSTALLNING = "frsrdg"
    FOREDRAGNINGSLISTA = "f-lista"
    GRANSKNINGSRAPPORT = "rir"
    INTERPELLATION = "ip"
    KOMMITTEBERATTELSER = "komm"
    MINISTERRADSPROMEMORIA = "minråd"
    MOTION = "mot"
    PROPOSITION = "prop"
    PROTOKOLL = "prot"
    RAPPORT = "rfr"
    RIKSDAGSSKRIVELSE = "rskr"
    FRAGA = "fr"
    SKRIVELSE = "skr"
    SOU = "sou"
    SVAR = "frs"
    SFS = "sfs"
    TALARLISTA = "t-lista"
    UTSKOTTSDOKUMENT = "utskottsdokument"
    YTTRANDE = "yttr"

    downloaded_suffix = ".xml"
    storage_policy = "dir"
    documentstore_class = RiksdagenStore
    document_type = None
    start_url = None
    start_url_template = "http://data.riksdagen.se/dokumentlista/?sort=datum&sortorder=asc&utformat=xml&doktyp=%(doctype)s"


    @property
    def urispace_segment(self):
        return {self.PROPOSITION: "prop",
                self.DS: "utr/ds",
                self.SOU: "utr/sou",
                self.DIREKTIV: "dir"}.get(self.document_type)

    
    @recordlastdownload
    def download(self, basefile=None):
        if basefile:
            return self.download_single(basefile)
        url = self.start_url_template % {'doctype': self.document_type}
        if 'lastdownload' in self.config and not self.config.refresh:
            url += "&from=" + self.config.lastdownload.strftime("%Y-%m-%d")
        for basefile, url in self.download_get_basefiles(url):
            self.download_single(basefile, url)

    @downloadmax
    def download_get_basefiles(self, start_url):
        self.log.debug("Starting at %s" % start_url)
        url = start_url
        done = False
        pagecount = 1
        while not done:
            resp = requests.get(url)
            soup = BeautifulSoup(resp.text, features="xml")

            subnodes = soup.find_all(lambda tag: tag.name == "subtyp" and
                                     tag.text == self.document_type)
            for doc in [x.parent for x in subnodes]:
                # TMP: Only retrieve old documents
                # if doc.rm.text > "1999":
                #     continue
                if doc.rm is None or doc.nummer is None:
                    # this occasionally happens, although not all the
                    # time for the same document. We can't really
                    # recover from this easily, so we'll just skip and
                    # hope that it doesn't occur on next download.
                    continue
                basefile = "%s:%s" % (doc.rm.text, doc.nummer.text)
                attachment = None
                if doc.tempbeteckning.text:
                    attachment = doc.tempbeteckning.text
                yield (basefile, attachment), doc.dokumentstatus_url_xml.text
            try:
                url = soup.dokumentlista['nasta_sida']
                pagecount += 1
                self.log.debug("Getting page #%d" % pagecount)
            except KeyError:
                self.log.debug("That was the last page")
                done = True

    def remote_url(self, basefile):
        # FIXME: this should be easy
        digits = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ'
        if "/" in basefile:   # eg 1975/76:24
            year = int(basefile.split("/")[0])
            remainder = year - 1400
        else:                 # eg 1975:6
            year = int(basefile.split(":")[0])
            remainder = year - 1401
        pnr = basefile.split(":")[1]
        base36year = ""
        # since base36 year is guaranteed to be 2 digits, this could
        # be simpler.
        while remainder != 0:
            remainder, i = divmod(remainder, len(digits))
            base36year = digits[i] + base36year
        # FIXME: Map more of these...
        doctypecode = {self.PROPOSITION: "03",
                       self.UTSKOTTSDOKUMENT: "01"}[self.document_type]
        return "http://data.riksdagen.se/dokumentstatus/%s%s%s.xml" % (
            base36year, doctypecode, pnr)

    def download_single(self, basefile, url=None):
        attachment = None
        if isinstance(basefile, tuple):
            basefile, attachment = basefile
        if attachment:
            docname = attachment
        else:
            docname = "index"
        if not url:
            url = self.remote_url(basefile)
            if not url:  # remote_url failed
                return False
        xmlfile = self.store.downloaded_path(basefile)
        if not (self.config.refresh or not os.path.exists(xmlfile)):
            self.log.debug("%s already exists" % (xmlfile))
            return False
        existed = os.path.exists(xmlfile)
        self.log.debug("  %s: Downloading to %s" % (basefile, xmlfile))
        try:
            updated = self.download_if_needed(url, basefile)
            if existed:
                if updated:
                    self.log.info("  %s: updated from %s" % (basefile, url))
                else:
                    self.log.debug("  %s: %s is unchanged, checking files" %
                                   (basefile, xmlfile))
            else:
                self.log.info("%s: downloaded from %s" % (basefile, url))
            fileupdated = False
            r = None
            # for some reason, using a XML parser ("xml" or
            # "lxml-xml") causes only the first ~70 kb of the file
            # being parsed... But the lxml parser should work good
            # enough for our needs, even if it uses non-html tags.
            docsoup = BeautifulSoup(open(xmlfile), "lxml")
            dokid = docsoup.find('dok_id').text
            if docsoup.find('dokument_url_html'):
                htmlurl = docsoup.find('dokument_url_html').text
                htmlfile = self.store.downloaded_path(basefile, attachment=docname + ".html")
                #self.log.debug("   Downloading to %s" % htmlfile)
                r = self.download_if_needed(htmlurl, basefile, filename=htmlfile)
                if r:
                    self.log.debug("    Downloaded html ver to %s" % htmlfile)
            elif docsoup.find('dokument_url_text'):
                texturl = docsoup.find('dokument_url_text').text
                textfile = self.store.downloaded_path(basefile, attachment=docname + ".txt")
                #self.log.debug("   Downloading to %s" % htmlfile)
                r = self.download_if_needed(texturl, basefile, filename=textfile)
                if r:
                    self.log.debug("    Downloaded text ver to %s" % textfile)
            fileupdated = fileupdated or r
            for b in docsoup.findAll('bilaga'):
                # self.log.debug("Looking for %s, found %s", dokid, b.dok_id.text)
                if b.dok_id.text != dokid:
                    continue
                if b.filtyp is None:
                    # apparantly this can happen sometimes? Very intermitently, though.
                    self.log.warning(
                        "Couldn't find filtyp for bilaga %s in %s" %
                        (b.dok_id.text, xmlfile))
                    continue
                filetype = "." + b.filtyp.text
                filename = self.store.downloaded_path(basefile, attachment=docname + filetype)
                # self.log.debug("   Downloading to %s" % filename)
                try:
                    r = self.download_if_needed(b.fil_url.text, basefile, filename=filename)
                    if r:
                        self.log.debug("    Downloaded attachment as %s" % filename)
                except requests.exceptions.HTTPError as e:
                    # occasionally we get a 404 even though we shouldn't. Report and hope it
                    # goes better next time.
                    self.log.error("   Failed: %s" % e)
                    continue
                fileupdated = fileupdated or r
                break
        except requests.exceptions.HTTPError as e:
            self.log.error("%s: Failed: %s" % (basefile, e))
            return False

        if updated or fileupdated:
            return True  # Successful download of new or changed file
        else:
            self.log.debug(
                "  %s: %s and all associated files unchanged" % (basefile, xmlfile))

    def downloaded_to_intermediate(self, basefile):
        # first check against our "blacklist-light":
        if self.get_parse_options(basefile) == "metadataonly":
            self.log.warning("%s: Will only process metadata, creating placeholder for body text" % basefile)
            # nb: tokenize() depends on the text being enclosed in <pre> tags
            return StringIO("<pre>Dokumenttext saknas (se originaldokument)</pre>")
        downloaded_path = self.store.downloaded_path(basefile,
                                                     attachment="index.pdf")
        downloaded_path_html = self.store.downloaded_path(basefile,
                                                          attachment="index.html")
        if not os.path.exists(downloaded_path):
            if os.path.exists(downloaded_path_html):
                # attempt to parse HTML instead
                return open(downloaded_path_html)
            else:
                # just grab the HTML from the XML file itself...
                tree = etree.parse(self.store.downloaded_path(basefile))
                html = tree.getroot().find("dokument").find("html")
            if html is not None:
                return StringIO(html.text)
            else:
                return StringIO("<html><h1>Dokumenttext saknas</h1></html>")

        intermediate_path = self.store.intermediate_path(basefile)
        intermediate_path += ".bz2" if self.config.compress == "bz2" else ""
        # if a compressed bz2 file is > 5 MB, it's just too damn big
        if os.path.exists(intermediate_path) and os.path.getsize(intermediate_path) > 5*1024*1024:
            raise ParseError("%s: %s is just too damn big (%s bytes)" % 
                             (basefile, intermediate_path, 
                              os.path.getsize(intermediate_path)))
        intermediate_dir = os.path.dirname(intermediate_path)
        convert_to_pdf = not downloaded_path.endswith(".pdf")
        keep_xml = "bz2" if self.config.compress == "bz2" else True
        reader = StreamingPDFReader()
        try:
            res = reader.convert(filename=downloaded_path,
                                 workdir=intermediate_dir,
                                 images=self.config.pdfimages,
                                 convert_to_pdf=convert_to_pdf,
                                 keep_xml=keep_xml)
        except errors.PDFFileIsEmpty:
            self.log.debug("%s: PDF had no textcontent, trying OCR" % basefile)
            res = reader.convert(filename=downloaded_path,
                                 workdir=intermediate_dir,
                                 images=self.config.pdfimages,
                                 convert_to_pdf=convert_to_pdf,
                                 keep_xml=keep_xml,
                                 ocr_lang="swe")
        intermediate_path = self.store.intermediate_path(basefile)
        intermediate_path += ".bz2" if self.config.compress == "bz2" else ""
        if os.path.getsize(intermediate_path) > 20*1024*1024:
            raise errors.ParseError("%s: %s (after conversion) is just too damn big (%s Mbytes)" % 
                                    (basefile, intermediate_path, 
                                     os.path.getsize(intermediate_path) / (1024*1024)))
        return res

    def metadata_from_basefile(self, basefile):
        a = super(Riksdagen, self).metadata_from_basefile(basefile)
        a["rpubl:arsutgava"], a["rpubl:lopnummer"] = basefile.split(":", 1)
        return a
    

    def extract_head(self, fp, basefile):
        # fp will point to the pdf2html output -- we need to open the
        # XML file instead
        with open(self.store.downloaded_path(basefile)) as fp:
            return BeautifulSoup(fp.read(), "xml")

    def extract_metadata(self, soup, basefile):
        attribs = self.metadata_from_basefile(basefile)
        attribs["dcterms:title"] = soup.dokument.titel.text
        attribs["dcterms:issued"] = util.strptime(soup.dokument.publicerad.text,
                                                  "%Y-%m-%d %H:%M:%S").date()
        return attribs

    def extract_body(self, fp, basefile):
        pdffile = self.store.downloaded_path(basefile, attachment="index.pdf")
        # fp can now be a pointer to a hocr file, a pdf2xml file,
        # a html file or a StringIO object containing html taken
        # from index.xml
        if (os.path.exists(pdffile) and
            self.get_parse_options(basefile) != "metadataonly"):
            fp = self.parse_open(basefile)
            parser = "ocr" if ".hocr." in util.name_from_fp(fp) else "xml"
            reader = StreamingPDFReader().read(fp, parser=parser)
            identifier = self.canonical_uri(basefile)
            pdffile = self.store.downloaded_path(basefile, attachment="index.pdf")
            for page in reader:
                page.src = pdffile
            return reader
        else:
            # fp points to a HTML file, which we can use directly.
            # fp will be a raw bitstream of a latin-1 file.
            try:
                filename = util.name_from_fp(fp)
                self.log.debug("%s: Loading soup from %s" % (basefile, filename))
            except ValueError:
                self.log.debug("%s: Loading placeholder soup" % (basefile))
            text = fp.read()
            if text == "Propositionen ej utgiven":
                raise errors.DocumentRemovedError("%s was never published" % basefile)
            else:
                return BeautifulSoup(text, "lxml")

    @staticmethod
    def htmlparser(chunks):
        b = Body()
        for block in chunks:
            tagtype = Preformatted if block.name == "pre" else Paragraph
            t = util.normalize_space(''.join(block.findAll(text=True)))
            block.extract()  # to avoid seeing it again
            if t:
                b.append(tagtype([t]))
        return b

    def get_parser(self, basefile, sanitized, initialstate=None, startpage=None, pagecount=None):
        if isinstance(sanitized, BeautifulSoup):
            return self.htmlparser
        else:
            return super(Riksdagen, self).get_parser(basefile, sanitized, initialstate, startpage, pagecount)

    def sanitize_body(self, rawbody):
        sanitized = super(Riksdagen, self).sanitize_body(rawbody)
        if isinstance(rawbody, BeautifulSoup):
            return rawbody
        if ".hocr." in rawbody.filename:
            # if this is a scanned source, examine the front page for some
            # common OCR errors (the coat of arms logo is sometimes
            # mistaken for text) and remove those
            pageheight = rawbody[0].height
            pagewidth = rawbody[0].width
            for textbox in rawbody[0]:
                prevright = 0
                for textelement in textbox:
                    # if a suspiciously large (over 5% of the page
                    # width) space occurs between "words" in a line,
                    # then the new item is probably a OCR mistake, at
                    # least if it's small.
                    if (prevright and
                        (textelement.left - prevright > pagewidth / 20) and
                        len(textelement.strip()) < 4):
                        textbox.remove(textelement)
                        self.log.debug("sanitize: removing textelement '%s', probable OCR mistake" % textelement)
                    prevright = textelement.left + textelement.width
            for pageidx, page in enumerate(rawbody):
                # then, loop through all pages and attempt to find
                # places where the "Bilaga" ocr_par node has been
                # placed at the end of the page rather than the start,
                # and rearrange
                if len(page) <= 2:
                    continue # but not for pages with 0-2 textboxes
                for idx in (-1, -2):
                    if page[idx].left > page.width * 0.6:
                        strchunk = str(page[idx])
                        if re.search("Bilaga [l\d]", strchunk):
                            self.log.debug("Rearranging boxes on page %s, moving box %s to page start" % (pageidx+1, idx))
                            page.insert(0, page.pop(idx))
                            break
        return rawbody

    def tokenize(self, reader):
        if isinstance(reader, BeautifulSoup):
            if reader.find("div"):
                return reader.find_all(['div', 'p', 'span'])
            else:
                return reader.find_all("pre")
        else:
            return super(Riksdagen, self).tokenize(reader)

    def visitor_functions(self, basefile):
        # the .metrics.json file must exist at this point, but just in
        # case it doesn't
        metrics_path = self.store.path(basefile, "intermediate", ".metrics.json")
        if os.path.exists(metrics_path):
            with open(metrics_path) as fp:
                metrics = json.load(fp)
                defaultsize = metrics['default']['size']
        else:
            self.log.warning("%s: visitor_functions: %s doesn't exist" % (basefile, metrics_path))
            defaultsize = 8
        sharedstate = {'basefile': basefile,
                       'defaultsize': defaultsize}
        functions = [(self.find_primary_law, sharedstate),
                     (self.find_commentary, sharedstate)]
        if not hasattr(self, 'sfsparser'):
            self.sfsparser = LegalRef(LegalRef.LAGRUM)
        self.sfsparser.currentlynamedlaws.clear()
        if self.document_type == self.PROPOSITION:
            functions.append((self.find_kommittebetankande, sharedstate))
        return functions


    def sourcefiles(self, basefile, resource=None):
        sourcefile = self.store.downloaded_path(basefile,
                                                attachment="index.pdf")
        if not os.path.exists(sourcefile):
            sourcefile = self.store.downloaded_path(basefile)
        return [(sourcefile, self.infer_identifier(basefile))]

    def source_url(self, basefile):
        # http://data.riksdagen.se/dokumentstatus/GF03167.xml =>
        # http://www.riksdagen.se/sv/Dokument-Lagar/Forslag/Propositioner-och-skrivelser/_GF03167/

        url = super(Riksdagen, self).source_url(basefile)
        return url.replace("http://data.riksdagen.se/dokumentstatus/",
                           "http://www.riksdagen.se/sv/Dokument-Lagar/Forslag/Propositioner-och-skrivelser/_"
                           ).replace(".xml", "/?text=true")
