# -*- coding: utf-8 -*-
from __future__ import unicode_literals

# From python stdlib
import re

# 3rd party modules
import lxml.html
import requests

# My own stuff
from ferenda import decorators
from ferenda import PDFDocumentRepository
from . import SwedishLegalSource


class JO(SwedishLegalSource, PDFDocumentRepository):

    """Hanterar beslut från Riksdagens Ombudsmän, www.jo.se

    Modulen hanterar hämtande av beslut från JOs webbplats i PDF samt
    omvandlande av dessa till XHTML.

    """
    alias = "jo"
    start_url = "http://www.jo.se/sv/JO-beslut/Soka-JO-beslut/?query=*&pn=1"

    document_url_regex = "http://www.jo.se/PageFiles/(?P<dummy>\d+)/(?P<basefile>\d+\-\d+).pdf"

    def download(self, basefile=None):
        for basefile, url in self.download_get_basefiles(self.start_url):
            self.download_single(basefile, url)

    @decorators.downloadmax
    def download_get_basefiles(self, start_url):
        done = False
        url = start_url
        pagecount = 1
        while not done:
            nextpage = None
            assert "pn=%s" % pagecount in url
            soughtnext = url.replace("pn=%s" % pagecount,
                                     "pn=%s" % (pagecount + 1)),
            self.log.info("Getting page #%s" % pagecount)
            resp = requests.get(url)
            tree = lxml.html.document_fromstring(resp.text)
            tree.make_links_absolute(url, resolve_base_href=True)
            for element, attribute, link, pos in tree.iterlinks():
                m = re.match(self.document_url_regex, link)
                if m:
                    yield m.group("basefile"), link
                elif link == soughtnext:
                    nextpage = link
            if nextpage:
                url = nextpage
            else:
                done = True
