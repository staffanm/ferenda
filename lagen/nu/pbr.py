# -*- coding: utf-8 -*-
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
from builtins import *

import re
import os
from urllib.parse import urljoin
from datetime import datetime


from bs4 import BeautifulSoup

from ferenda import util
from ferenda import DocumentEntry
from ferenda.sources.legal.se import SwedishLegalSource



class PBR(SwedishLegalSource):
    alias = "pbr"
    start_url = "http://www.pbr.se/malregister?sok=&mnr=&mntp=hfd&sak=&dtx=&ktp=&knm=&kob=&mnm=&mob=&klass=&jus=&tes=&lag=&prf=&d1typ=&d1min=&d1max=&d2typ=&d2min=&d2max=&patent=1&vm=1&monster=1&namn=1&vxt=1&ubevis=1&u_bifall=1&u_bifall2=1&u_fastst=1&u_avslag=1&u_avskr=1&u_avvis=1&u_annan=1&vagledande=1&intressant=1&ovrigt=1&esf=1&ekf=1"
    download_iterlinks = False
    storage_policy = "dir"

    def download_get_basefiles(self, source):
        current_url = self.start_url
        soup = BeautifulSoup(source, "lxml")
        done = False
        pagecnt = 1
        while not done:
            self.log.debug("loading page %s of results" % pagecnt)
            for f in soup.find_all("div", "rad"):
                link_el =  f.find("a", "block")
                link = urljoin(self.start_url, link_el["href"])
                basefile = link.rsplit("?malnr=")[1].replace(" ", "_")
                # we store the snippet since it's the only place where the
                # PRVnr metadata is shown
                with self.store.open_downloaded(basefile, attachment="snippet.html", mode="w") as fp:
                    fp.write(str(f))
                yield basefile, link
            next_el = soup.find("a", text=">")
            if next_el:
                pagecnt += 1
                current_url = urljoin(current_url, next_el["href"])
                resp = self.session.get(current_url)
                resp.raise_for_status()
                soup = BeautifulSoup(resp.text, "lxml")
            else:
                done = True


    def download_single(self, basefile, url):
        updated = False
        created = False
        filename = self.store.downloaded_path(basefile)
        created = not os.path.exists(filename)
        # util.print_open_fds()
        if self.download_if_needed(url, basefile):
            if created:
                self.log.info("%s: downloaded from %s" % (basefile, url))
            else:
                self.log.info(
                    "%s: downloaded new version from %s" % (basefile, url))
            updated = True
        else:
            self.log.debug("%s: exists and is unchanged" % basefile)
        soup = BeautifulSoup(util.readfile(filename), "lxml")
        for pdflink in soup.find_all("a", href=re.compile("\.pdf$")):
            slug =  "-".join(pdflink["href"].rsplit("/")[-2:])
            attachment_path = self.store.downloaded_path(basefile, attachment=slug)
            self.download_if_needed(urljoin(url, pdflink["href"]), basefile, filename=attachment_path)
        vm = soup.find("a", text="Visa Varumärke")
        if vm:
            attachment_path = self.store.downloaded_path(basefile, attachment="varumarke.jpg")
            attachment_url = re.search("http[^'\"]*", vm["href"]).group(0)
            self.download_if_needed(attachment_url, basefile, filename=attachment_path)

        entry = DocumentEntry(self.store.documententry_path(basefile))
        now = datetime.now()
        entry.orig_url = url
        if created:
            entry.orig_created = now
        if updated:
            entry.orig_updated = now
        entry.orig_checked = now
        entry.save()

