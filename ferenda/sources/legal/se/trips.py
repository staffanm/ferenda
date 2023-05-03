# -*- coding: utf-8 -*-
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
from builtins import *

# Base class for fetching data from an ancient database system used by
# swedish gov IT...  FIXME: Now that the ancient database system has
# been retired (early 2016), so should probably this class.
import os
import re
from urllib.parse import quote, urljoin
import codecs
from bz2 import BZ2File

import requests
import lxml.html
from bs4 import BeautifulSoup

from ferenda.decorators import downloadmax
from ferenda.errors import DocumentRemovedError
from ferenda import util
from . import SwedishLegalSource


class NoMoreLinks(Exception):

    def __init__(self, nextpage=None):
        super(NoMoreLinks, self).__init__()
        self.nextpage = nextpage


class Trips(SwedishLegalSource):
    alias = None  # abstract class
    basefile_regex = "(?P<basefile>\d{4}:\d+)$"
    source_encoding = "utf-8"

    @classmethod
    def get_default_options(cls):
        opts = super(Trips, cls).get_default_options()
        opts['ipbasedurls'] = False
        return opts

    def download(self, basefile=None):
        if self.config.ipbasedurls:
            self._make_ipbasedurls()
        if basefile:
            return self.download_single(basefile)
        refresh = self.config.refresh
        updated = False
        for basefile, url in self.download_get_basefiles(None):
            if (refresh or
                    (not os.path.exists(self.store.downloaded_path(basefile)))):
                ret = self.download_single(basefile, url)
                updated = updated or ret
        return updated

    def _make_ipbasedurls(self):
        import socket
        addrs = socket.getaddrinfo("rkrattsbaser.gov.se", 80)
        # grab the first IPv4 number
        ip = [addr[4][0] for addr in addrs if addr[0] == socket.AF_INET][0]
        self.log.debug("Changing rkrattsbaser.gov.se to %s in all URLs" % ip)
        for p in ('start_url',
                  'document_url_template',
                  'document_sfsr_url_template',
                  'document_sfsr_change_url_template'):
            if hasattr(self, p):
                setattr(self, p,
                        getattr(self, p).replace('rkrattsbaser.gov.se',
                                                 ip))

    @downloadmax
    def download_get_basefiles(self, nullparams):
        done = False
        url = self.start_url.format(c=self.config)
        pagecount = 1
        while not done:
            self.log.debug("Starting at %s" % url)
            resp = requests.get(url)
            soup = BeautifulSoup(resp.text, "lxml")
            try:
                for basefile, url in self.download_get_basefiles_page(soup):
                    yield basefile, url
            except NoMoreLinks as e:
                if e.nextpage:
                    pagecount += 1
                    url = e.nextpage
                    self.log.debug("Getting page #%s of results" % pagecount)
                else:
                    done = True

    def download_get_basefiles_page(self, soup):
        nextpage = None
        for hit in soup.findAll("div", "search-hit-info-num"):
            basefile = hit.text.split(": ", 1)[1].strip()
            m = re.search(self.basefile_regex, basefile)
            if m:
                basefile = m.group()
            else:
                self.log.warning("Couldn't find a basefile in this label: %r" % basefile)
                continue
            sbasefile = self.sanitize_basefile(basefile)
            if sbasefile != basefile:
                self.log.debug("%s: normalized from %s" % (sbasefile, basefile))
            year, ordinal = basefile.split(":")
            docurl = self.document_url_template % locals()
            yield(sbasefile, docurl)
        nextpage = soup.find("div", "search-opt-next").a
        if nextpage:
            nextpage = urljoin(self.start_url,
                               nextpage.get("href"))
        raise NoMoreLinks(nextpage)
#
#    def download_single(self, basefile, url=None):
#        # explicitly call superclass' download_single WITHOUT url
#        # parameter. The reason is so that we construct the url
#        # through self.remote_url, which provides permanent urls to
#        # the wanted documents, instead of the temporary/session id
#        # based urls that download_get_basefiles can provide
#        return super(Trips, self).download_single(basefile)
#
    def download_is_different(self, existing, new):
        if existing.endswith(".html"):
            # load both existing and new into a BeautifulSoup object, then
            # compare the first <pre> element
            existing_soup = BeautifulSoup(
                util.readfile(
                    existing,
                    encoding=self.source_encoding), "lxml")
            new_soup = BeautifulSoup(util.readfile(new, encoding=self.source_encoding), "lxml")
            existing = existing_soup.find("div", "search-results-content")
            new = new_soup.find("div", "search-results-content")
            try:
                return existing != new
            except RuntimeError: # can happen with at least v4.4.1 of beautifulsoup
                return True
        else:
            return super(Trips, self).download_is_different(existing, new)

    def remote_url(self, basefile):
        return self.document_url_template % {'basefile': quote(basefile)}

    def metadata_from_basefile(self, basefile):
        a = super(Trips, self).metadata_from_basefile(basefile)
        a["rpubl:arsutgava"], a["rpubl:lopnummer"] = basefile.split(":", 1)
        return a

    def _extract_text(self, basefile, attachment=None, version=None):
        txt = self._extract_text_inner(basefile, attachment, version)
        with self.store.open_intermediate(basefile, mode="wb", version=version) as fp:
            fp.write(txt.encode(self.source_encoding))
        return self.store.open_intermediate(basefile, "rb", version=version)


    def _extract_text_inner(self, basefile, attachment=None, version=None, encoding=None):
        if not attachment and self.store.storage_policy == "dir":
            attachment = "index.html"
        path = self.store.downloaded_path(basefile, attachment=attachment, version=version)
        if encoding is None:
            encoding = self._sniff_encoding(path)
        soup = BeautifulSoup(util.readfile(path, encoding=encoding), "lxml")
        if encoding == "utf-8":
            content = soup.find("div", "search-results-content")
            body = content.find("div", "body-text")
            if not body:
                raise DocumentRemovedError("%s has no body-text div" % basefile,
                                           dummyfile=self.store.parsed_path(basefile))
            if body.string:
                body.string = "----------------------------------------------------------------\n\n" + body.string
                txt = content.text
            else:
                if body.text.strip():
                    # meaning we have text, just not as a single
                    # textnode within the <div>. This usually means
                    # that some < > characters have not been escaped
                    # in the source material, and are misinterpreted
                    # by BS4. We warn and salvage what we can.
                    self.log.warning("Plaintext probably contains unescaped angle brackets, causing an incorrect HTML interpretation")
                    tmptext = body.text
                    body.clear()
                    body.string = "----------------------------------------------------------------\n\n" + tmptext
                    txt = content.text
                else:
                    txt = content.text + "----------------------------------------------------------------\n\n"
        else:
            # some archival pages have a different tag structure
            body = soup.find("pre")
            body.hr.string = "----------------------------------------------------------------\n\n"
            txt = body.text
        # the body of the text uses CRLF, but the header uses only
        # LF. Convert to only LF.
        txt = txt.replace("\r", "")
        return txt
        
    def _sniff_encoding(self, path):
        # when handling archival material, encoding can be both utf-8
        # and latin-1. We could use chardet, but it's probably better
        # to sniff the start of the file since we know that only utf-8
        # files use the html5 doctype
        with open(path, 'rb') as fp:
            rawhead = fp.read(256)
        return "utf-8" if b'<!DOCTYPE html>' in rawhead else "latin-1" 
        
