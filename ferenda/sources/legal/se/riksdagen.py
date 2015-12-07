# -*- coding: utf-8 -*-
from __future__ import unicode_literals

# A abstract base class for fetching documents from data.riksdagen.se

import os
from io import StringIO

import requests
import requests.exceptions
from bs4 import BeautifulSoup
from lxml import etree

from ferenda import util, errors
from ferenda.compat import OrderedDict
from ferenda.decorators import downloadmax
from ferenda.elements import Body, Paragraph, Preformatted
from ferenda.pdfreader import StreamingPDFReader
from .fixedlayoutsource import FixedLayoutSource, FixedLayoutStore


class RiksdagenStore(FixedLayoutStore):
    downloaded_suffix = ".xml"
    doctypes = OrderedDict([(".xml", b''),
                            (".pdf", b''),
                            (".html", b'')])

    def intermediate_path(self, basefile, version=None, attachment=None):
        candidate = None
        for suffix in (".hocr.html.bz2", ".hocr.html", ".xml", ".xml.bz2"):
            candidate = self.path(basefile, "intermediate", suffix)
            if os.path.exists(candidate):
                break
        if not candidate:
            return self.path(basefile, "intermediate", ".xml")
        else:
            return candidate.replace(".bz2", "")

class Riksdagen(FixedLayoutSource):
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

    # some ridiculusly large document (statsbudget) have little legal importance. Just process the metadata
    metadataonly = set([(PROPOSITION, "1971:1"),
                        (PROPOSITION, "1971:115"),
                        (PROPOSITION, "1972:1"),
                        (PROPOSITION, "1972:90"),
                        (PROPOSITION, "1973:1"),
                        (PROPOSITION, "1973:125"),
                        (PROPOSITION, "1974:1"),
                        (PROPOSITION, "1974:100"),
                        (PROPOSITION, "1975:1"),
                        (PROPOSITION, "1975:100"),
                        (PROPOSITION, "1975/76:25"),
                        (PROPOSITION, "1975/76:100"),
                        (PROPOSITION, "1975/76:101"),
                        (PROPOSITION, "1975/76:125"),
                        (PROPOSITION, "1975/76:150"),
                        (PROPOSITION, "1976/77:25"),
                        (PROPOSITION, "1976/77:100"),
                        (PROPOSITION, "1976/77:101"),
                        (PROPOSITION, "1976/77:125"),
                        (PROPOSITION, "1976/77:150"),
                        (PROPOSITION, "1977/78:100"),
                        (PROPOSITION, "1977/78:101"),
                        (PROPOSITION, "1977/78:125"),
                        (PROPOSITION, "1977/78:150"),
                        (PROPOSITION, "1977/78:25"),
                        (PROPOSITION, "1978/79:100"),
                        (PROPOSITION, "1978/79:101"),
                        (PROPOSITION, "1978/79:125"),
                        (PROPOSITION, "1978/79:150"),
                        (PROPOSITION, "1978/79:25"),
                        (PROPOSITION, "1978/79:43"),
                        (PROPOSITION, "1979/80:100"),
                        (PROPOSITION, "1979/80:101"),
                        (PROPOSITION, "1979/80:125"),
                        (PROPOSITION, "1979/80:150"),
                        (PROPOSITION, "1979/80:25"),
                        (PROPOSITION, "1980/81:100"),
                        (PROPOSITION, "1980/81:101"),
                        (PROPOSITION, "1980/81:125"),
                        (PROPOSITION, "1980/81:150"),
                        (PROPOSITION, "1980/81:25"),
                        (PROPOSITION, "1981/82:100"),
                        (PROPOSITION, "1981/82:101"),
                        (PROPOSITION, "1981/82:125"),
                        (PROPOSITION, "1981/82:150"),
                        (PROPOSITION, "1981/82:25"),
                        (PROPOSITION, "1982/83:100"),
                        (PROPOSITION, "1982/83:101"),
                        (PROPOSITION, "1982/83:125"),
                        (PROPOSITION, "1982/83:150"),
                        (PROPOSITION, "1982/83:25"),
                        (PROPOSITION, "1983/84:100"),
                        (PROPOSITION, "1983/84:101"),
                        (PROPOSITION, "1983/84:125"),
                        (PROPOSITION, "1983/84:150"),
                        (PROPOSITION, "1983/84:25"),
                        (PROPOSITION, "1984/85:100"),
                        (PROPOSITION, "1984/85:101"),
                        (PROPOSITION, "1984/85:125"),
                        (PROPOSITION, "1984/85:150"),
                        (PROPOSITION, "1984/85:25"),
                        (PROPOSITION, "1985/86:100"),
                        (PROPOSITION, "1985/86:125"),
                        (PROPOSITION, "1985/86:140"),
                        (PROPOSITION, "1985/86:150"),
                        (PROPOSITION, "1985/86:25"),
                        (PROPOSITION, "1986/87:100"),
                        (PROPOSITION, "1986/87:109"),
                        (PROPOSITION, "1986/87:150"),
                        (PROPOSITION, "1986/87:25"),
                        (PROPOSITION, "1987/88:100"),
                        (PROPOSITION, "1987/88:125"),
                        (PROPOSITION, "1987/88:150"),
                        (PROPOSITION, "1987/88:25"),
                        (PROPOSITION, "1988/89:100"),
                        (PROPOSITION, "1988/89:125"),
                        (PROPOSITION, "1988/89:150"),
                        (PROPOSITION, "1988/89:25"),
                        (PROPOSITION, "1989/90:125"),
                        (PROPOSITION, "1990/91:100"),
                        (PROPOSITION, "1990/91:125"),
                        (PROPOSITION, "1990/91:150"),
                        (PROPOSITION, "1990/91:25"),
                        (PROPOSITION, "1991/92:100"),
                        (PROPOSITION, "1991/92:125"),
                        (PROPOSITION, "1991/92:150"),
                        (PROPOSITION, "1991/92:25"),
                        (PROPOSITION, "1992/93:100"),
                        (PROPOSITION, "1992/93:105"),
                        (PROPOSITION, "1992/93:150"),
                        (PROPOSITION, "1993/94:100"),
                        (PROPOSITION, "1993/94:105"),
                        (PROPOSITION, "1993/94:125"),
                        (PROPOSITION, "1993/94:150"),
                        (PROPOSITION, "1994/95:100"),
                        (PROPOSITION, "1994/95:105"),
                        (PROPOSITION, "1994/95:125"),
                        (PROPOSITION, "1994/95:150"),
                        (PROPOSITION, "1995/96:105"),
                        (PROPOSITION, "1995/96:220"),
                        (PROPOSITION, "1996/97:1"),
                        (PROPOSITION, "1996/97:100"),
                        (PROPOSITION, "1997/98:1"),
                        (PROPOSITION, "1997/98:100"),
                        (PROPOSITION, "1998/99:1"),
                        (PROPOSITION, "1998/99:100"),
                        (PROPOSITION, "1999/2000:1"),
                        (PROPOSITION, "1999/2000:100"),
                        (PROPOSITION, "2000/01:1"),
                        (PROPOSITION, "2000/01:100"),
                        (PROPOSITION, "2001/02:1"),
                        (PROPOSITION, "2001/02:100"),
                        (PROPOSITION, "2002/03:1"),
                        (PROPOSITION, "2002/03:100"),
                        (PROPOSITION, "2003/04:1"),
                        (PROPOSITION, "2003/04:100"),
                        (PROPOSITION, "2004/05:1"),
                        (PROPOSITION, "2004/05:100"),
                        (PROPOSITION, "2005/06:1"),
                        (PROPOSITION, "2005/06:100"),
                        (PROPOSITION, "2006/07:1"),
                        (PROPOSITION, "2006/07:100"),
                        (PROPOSITION, "2007/08:1"),
                        (PROPOSITION, "2007/08:100"),
                        (PROPOSITION, "2008/09:1"),
                        (PROPOSITION, "2008/09:100"),
                        (PROPOSITION, "2009/10:1"),
                        (PROPOSITION, "2009/10:100"),
                        (PROPOSITION, "2010/11:1"),
                        (PROPOSITION, "2010/11:100"),
                        (PROPOSITION, "2011/12:1"),
                        (PROPOSITION, "2011/12:100"),
                        (PROPOSITION, "2012/13:1"),
                        (PROPOSITION, "2012/13:100"),
                        (PROPOSITION, "2013/14:1"),
                        (PROPOSITION, "2013/14:100"),
                        (PROPOSITION, "2014/15:1"),
                        (PROPOSITION, "2014/15:100"),
                        (PROPOSITION, "2015/16:1"),
                        ])

    @property
    def urispace_segment(self):
        return {self.PROPOSITION: "prop",
                self.DS: "utr/ds",
                self.SOU: "utr/sou",
                self.DIREKTIV: "dir"}.get(self.document_type)

    def download(self, basefile=None):
        if basefile:
            return self.download_single(basefile)
        url = self.start_url_template % {'doctype': self.document_type}
        for basefile, url in self.download_get_basefiles(url):
            self.download_single(basefile, url)

    @downloadmax
    def download_get_basefiles(self, start_url):
        self.log.info("Starting at %s" % start_url)
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
                self.log.info("Getting page #%d" % pagecount)
            except KeyError:
                self.log.info("That was the last page")
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
            docsoup = BeautifulSoup(open(xmlfile), features="xml")
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
        if (self.document_type, basefile) in self.metadataonly:
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
        with self.store.open_downloaded(basefile) as fp:
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
            not (self.document_type, basefile) in self.metadataonly):
            fp = self.parse_open(basefile)
            parser = "ocr" if ".hocr." in util.name_from_fp(fp) else "xml"
            return StreamingPDFReader().read(fp, parser=parser)
            # this will have returned a fully-loaded PDFReader document
        else:
            # fp points to a HTML file, which we can use directly.
            # fp will be a raw bitstream of a latin-1 file.
            try:
                filename = util.name_from_fp(fp)
                self.log.debug("%s: Loading soup from %s" % (basefile, filename))
            except ValueError:
                self.log.debug("%s: Loading placeholder soup" % (basefile))
            return BeautifulSoup(fp.read(), "lxml")

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

    def get_parser(self, basefile, sanitized):
        if isinstance(sanitized, BeautifulSoup):
            return self.htmlparser
        else:
            return super(Riksdagen, self).get_parser(basefile, sanitized)

    def tokenize(self, reader):
        if isinstance(reader, BeautifulSoup):
            if reader.find("div"):
                return reader.find_all(['div', 'p', 'span'])
            else:
                return reader.find_all("pre")
        else:
            return super(Riksdagen, self).tokenize(reader)

# FIXME: Work in the below support for FERENDA_DEBUGANALYSIS and FERENDA_FSMDEBUG in fixedlayoutsource.get_parser
#     def get_parser(self, basefile, sanitized)
#        # FIXME: add code to get a customized PDFAnalyzer class here
#        # if self.document_type = self.PROPOSITION:
#        analyzer = PDFAnalyzer(pdf)
#        # metrics = analyzer.metrics(metrics_path, plot_path, force=self.config.force)
#        metrics = analyzer.metrics(metrics_path, plot_path)
#        if os.environ.get("FERENDA_DEBUGANALYSIS"):
#            self.log.debug("Creating debug version of PDF")
#            analyzer.drawboxes(pdfdebug_path, offtryck_gluefunc, metrics=metrics)
#        self.log.debug("Parsing with metrics %s" % metrics)
#        parser = offtryck_parser(basefile, metrics=metrics, identifier=identifier)
#        parser.debug = os.environ.get('FERENDA_FSMDEBUG', False)
#        return parse

