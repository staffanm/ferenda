#!/usr/bin/env python
# -*- coding: iso-8859-1 -*-
#
# A abstract base class for fetching documents from data.riksdagen.se

import sys
import os
import re
import datetime
import socket
import codecs

# from mechanize import LinkNotFoundError, URLError
from bs4 import BeautifulSoup

from ferenda.describer import Describer
from ferenda import DocumentRepository
from ferenda import util
from ferenda.decorators import managedparsing
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
    start_url = "http://data.riksdagen.se/dokumentlista/?sz=100&sort=d&utformat=xml"
    downloaded_suffix = ".xml"
    storage_policy = "dir"

    #def generic_path(self,basefile,maindir,suffix):
    #    return super(Riksdagen,self).generic_path(basefile.replace("/","-"),maindir,suffix)

    def download(self):
        refresh = self.get_moduleconfig('refresh', bool, False)
        assert self.document_type is not None
        url = self.start_url + "&typ=%s" % self.document_type
        self.log.info("Starting at %s" % url)

        self.browser.open(url)
        done = False
        pagecnt = 1
        while not done:
            self.log.info('Result page #%s' % pagecnt)
            mainsoup = BeautifulSoup.BeautifulStoneSoup(
                self.browser.response())
            subnodes = mainsoup.findAll(lambda tag: tag.name == "subtyp" and
                                        tag.text == self.document_type)
            for doc in [x.parent for x in subnodes]:
                # TMP: Only retrieve old documents
                # if doc.rm.text > "1999":
                #     continue
                basefile = "%s:%s" % (doc.rm.text, doc.nummer.text)
                if doc.tempbeteckning.text:
                    basefile += "#%s" % doc.tempbeteckning.text
                if self.download_single(basefile, refresh=refresh, url=doc.dokumentstatus_url_xml.text):
                    self.log.info("Downloaded %s" % basefile)
            try:
                self.browser.open(mainsoup.dokumentlista['nasta_sida'])
                pagecnt += 1
            except KeyError:
                self.log.info(
                    'No next page link found, this was the last page')
                done = True

    def download_single(self, basefile, refresh=False, url=None):
        if not url:
            url = self.remote_url(basefile)
            if not url:  # remote_url failed
                return

        xmlfile = self.downloaded_path(basefile)
        if refresh or not os.path.exists(xmlfile):
            existed = os.path.exists(xmlfile)
            self.log.debug("  %s: Downloading to %s" % (basefile, xmlfile))
            try:
                updated = self.download_if_needed(url, xmlfile)

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
                docsoup = BeautifulSoup.BeautifulStoneSoup(open(xmlfile))
                dokid = docsoup.find('dok_id').text
                if docsoup.find('dokument_url_html'):
                    htmlurl = docsoup.find('dokument_url_html').text
                    htmlfile = self.generic_path(
                        basefile, "downloaded", ".html")
                    self.log.debug("   Downloading to %s" % htmlfile)
                    r = self.download_if_needed(htmlurl, htmlfile)
                elif docsoup.find('dokument_url_text'):
                    texturl = docsoup.find('dokument_url_text').text
                    textfile = self.generic_path(
                        basefile, "downloaded", ".txt")
                    self.log.debug("   Downloading to %s" % htmlfile)
                    r = self.download_if_needed(texturl, textfile)
                fileupdated = fileupdated or r

                for b in docsoup.findAll('bilaga'):
                    # self.log.debug("Looking for %s, found %s", dokid, b.dok_id.text)
                    if b.dok_id.text != dokid:
                        continue
                    filetype = "." + b.filtyp.text
                    filename = self.generic_path(
                        basefile, "downloaded", filetype)
                    self.log.debug("   Downloading to %s" % filename)
                    r = self.download_if_needed(b.fil_url.text, filename)
                    fileupdated = fileupdated or r
                    break

            except (URLError, socket.error) as e:
                # 404 not found or similar -- logged in download_if_needed
                return False
            if updated or fileupdated:
                return True  # Successful download of new or changed file
            else:
                self.log.debug(
                    "%s and all associated files unchanged" % xmlfile)
        else:
            self.log.debug("%s already exists" % (xmlfile))
        return False

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
        return self.config['url'] + "publ/%s/%s" % (seg[self.rdf_type], basefile)
