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
from ferenda import PDFDocumentRepository, DocumentStore, PDFReader, WordReader, Describer
from ferenda import util
from ferenda.decorators import downloadmax, recordlastdownload, managedparsing
from ferenda.elements import Body, UnicodeElement, CompoundElement, serialize
from . import SwedishLegalSource, SwedishCitationParser, RPUBL
from ferenda.sources.legal.se.legalref import LegalRef


class ARNStore(DocumentStore):
    """Customized DocumentStore."""
    def basefile_to_pathfrag(self, basefile):
        return basefile.replace("-", "/")

    def pathfrag_to_basefile(self, pathfrag):
        return pathfrag.replace("/","-", 1)

    def downloaded_path(self, basefile, version=None, attachment=None, suffix=None):
        if not suffix:
            if os.path.exists(self.path(basefile, "downloaded", ".wpd")):
                suffix = ".wpd"
            elif os.path.exists(self.path(basefile, "downloaded", ".doc")):
                suffix = ".doc"
            elif os.path.exists(self.path(basefile, "downloaded", ".docx")):
                suffix = ".docx"
            elif os.path.exists(self.path(basefile, "downloaded", ".rtf")):
                suffix = ".rtf"
            elif os.path.exists(self.path(basefile, "downloaded", ".pdf")):
                suffix = ".pdf"
            else:
                suffix = self.downloaded_suffix
        return self.path(basefile, "downloaded", suffix, version, attachment)

    def list_basefiles_for(self, action, basedir=None):
        if not basedir:
            basedir = self.datadir
        if action == "parse":
            d = os.path.sep.join((basedir, "downloaded"))
            if not os.path.exists(d):
                return
            for x in sorted(itertools.chain(util.list_dirs(d, ".wpd"),
                                            util.list_dirs(d, ".rtf"),
                                            util.list_dirs(d, ".doc"),
                                            util.list_dirs(d, ".docx"),
                                            util.list_dirs(d, ".pdf"))):
                suffix = "/index"+ os.path.splitext(x)[1]
                pathfrag = x[len(d) + 1:-len(suffix)]
                yield self.pathfrag_to_basefile(pathfrag)
        else:
            for x in super(ARNStore, self).list_basefiles_for(action, basedir):
                yield x

class ARN(SwedishLegalSource, PDFDocumentRepository):

    """Hanterar referat från Allmänna Reklamationsnämnden, www.arn.se.

    Modulen hanterar hämtande av referat från ARNs webbplats, omvandlande
    av dessa till XHTML1.1+RDFa, samt transformering till browserfärdig
    HTML5.
    """

    alias = "arn"
    xslt_template = "res/xsl/arn.xsl"
    start_url = "http://adokweb.arn.se/digiforms/sessionInitializer?processName=SearchRefCasesProcess"
    documentstore_class = ARNStore
    rdf_type = RPUBL.VagledandeMyndighetsavgorande
    
    @recordlastdownload
    def download(self, basefile=None):
        self.session = requests.Session()
        resp = self.session.get(self.start_url)
        soup = BeautifulSoup(resp.text)
        action = soup.find("form")["action"]

        if self.config.lastdownload and not self.config.refresh:
            d = self.config.lastdownload
            datefrom = '%d-%02d-%02d' % (d.year, d.month, d.day)
            dateto = '%d-01-01' % (d.year+1)
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
                body = body.decode() # should be pure ascii
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
            for link in soup.find_all("input", "standardlink", onclick=re.compile("javascript:window.open")):
                url = link['onclick'][24:-2]  # remove 'javascript:window.open' call around the url
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
                    params['/root/searchTemplate/decisionDateFrom'] = "%s-01-01" % str(fromYear+1)
                    params['/root/searchTemplate/decisionDateTo'] = "%s-01-01" % str(fromYear+2)
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

    
    def guess_type(self, filename):
        with open(filename, "rb") as fp:
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
        return doctype
        
                    
    def download_single(self, basefile, url, fragment):
        ret = super(ARN, self).download_single(basefile, url)
        # after downloading: see if our PDF in reality was something else
        # FIXME: we should do this prior to .download_if_needed...
        if ret:
            d = self.store.downloaded_path(basefile)
            if os.path.exists(d) and not d.endswith(".pdf"):
                doctype = self.guess_type(d)
                if doctype != '.pdf':
                    util.robust_rename(d, d.replace(".pdf", doctype))

            # the HTML fragment from the search result page contains
            # metadata not available in the main document, so save it
            # as fragment.html
            with self.store.open_downloaded(basefile, mode="w", attachment="fragment.html") as fp:
                fp.write(str(fragment))
        return ret
        
    @managedparsing
    def parse(self, doc):
        def nextcell(key):
            cell = soup.find(text=key)
            if cell:
                return cell.find_parent("td").find_next_sibling("td").get_text().strip()
            else:
                raise KeyError("Could not find cell key %s" % key)
        downloaded = self.store.downloaded_path(doc.basefile)
        fragment = self.store.downloaded_path(doc.basefile, attachment="fragment.html")
        soup = BeautifulSoup(util.readfile(fragment, encoding="utf-8"))
        desc = Describer(doc.meta, doc.uri)
        desc.rdftype(self.rdf_type)
        desc.value(self.ns['prov'].wasGeneratedBy, self.qualified_class_name())
        desc.value(self.ns['dct'].identifier, "ARN %s" % doc.basefile)
        desc.value(self.ns['rpubl'].arendenummer, nextcell("Änr"))
        desc.value(self.ns['rpubl'].avgorandedatum, nextcell("Avgörande"))
        desc.value(self.ns['dct'].subject, nextcell("Avdelning"), lang="sv")
        title = util.normalize_space(soup.table.find_all("tr")[3].get_text())
        title = re.sub("Avgörande \d+-\d+-\d+; \d+-\d+\.?", "", title)
        desc.value(self.ns['dct'].title, title.strip(), lang="sv")
        # dct:issued is required, but we only have rpubl.avgorandedatum
        desc.value(self.ns['dct'].issued,
                desc.getvalue(self.ns['rpubl'].avgorandedatum))
        filetype = os.path.splitext(downloaded)[1]
        if filetype == ".pdf":
            # make sure it really is a PDF
            doctype = self.guess_type(downloaded)
            if doctype != ".pdf":
                util.robust_rename(downloaded,
                                   downloaded.replace(".pdf", doctype))
                downloaded = downloaded.replace(".pdf", doctype)
                filetype = doctype

            self.parse_from_pdf(doc, downloaded, filetype=filetype)

        # for uniformity, we try to treat everything as PDF by
        # converting, even .doc/.docx documents are converted to PDF
        # elif filetype in (".doc", ".docx"):
        # self.parse_from_word(doc, downloaded)
        else:
            self.parse_from_pdf(doc, downloaded, filetype=filetype)
        return True

    def parse_from_pdf(self, doc, filename, filetype=".pdf"):
        def gluecondition(textbox, nextbox, prevbox):
            linespacing = 7
            return (textbox.getfont() == nextbox.getfont() and 
                    textbox.top + textbox.height + linespacing >= nextbox.top)
            
        reader = PDFReader()
        convert_to_pdf = filetype != ".pdf"
        workdir = os.path.dirname(self.store.intermediate_path(doc.basefile))
        if self.config.compress == "bz2":
            keep_xml = "bz2"
        else:
            keep_xml = True
        reader.read(filename, workdir,
                    images=self.config.pdfimages,
                    convert_to_pdf=convert_to_pdf,
                    keep_xml=keep_xml)
        textboxes = reader.textboxes(gluecondition)
        doc.body = Body(textboxes)

        if not hasattr(self, 'ref_parser'):
            self.ref_parser = LegalRef(LegalRef.RATTSFALL, LegalRef.LAGRUM, LegalRef.FORARBETEN)
        citparser = SwedishCitationParser(self.ref_parser)
        citparser.parse_recursive(doc.body)

    def create_external_resources(self, doc):
        pass
