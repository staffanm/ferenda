# -*- coding: utf-8 -*-
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
from builtins import *

# A number of different classes each fetching the same data from
# different sources (and with different data formats and data fidelity)
import os
import re
import functools
import codecs
from datetime import datetime, timedelta

from bs4 import BeautifulSoup
from rdflib import Literal, URIRef, Namespace
from rdflib.namespace import DCTERMS, XSD, RDFS
import requests

from . import (SwedishLegalSource, SwedishLegalStore, FixedLayoutSource,
               Trips, Regeringen, RPUBL, Offtryck)
from ferenda import CompositeRepository, CompositeStore
from ferenda import TextReader
from ferenda import util
from ferenda import PDFAnalyzer
from ferenda.decorators import downloadmax, recordlastdownload
from ferenda.elements import Body, Heading, ListItem, Paragraph
from ferenda.errors import DocumentRemovedError
from ferenda.compat import urljoin

# custom style analyzer
class DirAnalyzer(PDFAnalyzer):
    # direktiv has no footers
    footer_significance_threshold = 0

    def analyze_styles(self, styles):
        styledefs = {}
        ds = styles.most_common(1)[0][0]
        styledefs['default'] = self.fontdict(ds)
        # Largest style: used for the text "Kommittédirektiv" on the frontpage
        # 2nd largest: used for the title
        # 3rd largest: used for the id (eg "Dir. 2014:158") on the frontpage.
        # 4th largest (same size as body text but bold): h1
        # 5th largest (same size as body text but italic): h2
        (ts, dummy, h1, h2) = sorted(styles.keys(), key=self.fontsize_key,
                                     reverse=True)[1:5]
        styledefs['title'] = self.fontdict(ts)
        styledefs['h1'] = self.fontdict(h1)
        styledefs['h2'] = self.fontdict(h2)
        return styledefs


class Continuation(object):
    pass


class DirTrips(Trips):
    """Downloads Direktiv in plain text format from http://rkrattsbaser.gov.se/dir/"""

    alias = "dirtrips"
    start_url = "http://rkrattsbaser.gov.se/dir/adv?sort=asc"
    document_url_template = "http://rkrattsbaser.gov.se/dir?bet=%(basefile)s"
    rdf_type = RPUBL.Kommittedirektiv

    @recordlastdownload
    def download(self, basefile=None):
        if basefile:
            return super(DirTrips, self).download(basefile)
        else:
            if 'lastdownload' in self.config and self.config.lastdownload and not self.config.refresh:
                startdate = self.config.lastdownload - timedelta(days=30)
                self.start_url += "&UDAT=%s+till+%s" % (
                    datetime.strftime(startdate, "%Y-%m-%d"),
                    datetime.strftime(datetime.now(), "%Y-%m-%d"))
            super(DirTrips, self).download()


    def downloaded_to_intermediate(self, basefile):
        return self._extract_text(basefile)

    def extract_head(self, fp, basefile):
        textheader = fp.read(2048)
        if not isinstance(textheader, str):
            # Depending on whether the fp is opened through standard
            # open() or bz2.BZ2File() in self.parse_open(), it might
            # return bytes or unicode strings. This seem to be a
            # problem in BZ2File (or how we use it). Just roll with it.
            # 
            # if the very last byte is the start of a multi-byte UTF-8
            # character, skip it so that we don't get a unicodedecode
            # error because of the incomplete character. In py2, wrap
            # in future.types.newbytes to get a py3 compatible
            # interface.
            textheader = bytes(textheader)
            if textheader[-1] == ord(bytes(b'\xc3')):
                textheader = textheader[:-1]
            textheader = textheader.decode(self.source_encoding)
        idx = textheader.index("-"*64)
        header = textheader[:idx]
        fp.seek(len(header.encode("utf-8")) + 66)
        return header

    def extract_metadata(self, rawheader, basefile):  # -> dict
        predicates = {'Departement': "rpubl:departement",
                      'Beslut': "rpubl:beslutsdatum"}
        headers = [x.strip() for x in rawheader.split("\n\n") if x.strip()]
        title, identifier = headers[0].rsplit(", ", 1)
        d = self.metadata_from_basefile(basefile)
        d.update({'dcterms:identifier': identifier.strip(),
                  'dcterms:title': title.strip()})
        if d['dcterms:title'] == "Utgår":
            raise DocumentRemovedError("%s: Removed" % basefile,
                                       dummyfile=self.store.parsed_path(basefile))
        for header in headers[1:]:
            key, val = header.split(":")
            d[predicates[key.strip()]] = val.strip()
        d["dcterms:publisher"] = self.lookup_resource("Regeringskansliet")
        if "rpubl:beslutsdatum" in d:
            d["dcterms:issued"] = d["rpubl:beslutsdatum"]  # best we can do
        return d
    
    def sanitize_rubrik(self, rubrik):
        if rubrik == "Utgår":
            raise DocumentRemovedError()
        rubrik = re.sub("^/r2/ ", "", rubrik)
        return Literal(rubrik, lang="sv")

    def sanitize_identifier(self, identifier):
        # "Dir.1994:111" -> "Dir. 1994:111"
        if re.match("Dir.\d+", identifier):
            identifier = "Dir. " + identifier[4:]
        if not identifier.startswith("Dir. "):
            identifier = "Dir. " + identifier
        return Literal(identifier)


    def parse_body(self, fp, basefile):
        current_type = None
        rawtext = fp.read().decode(self.source_encoding)
        # remove whitespace on otherwise empty lines
        rawtext = re.sub("\n\t\n", "\n\n", rawtext)
        reader = TextReader(string=rawtext,
                            linesep=TextReader.UNIX)
        body = Body()
        for p in reader.getiterator(reader.readparagraph):
            new_type = self.guess_type(p, current_type)
            # if not new_type == None:
            #    print "Guessed %s for %r" % (new_type.__name__,p[:20])
            if new_type is None:
                pass
            elif new_type == Continuation and len(body) > 0:
                # Don't create a new text node, add this text to the last
                # text node created
                para = body.pop()
                para.append(p)
                body.append(para)
            else:
                if new_type == Continuation:
                    new_type = Paragraph
                body.append(new_type([p]))
                current_type = new_type

        # LegalRef needs to be a little smarter and not parse refs
        # like "dir. 2004:55" and "(N2004:13)" as SFS references
        # before we enable it.
#         parser = SwedishCitationParser(LegalRef(*self.parse_types),
#                                        self.minter,
#                                        self.commondata)
#        body = parser.parse_recursive(body)
        return body

    def guess_type(self, p, current_type):
        if not p:  # empty string
            return None
        # complex heading detection heuristics: Starts with a capital
        # or a number, and doesn't end with a period (except in some
        # cases).
        elif ((re.match("^\d+", p)
               or p[0].lower() != p[0])
              and not (p.endswith(".") and
                       not (p.endswith("m.m.") or
                            p.endswith("m. m.") or
                            p.endswith("m.fl.") or
                            p.endswith("m. fl.")))):
            return Heading
        elif p.startswith("--"):
            return ListItem
        elif (p[0].upper() != p[0]):
            return Continuation  # magic value, used to glue together
            # paragraphs that have been
            # inadvertently divided.
        else:
            return Paragraph

    def process_body(self, element, prefix, baseuri):
        if isinstance(element, str):
            return
        fragment = prefix
        uri = baseuri
        for p in element:
            self.process_body(p, fragment, baseuri)

    def canonical_uri(self, basefile):
        return self.config.url + "res/dir/" + basefile


class DirAsp(FixedLayoutSource):

    """Downloads Direktiv in PDF format from http://rkrattsdb.gov.se/kompdf/"""
    alias = "dirasp"
    # FIXME: these url should start with http://rkrattsdb.gov.se/, but
    # on at least some systems we have some IPv4/IPv6 problems with
    # that URI similar to what required the config.ipbasedurls option
    # in trips.py -- maybe we need something similar here (or fix our
    # systems at a lower level...)
    start_url = "http://193.188.157.100/kompdf/search.asp"
    document_url = "http://193.188.157.100/KOMdoc/%(yy)02d/%(yy)02d%(num)04d.PDF"
    source_encoding = "iso-8859-1"
    rdf_type = RPUBL.Kommittedirektiv
    storage_policy = "dir"
    # these defs are to play nice with SwedishLegalSource.get_parser
    KOMMITTEDIREKTIV = "dir"
    PROPOSITION = SOU = DS = None
    document_type = KOMMITTEDIREKTIV
    urispace_segment = "dir"
    
    def download(self, basefile=None):
        resp = requests.get(self.start_url)
        soup = BeautifulSoup(resp.text, "lxml")
        depts = [opt['value'] for opt in soup.find_all("option", value=True)]
        for basefile, url in self.download_get_basefiles(depts):
            # since the server doesn't support conditional caching and
            # direktivs are basically never updated once published, we
            # avoid even calling download_single if we already have
            # the doc.
            if ((not self.config.refresh) and
                    (not os.path.exists(self.store.downloaded_path(basefile)))):
                self.download_single(basefile, url)
    @downloadmax
    def download_get_basefiles(self, depts):
        for dept in depts:
            resp = requests.post(urljoin(self.start_url, 'sql_search_rsp.asp'),
                                 {'departement': dept.encode('latin-1'),
                                  'kom_nr': '',
                                  'title': '',
                                  'ACTION': '  SÖK  '.encode('latin-1')})
            soup = BeautifulSoup(resp.text, "lxml")
            hits = list(soup.find_all(True, text=re.compile(r'(\d{4}:\d+)')))
            self.log.debug("Searching for dept %s, %d results" % (dept, len(hits)))
            for hit in hits:
                link = hit.find_parent("a")
                # convert 2006:02 to 2006:2 for consistency
                segments = re.search("(\d+):(\d+)", link.text).groups()
                basefile = ":".join([str(int(x)) for x in segments])
                # we use link.absolute_url rather than relying on our
                # own basefile -> url code in remote_url. It seems
                # that in least one case the URL formatting rule is
                # not followed by the system...
                yield basefile, urljoin(self.start_url, link['href'])

    def remote_url(self, basefile):
        yy = int(basefile[2:4])
        num = int(basefile[5:])
        return self.document_url % {'yy': yy, 'num': num}

    def metadata_from_basefile(self, basefile):
        a = super(DirAsp, self).metadata_from_basefile(basefile)
        a["rpubl:arsutgava"], a["rpubl:lopnummer"] = basefile.split(":", 1)
        return a

    def infer_identifier(self, basefile):
        return "Dir %s" % basefile

    def postprocess_doc(self, doc):
        next_is_title = False
        for para in doc.body:
            strpara = str(para).strip()
            if strpara == "Kommittédirektiv":
                next_is_title = True
            elif next_is_title:
                doc.meta.add((URIRef(doc.uri), DCTERMS.title, Literal(strpara)))
                next_is_title = False
            elif strpara.startswith("Beslut vid regeringssammanträde den "):
                datestr = strpara[36:]  # length of above prefix
                if datestr.endswith("."):
                    datestr = datestr[:-1]
                doc.meta.add((URIRef(doc.uri), DCTERMS.issued,
                              Literal(self.parse_swedish_date(datestr),
                                      datatype=XSD.date)))
                break

class DirRegeringen(Regeringen):

    """Downloads Direktiv in PDF format from http://www.regeringen.se/"""
    alias = "dirregeringen"
    cssfiles = ['pdfview.css']
    jsfiles = ['pdfviewer.js']
    re_basefile_strict = re.compile(r'Dir\. (\d{4}:\d+)')
    re_basefile_lax = re.compile(r'(?:[Dd]ir\.?|) ?(\d{4}:\d+)')
    re_urlbasefile_strict = re.compile("kommittedirektiv/\d+/\d+/[a-z]*\.?-?(\d{4})(\d+)-?/$")
    re_urlbasefile_lax = re.compile("kommittedirektiv/\d+/\d+/.*?(\d{4})_?(\d+)")
    rdf_type = RPUBL.Kommittedirektiv
    document_type = Regeringen.KOMMITTEDIREKTIV

    def sanitize_identifier(self, identifier):
        # "Dir.1994:111" -> "Dir. 1994:111"
        if re.match("Dir.\d+", identifier):
            identifier = "Dir. " + identifier[4:]
        if not identifier.startswith("Dir. "):
            identifier = "Dir. " + identifier
        return Literal(identifier)

# inherit list_basefiles_for from CompositeStore, basefile_to_pathfrag
# from SwedishLegalStore)


class DirektivStore(CompositeStore, SwedishLegalStore):
    pass


# Does parsing, generating etc from base files:
class Direktiv(CompositeRepository, SwedishLegalSource):

    "A composite repository containing ``DirTrips``, ``DirAsp`` and ``DirRegeringen``."""
    subrepos = DirRegeringen, DirAsp, DirTrips
    alias = "dir"
    xslt_template = "xsl/forarbete.xsl"
    storage_policy = "dir"
    rdf_type = RPUBL.Kommittedirektiv
    documentstore_class = DirektivStore

    # NB: The same logic as in
    # ferenda.sources.legal.se.{Regeringen,Riksdagen}.metadata_from_basefile
    # news() needs to be able to compute URI from basefile, so we need
    # to reimplement this logic. Maybe that's stupid as there should
    # already be a distilled RDF file available in
    # distilled/[BASEFILE].rdf...
    def metadata_from_basefile(self, basefile):
        a = super(Direktiv, self).metadata_from_basefile(basefile)
        a["rpubl:arsutgava"], a["rpubl:lopnummer"] = basefile.split(":", 1)
        return a

