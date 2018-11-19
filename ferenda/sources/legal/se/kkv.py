# -*- coding: utf-8 -*-
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
from builtins import *

import re

import lxml.html
from bs4 import BeautifulSoup

from ferenda import util
from . import RPUBL
from .fixedlayoutsource import FixedLayoutSource, FixedLayoutStore



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

    
    def download_get_first_page(self):
        resp = self.session.get(self.start_url)
        tree = lxml.html.document_fromstring(resp.text)
        tree.make_links_absolute(self.start_url, resolve_base_href=True)
        form = tree.forms[1]
        #form.fields['beslutsdatumfrom'] = '2000-01-01'
        form.fields['beslutsdatumfrom'] = '2018-09-01'
        action = form.action
        parameters = form.form_values()
        # self.log.debug("First Params (%s): %s" % (action, dict(parameters)))
        res = self.session.post(action, data=dict(parameters))
        return res


    def download_single(self, basefile, url):
        headnote = self.store.downloaded_path(basefile, attachment="headnote.html")
        self.download_if_needed(url, basefile, filename=headnote)
        soup = BeautifulSoup(util.readfile(headnote, encoding=self.source_encoding), "lxml")
        url = soup.find("a", text=re.compile("\w*Beslut\w*")).get("href")
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
