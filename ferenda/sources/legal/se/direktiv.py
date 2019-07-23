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
from .elements import *
from ferenda import CompositeRepository, CompositeStore
from ferenda import TextReader
from ferenda import util
from ferenda import PDFAnalyzer, Facet, FSMParser
from ferenda.decorators import downloadmax, recordlastdownload, newstate
from ferenda.elements import Body, Heading, ListItem, Paragraph
from ferenda.errors import DocumentRemovedError
from ferenda.compat import urljoin
from ferenda.pdfreader import Page

def dir_sanitize_identifier(identifier):
    # common sanitizer for all purposes
    if not identifier:
        return identifier # allow infer_identifier to do it's magic later
    if identifier.startswith("Direktiv "):
        identifier = identifier.replace("Direktiv ", "Dir. ")
    if identifier.startswith("Dir. dir. "):
        identifier = identifier.replace("dir. ", "")
    if identifier.startswith("Dir "):
        identifier = identifier.replace("Dir ", "Dir. ")
    if identifier.startswith("dir."):
        identifier = identifier.replace("dir.", "Dir.")
    if identifier.startswith("Dir:"):
        identifier = identifier.replace("Dir:", "Dir.")
    # "Dir.1994:111" -> "Dir. 1994:111"
    if re.match("Dir\.\d+", identifier):
        identifier = "Dir. " + identifier[4:]
    # Dir. 2006.44 -> Dir. 2006:44
    if re.match("Dir\. \d+\.\d+", identifier):
        # replace the rightmost . with a :
        identifier = identifier[::-1].replace(".", ":", 1)[::-1]
    if not identifier.startswith("Dir. "):
        identifier = "Dir. " + identifier
    if not re.match("Dir\. (19|20)\d{2}:[1-9]\d{0,2}$", identifier):
        raise ValueError("Irregular identifier %s (after mangling)" %  identifier)
    return Literal(identifier)

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
        styles = sorted(styles.keys(), key=self.fontsize_key,
                        reverse=True)[1:5]
        if len(styles) < 3: # only happens for dir 1991:49, which do
                            # not use any header styles except ts
            (ts, dummy) = styles
            h1 = None
            h2 = None
        elif len(styles) < 4: # might be the case if no h2:s are ever used
            (ts, dummy, h1) = styles
            h2 = None
        else:
            (ts, dummy, h1, h2) = styles
            if h2 == ds:  # what we thought was h2 was really the
                          # default style, meaning no h2:s are used in
                          # the doc
                h2 = None

        styledefs['title'] = self.fontdict(ts)
        if h1:
            styledefs['h1'] = self.fontdict(h1)
        if h2:
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
    urispace_segment = "dir"
    storage_policy = "file"
    
    @recordlastdownload
    def download(self, basefile=None):
        if basefile:
            return super(DirTrips, self).download(basefile)
        else:
            if 'lastdownload' in self.config and self.config.lastdownload and not self.config.refresh:
                startdate = self.config.lastdownload - timedelta(days=30)
                # The search form now only supports whole year
                # interval on the form "YYYY to YYYY"
                self.start_url += "&udat=%s+to+%s" % (startdate.year, datetime.now().year)
            super(DirTrips, self).download()


    def downloaded_to_intermediate(self, basefile, attachment=None):
        return self._extract_text(basefile)

    def extract_head(self, fp, basefile):
        textheader = fp.read(2048)
        if not isinstance(textheader, str):
            # Depending on whether the fp is opened through standard
            # open() or bz2.BZ2File() in self.parse_open(), it might
            # return bytes or unicode strings. This seem to be a
            # problem in BZ2File (or how we use it). Just roll with it.

            textheader = bytes(textheader)
            textheader = textheader.decode(self.source_encoding, errors="ignore")
        idx = textheader.index("-"*64)
        header = textheader[:idx]
        fp.seek(len(header) + 66)
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
    
    def sanitize_identifier(self, identifier):
        return dir_sanitize_identifier(identifier)
        
    def sanitize_rubrik(self, rubrik):
        if rubrik == "Utgår":
            raise DocumentRemovedError()
        rubrik = re.sub("^/r2/ ", "", rubrik)
        return Literal(rubrik, lang="sv")


    def extract_body(self, fp, basefile):
        rawtext = fp.read()
        if isinstance(rawtext, bytes): # happens when creating the intermediate file
            rawtext = rawtext.decode(self.source_encoding)
        # remove whitespace on otherwise empty lines
        rawtext = re.sub("\n\t\n", "\n\n", rawtext)
        reader = TextReader(string=rawtext,
                            linesep=TextReader.UNIX)
        return reader
    
    def parse_body_parseconfigs(self):
        return ("default", "simple")

    def get_parser(self, basefile, sanitized, parseconfig="default"):

        def is_header(parser):
            p = parser.reader.peek()
            # older direktiv sources start with dir number
            if re.match(r'Dir\.? \d{4}:\d+$', p):
                return False
            return (headerlike(p) and 
                    not is_strecksats(parser, parser.reader.peek(2)))

        def is_strecksats(parser, chunk=None):
            if chunk is None:
                chunk = parser.reader.peek()
            return chunk.startswith(("--", "- "))

        def is_section(parser):
            (ordinal, headingtype, title) = analyze_sectionstart(parser)
            if ordinal:
                return headingtype == "h1"

        def is_subsection(parser):
            (ordinal, headingtype, title) = analyze_sectionstart(parser)
            if ordinal:
                return headingtype == "h2"

        def is_paragraph(parser):
            return True

        @newstate('body')
        def make_body(parser):
            return parser.make_children(Body())

        @newstate('section')
        def make_section(parser):
            chunk = parser.reader.next()
            ordinal, headingtype, title = analyze_sectionstart(parser, chunk)
            s = Avsnitt(ordinal=ordinal, title=title)
            return parser.make_children(s)

        @newstate('strecksats')
        def make_strecksatslista(parser):
            ul = Strecksatslista()
            li = make_listitem(parser)
            ul.append(li)
            res = parser.make_children(ul)
            return res

        def make_listitem(parser):
            chunk = parser.reader.next()
            s = str(chunk)
            if " " in s:
                # assume text before first space is the bullet
                s = s.split(" ",1)[1]
            else:
                # assume the bullet is a single char
                s = s[1:]
            return Strecksatselement([s])

        def make_header(parser):
            return Heading([parser.reader.next()])
        
        def make_paragraph(parser):
            return Paragraph([parser.reader.next()])

        @newstate('unorderedsection')
        def make_unorderedsection(parser):
            s = UnorderedSection(title=parser.reader.next().strip())
            return parser.make_children(s)
            
        def headerlike(p):
            return (p[0].lower() != p[0]
                    and len(p) < 150
                    and not (p.endswith(".") and
                             not (p.endswith("m.m.") or
                                  p.endswith("m. m.") or
                                  p.endswith("m.fl.") or
                                  p.endswith("m. fl."))))

        re_sectionstart = re.compile("^(\d[\.\d]*) +([A-ZÅÄÖ].*)$").match
        def analyze_sectionstart(parser, chunk=None):
            """returns (ordinal, headingtype, text) if it looks like a section
            heading, (None, None, chunk) otherwise."""
            if chunk is None:
                chunk = parser.reader.peek()
            m = re_sectionstart(chunk)
            if m and headerlike(m.group(2)):
                return (m.group(1),
                        "h" + str(m.group(1).count(".") + 1),
                        m.group(2).strip())
            else:
                return None, None, chunk

        p = FSMParser()
        if parseconfig == "simple":
            recognizers = [is_header, is_strecksats, is_paragraph]
        else:
            recognizers = [is_section,
                           is_subsection,
                           is_header,
                           is_strecksats,
                           is_paragraph]
        p.set_recognizers(*recognizers)
        commonstates = ("body", "section", "subsection", "unorderedsection")
        p.set_transitions({(commonstates, is_paragraph): (make_paragraph, None),
                           (commonstates, is_strecksats): (make_strecksatslista, "strecksats"),
                           (commonstates, is_header): (make_unorderedsection, "unorderedsection"),
                           (commonstates, is_section): (make_section, "section"),
                           
                           ("unorderedsection", is_header): (False, None),
                           ("unorderedsection", is_section): (False, None),
                           ("strecksats", is_paragraph): (False, None),
                           ("strecksats", is_strecksats): (make_listitem, None),
                           ("section", is_header): (False, None),
                           ("section", is_section): (False, None),
                           ("section", is_subsection): (make_section, "subsection"),
                           ("subsection", is_subsection): (False, None),
                           ("subsection", is_section): (False, None)})
        p.initial_state = "body"
        p.initial_constructor = make_body
        p.debug = os.environ.get('FERENDA_FSMDEBUG', False)
        return p.parse

    def tokenize(self, reader):
        return reader.getiterator(reader.readparagraph)


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
        if basefile:
            return super(DirAsp, self).download(basefile)
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
        return "Dir. %s" % basefile

    def postprocess_doc(self, doc):
        next_is_title = False
        newbody = Body()
        glue = lambda x, y, z: False
        for para in doc.body.textboxes(gluefunc=glue, pageobjects=True):
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
            if isinstance(para, Page):
                newbody.append(Sidbrytning(ordinal=para.number,
                                           width=para.width,
                                           height=para.height,
                                           src=para.src))
            else:
                newbody.append(para)
            doc.body = newbody

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
        return dir_sanitize_identifier(identifier)

    def infer_identifier(self, basefile):
        return "Dir. %s" % basefile

# inherit list_basefiles_for from CompositeStore, basefile_to_pathfrag
# from SwedishLegalStore)
class DirektivStore(CompositeStore, SwedishLegalStore):
    pass


# Does parsing, generating etc from base files:
class Direktiv(CompositeRepository, FixedLayoutSource):

    "A composite repository containing ``DirTrips``, ``DirAsp`` and ``DirRegeringen``."""
    subrepos = DirRegeringen, DirAsp, DirTrips
    alias = "dir"
    xslt_template = "xsl/forarbete.xsl"
    storage_policy = "dir"
    rdf_type = RPUBL.Kommittedirektiv
    documentstore_class = DirektivStore
    sparql_annotations = "sparql/describe-with-subdocs.rq"
    sparql_expect_results = False

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

    def facets(self):
        return super(Direktiv, self).facets() + [Facet(DCTERMS.title,
                                                       toplevel_only=False)]
