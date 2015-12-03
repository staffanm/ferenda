# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import re
import os
from datetime import datetime
import time
import itertools

from six import text_type as str
from six import binary_type as bytes

# 3rd party
from bs4 import BeautifulSoup
import requests
import requests.exceptions
from rdflib import URIRef, Literal

# My own stuff
from ferenda import util
from ferenda.elements import Body
from ferenda.decorators import downloadmax, recordlastdownload
from . import FixedLayoutSource, FixedLayoutStore, RPUBL


class ARNStore(FixedLayoutStore):

    """Customized DocumentStore that handles multiple download suffixes
    and transforms YYYY-NNN basefiles to YYYY/NNN pathfrags"""

    def basefile_to_pathfrag(self, basefile):
        return basefile.replace("-", "/")

    def pathfrag_to_basefile(self, pathfrag):
        return pathfrag.replace("/", "-", 1)


class ARN(FixedLayoutSource):

    """Hanterar referat från Allmänna Reklamationsnämnden, www.arn.se.

    Modulen hanterar hämtande av referat från ARNs webbplats, omvandlande
    av dessa till XHTML1.1+RDFa, samt transformering till browserfärdig
    HTML5.
    """

    alias = "arn"
    # xslt_template = "res/xsl/arn.xsl"
    start_url = ("http://adokweb.arn.se/digiforms/sessionInitializer?"
                 "processName=SearchRefCasesProcess")
    documentstore_class = ARNStore
    rdf_type = RPUBL.VagledandeMyndighetsavgorande
    storage_policy = "dir"
    urispace_segment = "avg/arn"

    def metadata_from_basefile(self, basefile):
        attribs = super(ARN, self).metadata_from_basefile(basefile)
        attribs["rpubl:diarienummer"] = basefile
        attribs["dcterms:publisher"] = self.lookup_resource(
                    'Allmänna reklamationsnämnden')
        return attribs

    @recordlastdownload
    def download(self, basefile=None):
        self.session = requests.Session()
        resp = self.session.get(self.start_url)
        soup = BeautifulSoup(resp.text, "lxml")
        action = soup.find("form")["action"]

        if ('lastdownload' in self.config and
                self.config.lastdownload and
                not self.config.refresh):
            d = self.config.lastdownload
            datefrom = '%d-%02d-%02d' % (d.year, d.month, d.day)
            dateto = '%d-01-01' % (d.year + 1)
        else:
            # only fetch one year at a time
            datefrom = '1992-01-01'
            dateto = '1993-01-01'

        params = {
            '/root/searchTemplate/decision': 'obegransad',
            '/root/searchTemplate/decisionDateFrom': datefrom,
            '/root/searchTemplate/decisionDateTo': dateto,
            '/root/searchTemplate/department': 'alla',
            '/root/searchTemplate/journalId': '',
            '/root/searchTemplate/searchExpression': '',
            '_cParam0': 'method=search',
            '_cmdName': 'cmd_process_next',
            '_validate': 'page'
        }

        for basefile, url, fragment in self.download_get_basefiles((action, params)):
            if (self.config.refresh or
                    (not os.path.exists(self.store.downloaded_path(basefile)))):
                self.download_single(basefile, url, fragment)

    @downloadmax
    def download_get_basefiles(self, args):
        action, params = args
        done = False
        self.log.debug("Retrieving all results from %s to %s" %
                       (params['/root/searchTemplate/decisionDateFrom'],
                        params['/root/searchTemplate/decisionDateTo']))
        paramcopy = dict(params)
        while not done:
            # First we need to use the files argument to send the POST
            # request as multipart/form-data
            req = requests.Request(
                "POST", action, cookies=self.session.cookies, files=paramcopy).prepare()
            # Then we need to remove filename
            # from req.body in an unsupported manner in order not to
            # upset the sensitive server
            body = req.body
            if isinstance(body, bytes):
                body = body.decode()  # should be pure ascii
            req.body = re.sub(
                '; filename="[\w\-\/]+"', '', body).encode()
            req.headers['Content-Length'] = str(len(req.body))
            # And finally we have to allow RFC-violating redirects for POST

            resp = False
            remaining_attempts = 5
            while (not resp) and (remaining_attempts > 0):
                try:
                    resp = self.session.send(req, allow_redirects=True)
                except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
                    self.log.warning(
                        "Failed to POST %s: error %s (%s remaining attempts)" % (action, e, remaining_attempts))
                    remaining_attempts -= 1
                    time.sleep(1)

            soup = BeautifulSoup(resp.text)
            for link in soup.find_all(
                    "input", "standardlink", onclick=re.compile("javascript:window.open")):
                url = link['onclick'][
                    24:-
                    2]  # remove 'javascript:window.open' call around the url
                # this probably wont break...
                fragment = link.find_parent("table").find_parent("table")
                basefile = fragment.find_all("div", "strongstandardtext")[1].text
                yield basefile, url, fragment
            if soup.find("input", value="Nästa sida"):
                self.log.debug("Now retrieving next page in current search")
                paramcopy = {'_cParam0': "method=nextPage",
                             '_validate': "none",
                             '_cmdName': "cmd_process_next"}
            else:
                fromYear = int(params['/root/searchTemplate/decisionDateFrom'][:4])
                if fromYear >= datetime.now().year:
                    done = True
                else:
                    # advance one year
                    params[
                        '/root/searchTemplate/decisionDateFrom'] = "%s-01-01" % str(fromYear + 1)
                    params[
                        '/root/searchTemplate/decisionDateTo'] = "%s-01-01" % str(fromYear + 2)
                    self.log.debug("Now retrieving all results from %s to %s" %
                                   (params['/root/searchTemplate/decisionDateFrom'],
                                    params['/root/searchTemplate/decisionDateTo']))
                    paramcopy = dict(params)
                    # restart the search, so that poor digiforms
                    # doesn't get confused

                    resp = False
                    remaining_attempts = 5
                    while (not resp) and (remaining_attempts > 0):
                        try:
                            resp = self.session.get(self.start_url)
                        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
                            self.log.warning(
                                "Failed to POST %s: error %s (%s remaining attempts)" % (action, e, remaining_attempts))
                            remaining_attempts -= 1
                            time.sleep(1)

                    soup = BeautifulSoup(resp.text)
                    action = soup.find("form")["action"]

    def download_name_file(self, tmpfile, basefile, assumedfile):
        with open(tmpfile, "rb") as fp:
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
                    "%s has unknown signature %r -- don't know what kind of file it is" % (filename, sig))
                doctype = ".pdf"  # don't do anything
        return self.store.path(basefile, 'downloaded', doctype)

    def download_single(self, basefile, url, fragment):
        ret = super(ARN, self).download_single(basefile, url)
        if ret:
            # the HTML fragment from the search result page contains
            # metadata not available in the main document, so save it
            # as fragment.html
            with self.store.open_downloaded(basefile, mode="wb",
                                            attachment="fragment.html") as fp:
                fp.write(str(fragment).encode("utf-8"))
        return ret

    def extract_head(self, fp, basefile):
        # the fp contains the PDF file, but most of the metadata is in
        # stored HTML fragment attachment. So we open that separately.
        fragment = self.store.downloaded_path(basefile, attachment="fragment.html")
        return BeautifulSoup(util.readfile(fragment, encoding="utf-8"), "lxml")


    def extract_metadata(self, soup, basefile):
        d = self.metadata_from_basefile(basefile)
        def nextcell(key):
            cell = soup.find(text=key)
            if cell:
                return cell.find_parent("td").find_next_sibling("td").get_text().strip()
            else:
                raise KeyError("Could not find cell key %s" % key)
        d.update({'dcterms:identifier': "ARN %s" % basefile,
                  'rpubl:arendenummer': nextcell("Änr"),
                  'rpubl:diarienummer': nextcell("Änr"),
                  'rpubl:avgorandedatum': nextcell("Avgörande"),
                  'dcterms:subject': Literal(nextcell("Avdelning"), lang="sv"),
                  'dcterms:title': soup.table.find_all("tr")[3].get_text(),
                  'dcterms:issued': nextcell("Avgörande")
        })
        assert d["rpubl:diarienummer"] == basefile, "Doc metadata differs from basefile"
        return d

    def sanitize_metadata(self, attribs, basefile):
        attribs['dcterms:title'] = Literal(
            re.sub("Avgörande \d+-\d+-\d+; \d+-\d+\.?",
                   "", util.normalize_space(attribs['dcterms:title'])),
            lang="sv")
        return attribs

    def get_parser(self, basefile, sanitized):
        return lambda stream: Body(list(stream))

    def tokenize(self, reader):
        def gluecondition(textbox, nextbox, prevbox):
            linespacing = 7
            res = (textbox.font.family == nextbox.font.family and
                   textbox.font.size == nextbox.font.size and
                   textbox.top + textbox.height + linespacing >= nextbox.top and
                   nextbox.top > prevbox.top)
            return res
        return reader.textboxes(gluecondition)

    def create_external_resources(self, doc):
        pass

    def tabs(self):
        if self.config.tabs:
            return [("ARN", self.dataset_uri())]
        else:
            return []
