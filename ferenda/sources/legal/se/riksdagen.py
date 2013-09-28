# -*- coding: utf-8 -*-
from __future__ import unicode_literals

# A abstract base class for fetching documents from data.riksdagen.se

import os
import codecs

import requests
from bs4 import BeautifulSoup

from ferenda.describer import Describer
from ferenda import util
from ferenda.decorators import managedparsing, downloadmax
from . import SwedishLegalSource
from ferenda.elements import Paragraph


class Riksdagen(SwedishLegalSource):
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

    # add typ=prop or whatever
    downloaded_suffix = ".xml"
    storage_policy = "dir"
    document_type = None
    start_url = None
    start_url_template = "http://data.riksdagen.se/dokumentlista/?sz=100&sort=d&utformat=xml&typ=%(doctype)s"

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
        while not done:
            pagecount = 1
            resp = requests.get(url)
            soup = BeautifulSoup(resp.text, features="xml")

            subnodes = soup.find_all(lambda tag: tag.name == "subtyp" and
                                     tag.text == self.document_type)
            for doc in [x.parent for x in subnodes]:
                # TMP: Only retrieve old documents
                # if doc.rm.text > "1999":
                #     continue
                basefile = "%s:%s" % (doc.rm.text, doc.nummer.text)
                attachment = None
                if doc.tempbeteckning.text:
                    attachment = doc.tempbeteckning.text
                yield (basefile, attachment), doc.dokumentstatus_url_xml.text
            try:
                url = soup.dokumentlista['nasta_sida']
                pagecnt += 1
                self.log.info("Getting page #%d" % pagecnt)
            except KeyError:
                self.log.info("That was the last page")
                done = True

    def download_single(self, basefile, url=None):
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
        if not (self.config.force or not os.path.exists(xmlfile)):
            self.log.debug("%s already exists" % (xmlfile))
            return False

        existed = os.path.exists(xmlfile)
        self.log.debug("  %s: Downloading to %s" % (basefile, xmlfile))

        updated = self.download_if_needed(url, basefile)

        if existed:
            if updated:
                self.log.debug(
                    "%s existed, but downloaded new" % xmlfile)
            else:
                self.log.debug(
                    "%s is unchanged -- checking files" % xmlfile)
        else:
            self.log.debug(
                "%s did not exist, so it was downloaded" % xmlfile)
        fileupdated = False
        r = None
        docsoup = BeautifulSoup(open(xmlfile), features="xml")
        dokid = docsoup.find('dok_id').text
        if docsoup.find('dokument_url_html'):
            htmlurl = docsoup.find('dokument_url_html').text
            htmlfile = self.store.downloaded_path(basefile, attachment=docname + ".html")
            self.log.debug("   Downloading to %s" % htmlfile)
            r = self.download_if_needed(htmlurl, basefile, filename=htmlfile)
        elif docsoup.find('dokument_url_text'):
            texturl = docsoup.find('dokument_url_text').text
            htmlfile = self.store.downloaded_path(basefile, attachment=docname + ".txt")
            self.log.debug("   Downloading to %s" % htmlfile)
            r = self.download_if_needed(texturl, basefile, filename=textfile)
        fileupdated = fileupdated or r

        for b in docsoup.findAll('bilaga'):
            # self.log.debug("Looking for %s, found %s", dokid, b.dok_id.text)
            if b.dok_id.text != dokid:
                continue
            filetype = "." + b.filtyp.text
            filename = self.store.downloaded_path(basefile, attachment=docname + filetype)
            self.log.debug("   Downloading to %s" % filename)
            r = self.download_if_needed(b.fil_url.text, basefile, filename=filename)
            fileupdated = fileupdated or r
            break

        if updated or fileupdated:
            return True  # Successful download of new or changed file
        else:
            self.log.debug(
                "%s and all associated files unchanged" % xmlfile)

    @managedparsing
    def parse(self, doc):
        doc.uri = self.canonical_uri(doc.basefile)
        self.log.debug("Set URI to %s (from %s)" % (doc.uri, doc.basefile))
        d = Describer(doc.meta, doc.uri)
        d.rdftype(self.rdf_type)
        d.value(self.ns['prov'].wasGeneratedBy, self.qualified_class_name())
        self.infer_triples(d, doc.basefile)
        htmlfile = self.generic_path(doc.basefile, 'downloaded', '.html')
        pdffile = self.generic_path(doc.basefile, 'downloaded', '.pdf')
        self.log.debug("Loading soup from %s" % htmlfile)
        soup = BeautifulSoup.BeautifulSoup(
            codecs.open(
                htmlfile, encoding='iso-8859-1', errors='replace').read(),
            convertEntities='html')
        self.parse_from_soup(soup, doc)

    def parse_from_soup(self, soup, doc):
        for block in soup.findAll(['div', 'p']):
            t = util.normalize_space(''.join(block.findAll(text=True)))
            block.extract()  # to avoid seeing it again
            if t:
                doc.body.append(Paragraph([t]))

    def canonical_uri(self, basefile):
        seg = {self.ns['rpubl'].Proposition: "prop",
               self.ns['rpubl'].Skrivelse: "skr"}
        return self.config.url + "publ/%s/%s" % (seg[self.rdf_type], basefile)
