# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import re
import os

from six import text_type as str

# 3rd party
from bs4 import BeautifulSoup
import requests

# My own stuff
from ferenda import PDFDocumentRepository
from ferenda import util
from ferenda.decorators import downloadmax
from ferenda.elements import UnicodeElement, CompoundElement, serialize
from . import SwedishLegalSource


class ARN(SwedishLegalSource, PDFDocumentRepository):

    """Hanterar referat från Allmänna Reklamationsnämnden, www.arn.se.

    Modulen hanterar hämtande av referat från ARNs webbplats, omvandlande
    av dessa till XHTML1.1+RDFa, samt transformering till browserfärdig
    HTML5.
    """
    alias = "arn"
    xslt_template = "res/xsl/arn.xsl"
    start_url = "http://adokweb.arn.se/digiforms/sessionInitializer?processName=SearchRefCasesProcess"

    def download(self, basefile=None):
        self.session = requests.Session()
        resp = self.session.get(self.start_url)
        soup = BeautifulSoup(resp.text)
        action = soup.find("form")["action"]

        params = {
            '/root/searchTemplate/decision': 'obegransad',
            '/root/searchTemplate/decisionDateFrom': '1992-01-01',
            '/root/searchTemplate/decisionDateTo': '1993-01-01',
            '/root/searchTemplate/department': 'alla',
            '/root/searchTemplate/journalId': '',
            '/root/searchTemplate/searchExpression': '',
            '_cParam0': 'method=search',
            '_cmdName': 'cmd_process_next',
            '_validate': 'page'
        }

        for basefile, url in self.download_get_basefiles((action, params)):
            self.download_single(basefile, url)

    @downloadmax
    def download_get_basefiles(self, args):
        action, params = args
        done = False
        while not done:
            # First we need to use the files argument to send the POST
            # request as multipart/form-data
            req = requests.Request(
                "POST", action, cookies=self.session.cookies, files=params).prepare()
            # Then we need to remove filename and content-type fields
            # from req.body in an unsupported manner in order not to
            # upset the sensitive server
            req.body = re.sub(
                '; filename="[\w\-\/]+"\r\nContent-Type: [\w\-\/]+', '', req.body).encode()
            req.headers['Content-Length'] = str(len(req.body))
            # And finally we have to allow RFC-violating redirects for POST
            resp = self.session.send(req, allow_redirects=True)
            soup = BeautifulSoup(resp.text)
            for link in soup.find_all("input", "standardlink", onclick=re.compile("javascript:window.open")):
                url = link['onclick'][24:-2]  # remove 'javascript:window.open' call around the url
                # this probably wont break...
                basefile = link.find_parent("table").find_parent(
                    "table").find_all("div", "strongstandardtext")[1].text
                yield basefile, url
            if soup.find('Nästa sida'):
                params = {}
                action = []
            else:
                done = True

    def download_single(self, basefile, url):
        super(ARN, self).download_single(basefile, url)
        # after downloading: see if our PDF in reality was something else
        # FIXME: we should do this prior to .download_if_needed...
        d = self.store.downloaded_path(basefile)
        if os.path.exists(d):
            with open(d, "rb") as fp:
                sig = fp.read(4)
                if sig == b'\xffWPC':
                    doctype = ".wpd"
                elif sig == b'\xd0\xcf\x11\xe0':
                    doctype = ".doc"
                elif sig == b'PK\x03\x04':
                    doctype = ".docx"
                elif sig == b'{\\rt':
                    doctype = ".rtf"
                elif sig == b'%PDF':
                    doctype = ".pdf"
                else:
                    self.log.warning(
                        "%s has unknown signature %r -- don't know what kind of file it is" % (d, sig))
                    doctype = ".pdf"  # don't do anything
            if doctype != '.pdf':
                util.robust_rename(d, d.replace(".pdf", doctype))

    def parse(self, doc):
        # find out if we have a .pdf or a .doc (possibly .wpd?)
        type = self.guess_type
        self.parse_from_pdf(self, doc, "...")

    def parse_from_pdf(self, pdfreader, doc):
        pass

    def parse_from_word(self, wordreader, doc):
        pass

    def parse_from_wpd(self, wpd, doc):
        pass
