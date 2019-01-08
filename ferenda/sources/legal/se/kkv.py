# -*- coding: utf-8 -*-
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
from builtins import *

import re
import os

import lxml.html
from bs4 import BeautifulSoup

from ferenda import util, errors
from ferenda import PDFReader
from ferenda.elements import Body
from . import RPUBL, RINFOEX
from .fixedlayoutsource import FixedLayoutSource, FixedLayoutStore, FixedLayoutHandler

class KKVHandler(FixedLayoutHandler):
    # this is a simplified version of MyndFskrHandler.get_pathfunc
    def get_pathfunc(self, environ, basefile, params, contenttype, suffix):
        if basefile and suffix == "png":
            params["dir"] = "downloaded"
            params["page"] = str(int(environ["PATH_INFO"].split("/sid")[1][:-4])-1)
            params["format"] = suffix
        return super(FixedLayoutHandler, self).get_pathfunc(environ, basefile, params,
                                                         contenttype, suffix)
            

class KKV(FixedLayoutSource):
    """Hanterar konkurrensverkets databas över upphandlingsmål. Dokumenten
härstammar alltså inte från konkurrensverket, men det är den myndighet
som samlar, strukturerar och tillgängliggör dem."""

    alias = "kkv"
    storage_policy = "dir"
    start_url = "http://www.konkurrensverket.se/domar/DomarKKV/domar.asp"
    document_url_regex = ".*/arende.asp\?id=(?P<basefile>\d+)"
    source_encoding = "iso-8859-1"
    download_iterlinks = False
    download_accept_404 = True
    download_accept_400 = True
    rdf_type = RPUBL.VagledandeDomstolsavgorande  # FIXME: Not all are Vägledande...
    xslt_template = "xsl/myndfskr.xsl" # FIXME: don't we have a better template?
    requesthandler_class = KKVHandler

    _default_creator_predicate = RINFOEX.domstol

    identifiers = {}
    
    # For now we use a simpler basefile-to-uri mapping through these
    # implementations of canonical_uri and coin_uri
    def canonical_uri(self, basefile):
        return "%s%s/%s" % (self.config.url, self.alias, basefile)

    def coin_uri(self, resource, basefile):
        return self.canonical_uri(basefile)
    
    def basefile_from_uri(self, uri):
        basefile_segment = -2 if re.search('/sid\d+.png$',uri) else -1
        return uri.split("/")[basefile_segment].split("?")[0]

    def download_get_first_page(self):
        resp = self.session.get(self.start_url)
        tree = lxml.html.document_fromstring(resp.text)
        tree.make_links_absolute(self.start_url, resolve_base_href=True)
        form = tree.forms[1]
        form.fields['beslutsdatumfrom'] = '2000-01-01'
        # form.fields['beslutsdatumfrom'] = '2018-09-01'
        action = form.action
        parameters = form.form_values()
        # self.log.debug("First Params (%s): %s" % (action, dict(parameters)))
        res = self.session.post(action, data=dict(parameters))
        return res


    def download_single(self, basefile, url):
        headnote = self.store.downloaded_path(basefile, attachment="headnote.html")
        self.download_if_needed(url, basefile, filename=headnote)
        soup = BeautifulSoup(util.readfile(headnote, encoding=self.source_encoding), "lxml")
        beslut = soup.find("a", text=re.compile("\w*Beslut\w*"))
        if not beslut:
            raise errors.DownloadFileNotFoundError("%s contains no PDF link" % url)
        url = beslut.get("href")
        assert url
        return super(KKV, self).download_single(basefile, url)


    def download_get_basefiles(self, source):
        page = 1
        done = False

        while not done:
            # soup = BeautifulSoup(source, "lxml")
            # links = soup.find_all("a", href=re.compile("arende\.asp"))
            # self.log.debug("Links on this page: %s" % ", ".join([x.text for x in links]))
            tree = lxml.html.document_fromstring(source)
            tree.make_links_absolute(self.start_url, resolve_base_href=True)
            self.downloaded_iterlinks = True
            for res in super(KKV, self).download_get_basefiles(tree.iterlinks()):
                yield res
            self.download_iterlinks = False
            done = True
            linktext = str(page+1)
            for element in tree.findall(".//a"):
                if element.text == linktext and element.get("href").startswith("javascript:"):
                    done = False
                    page += 1
                    form = tree.forms[1]
                    form.fields['showpage'] = str(page)
                    action = form.action
                    parameters = form.form_values()
                    self.log.debug("Downloading page %s" % page)
                    # self.log.debug("Params (%s): %s" % (action, dict(parameters)))
                    res = self.session.post(action, data=dict(parameters))
                    source = res.text
                    break

    def extract_head(self, fp, basefile):
        data = util.readfile(self.store.downloaded_path(basefile, attachment="headnote.html"), encoding=self.source_encoding)
        return BeautifulSoup(data, "lxml")

    def infer_identifier(self, basefile):
        return self.identifiers[basefile]

    lblmap = {"Domstol:": "rinfoex:domstol",  # this ad-hoc predicate
                                              # keeps
                                              # attributes_to_resource
                                              # from converting the
                                              # string into a URI,
                                              # which we'd like to
                                              # avoid for now
              "Instans:": "rinfoex:instanstyp",
              "Målnummer:": "rpubl:malnummer",
              "Ärendemening:": "dcterms:title",
              "Beslutsdatum:": "rpubl:avgorandedatum",
              "Leverantör/Sökande:": "rinfoex:leverantor",
              "UM/UE:": "rinfoex:upphandlande",
              "Ärendetyp:": "rinfoex:arendetyp",
              "Avgörande:": "rinfoex:avgorande",
              "Kortreferat:": "dcterms:abstract"}
    def extract_metadata(self, rawhead, basefile):
        d = self.metadata_from_basefile(basefile)
        for row in rawhead.find("table", "tabellram").find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 2:
                continue
            lbl = cells[0].text.strip()
            value = cells[1].text.strip()
            if value and lbl and self.lblmap.get(lbl):
                assert lbl.endswith(":"), "invalid label %s" % lbl
                d[self.lblmap[lbl]] = value
        d["dcterms:issued"] = d["rpubl:avgorandedatum"]
        self.identifiers[basefile] = "%ss dom den %s i mål %s" % (d["rinfoex:domstol"],
                                                                  d["rpubl:avgorandedatum"],
                                                                  d["rpubl:malnummer"])
        return d

    def get_parser(self, basefile, sanitized, initialstate=None, parseconfig="default"):
        def kkv_parser(pdfreader):
            def is_overklagandehanvisning(page):
                # only look at the top 1/4 of the page
                for textbox in page.boundingbox(0, 0, page.height/4, page.width):
                    if str(textbox).strip() in ("HUR MAN ÖVERKLAGAR - PRÖVNINGSTILLSTÅND",
                                                "Hur man överklagar FR-05"):
                        return True
                return False

            def detect_ombud(sokande):
                ombud = False
                for line in sokande:
                    if line.startswith("Ombud:"):
                        ombud = True
                    if ombud and ("firman " in line or "byrå " in line or  "AB" in line or "KB" in line or "HB" in line):
                        return line

            def detect_domare(trailing):
                domare = False
                # first strategy: Whatever line is followed by a known title
                for line in reversed(trailing):
                    if line.lower() in ("förvaltningsrättsfiskal", "rådman", "chefsrådman"):
                        domare = True # next line will contain what we want
                    elif domare:
                        return line
                # second strategy: Whatever line is followed by the föredragande
                for line in reversed(trailing):
                    if line.endswith("har föredragit målet."):
                        domare = True # next line will contain what we want
                    elif domare:
                        return line

            def detect_klagande_type(contact):
                # returns "myndighet", "leverantör", or None
                for line in contact:
                    if line.endswith("kommun"):
                        return "myndighet"
                    elif line.endswith(" AB"):
                        return "leverantör"
                return None
            
                
            def find_headsection(page, heading, startswith=False, bbheight=0.75):
                result = []
                started = False
                for textbox in page.boundingbox(0, 0, page.height*bbheight, page.width):
                    strtextbox = str(textbox).strip()
                    # What to do in the case of OCR errors, eg
                    # "SOKANDE ." instead of "SÖKANDE"?
                    if strtextbox == heading or (startswith and strtextbox.startswith(heading)):
                        started = True
                    elif strtextbox.isupper() and len(strtextbox) > 4:
                        if started:
                            return result
                    elif started:
                        result.append(strtextbox)
                if result:
                    self.log.debug("Possible non-finished headsection %s: %s...%s" % (heading, result[0], result[-1]))
                    return result

            assert isinstance(pdfreader, PDFReader), "Unexpected: %s is not PDFReader" % type(pdfreader)
            # start by remove overklagandehanvisning and all subsequent pages
            for idx, page in enumerate(pdfreader):
                if is_overklagandehanvisning(page):
                    # sanity check: should be max three pages left
                    assert(len(pdfreader) - idx <= 3), "Överklagandehänvisning probably incorrectly detected"
                    self.log.info("%s: Page %s is överklagandehänvisning, skipping this and all following pages" % (basefile, idx+1))
                    pdfreader[:] = pdfreader[:idx]
                    break

            # find crap
            from pudb import set_trace; set_trace()
            sokande = find_headsection(pdfreader[0], "SÖKANDE")
            if sokande:
                # print(",".join(sokande))
                sokandeombud = detect_ombud(sokande)
                if sokandeombud:
                    self.log.info("Sökandeombud: " + sokandeombud)
            else:
                klagande = find_headsection(pdfreader[0], "KLAGANDE")
                if klagande:
                    sokandeombud = detect_ombud(klagande)
                    if sokandeombud:
                        self.log.info("Klagandeombud: " + sokandeombud)
                    klagandetyp = detect_klagande_type(klagande)
                    self.log.info("Klagande: %s" % klagandetyp)
            motpart = find_headsection(pdfreader[0], "MOTPART")
            if motpart:
                # print(",".join(motpart))
                motpartsombud = detect_ombud(motpart)
                if motpartsombud:
                    self.log.info("Motpartsombud: " + motpartsombud)
                    

            trailing = find_headsection(pdfreader[-1], "HUR MAN ÖVERKLAGAR", startswith=True, bbheight=1)
            if trailing:
                domare = detect_domare(trailing)
                if not domare:
                    self.log.warning("Can't detect domare in %s" % ", ".join(trailing))
                self.log.info("Domare: %s" % domare)
            return pdfreader
        return kkv_parser


    def postprocess_doc(self, doc):
        super(KKV, self).postprocess_doc(doc)
        if getattr(doc.body, 'tagname', None) != "body":
            doc.body.tagname = "body"
        doc.body.uri = doc.uri
