# -*- coding: utf-8 -*-
from __future__ import unicode_literals

# From python stdlib
import re
import os
from datetime import datetime, timedelta

# 3rd party modules
import lxml.html
import requests
from six import text_type as str
from rdflib import Literal
from rdflib.namespace import SKOS
from bs4 import BeautifulSoup

# My own stuff
from ferenda import FSMParser
from ferenda import decorators
from ferenda.elements import CompoundElement, Body, Paragraph, Heading
from . import RPUBL
from .fixedlayoutsource import FixedLayoutSource
from .swedishlegalsource import UnorderedSection


class Abstract(CompoundElement):
    tagname = "div"
    classname = "beslutikorthet"


class Blockquote(CompoundElement):
    tagname = "blockquote"


class Meta(CompoundElement):
    pass
    
class JO(FixedLayoutSource):

    """Hanterar beslut från Riksdagens Ombudsmän, www.jo.se

    Modulen hanterar hämtande av beslut från JOs webbplats i PDF samt
    omvandlande av dessa till XHTML.

    """
    alias = "jo"
    start_url = "http://www.jo.se/sv/JO-beslut/Soka-JO-beslut/?query=*&pn=1"
    document_url_regex = "http://www.jo.se/PageFiles/(?P<dummy>\d+)/(?P<basefile>\d+\-\d+)(?P<junk>[,%\d\-]*).pdf"
    headnote_url_template = "http://www.jo.se/sv/JO-beslut/Soka-JO-beslut/?query=%(basefile)s&pn=1"

    rdf_type = RPUBL.VagledandeMyndighetsavgorande
    storage_policy = "dir"
    downloaded_suffix = ".pdf" 

    def metadata_from_basefile(self, basefile):
        return {'rpubl:diarienummer': basefile,
                'dcterms:publisher': self.lookup_resource("JO", SKOS.altLabel),
                'rdf:type': self.rdf_type}

    @decorators.action
    @decorators.recordlastdownload
    def download(self, basefile=None):
        self.session = requests.session()
        if ('lastdownload' in self.config and
                self.config.lastdownload and
                not self.config.refresh):
            startdate = self.config.lastdownload - timedelta(days=30)
            self.start_url += "&from=%s" % datetime.strftime(startdate, "%Y-%m-%d")
        for basefile, url in self.download_get_basefiles(self.start_url):
            self.download_single(basefile, url)

    @decorators.downloadmax
    def download_get_basefiles(self, start_url):
        # FIXME: try to download a single result HTML page, since
        # there are a few metadata props there.
        done = False
        url = start_url
        pagecount = 1
        self.log.debug("Starting at %s" % start_url)
        while not done:
            nextpage = None
            assert "pn=%s" % pagecount in url
            soughtnext = url.replace("pn=%s" % pagecount,
                                     "pn=%s" % (pagecount + 1))
            self.log.debug("Getting page #%s" % pagecount)
            resp = requests.get(url)
            tree = lxml.html.document_fromstring(resp.text)
            tree.make_links_absolute(url, resolve_base_href=True)
            for element, attribute, link, pos in tree.iterlinks():
                m = re.match(self.document_url_regex, link)
                if m:
                    yield m.group("basefile"), link
                elif link == soughtnext:
                    nextpage = link
                    pagecount += 1
            if nextpage:
                url = nextpage
            else:
                done = True

    def download_single(self, basefile, url):
        ret = super(JO, self).download_single(basefile, url)
        if ret or self.config.refresh:
            headnote_url = self.headnote_url_template % {'basefile': basefile}
            resp = requests.get(headnote_url)
            if "1 totalt antal träffar" in resp.text:
                # don't save the entire 100+ KB HTML mess when we only
                # want a litle 6 KB piece. Disk space is cheap but not
                # infinite
                soup = BeautifulSoup(resp.text).find("div", "MidContent")
                soup.find("ol", "breadcrumb").decompose()
                soup.find("div", id="SearchSettings").decompose()
                with self.store.open_downloaded(basefile, mode="wb", attachment="headnote.html") as fp:
                    fp.write(soup.prettify().encode("utf-8"))
                self.log.debug("%s: downloaded headnote from %s" %
                               (basefile, headnote_url))
            else:
                self.log.warn("Could not find unique headnote for %s at %s" %
                              (basefile, headnote_url))
        return ret

    def extract_head(self, fp, basefile):
        if "headnote.html" in list(self.store.list_attachments(basefile,
                                                               "downloaded")):
            with self.store.open_downloaded(basefile,
                                            attachment="headnote.html") as fp:
                return BeautifulSoup(fp, "lxml")
        # else: return None

    def infer_identifier(self, basefile):
        return "JO %s" % basefile.replace("/", "-")
        
    def extract_metadata(self, rawhead, basefile):
        if rawhead:
            print("FIXME: we should do something with this BeautifulSoup data")
        return self.metadata_from_basefile(basefile)

    def tokenize(self, reader):
        def gluecondition(textbox, nextbox, prevbox):
            linespacing = nextbox.height / 1.5  # allow for large linespacing
            return (textbox.font.size == nextbox.font.size and
                    textbox.top + textbox.height + linespacing >= nextbox.top)
        return reader.textboxes(gluecondition)

    def get_parser(self, basefile, sanitized):
        def is_heading(parser):
            return parser.reader.peek().font.size == 17

        def is_dnr(parser):
            chunk = parser.reader.peek()
            if (chunk.font.size == 12 and
                    re.match('\d+-\d{2,4}', str(chunk))):
                return True

        def is_datum(parser):
            chunk = parser.reader.peek()
            if (chunk.font.size == 12 and
                    re.match('\d{4}-\d{2}-\d{2}', str(chunk))):
                return True

        def is_nonessential(parser):
            chunk = parser.reader.peek()
            if chunk.top >= 1159 or chunk.top <= 146:
                return True

        def is_abstract(parser):
            if str(parser.reader.peek()).startswith("Beslutet i korthet:"):
                return True

        def is_section(parser):
            chunk = parser.reader.peek()
            strchunk = str(chunk)
            if chunk.font.size == 14 and chunk[0].tag == "b" and not strchunk.endswith("."):
                return True

        def is_blockquote(parser):
            chunk = parser.reader.peek()
            if chunk.left >= 255:
                return True

        def is_normal(parser):
            chunk = parser.reader.peek()
            if chunk.left < 255:
                return True

        def is_paragraph(parser):
            return True

        @decorators.newstate("body")
        def make_body(parser):
            return parser.make_children(Body())

        def make_heading(parser):
            # h = Heading(str(parser.reader.next()).strip())
            h = Meta([str(parser.reader.next()).strip()],
                     predicate=self.ns['dcterms'].title)
            return h

        @decorators.newstate("abstract")
        def make_abstract(parser):
            a = Abstract([Paragraph(parser.reader.next())])
            return parser.make_children(a)

        @decorators.newstate("section")
        def make_section(parser):
            s = UnorderedSection(title=str(parser.reader.next()).strip())
            return parser.make_children(s)

        @decorators.newstate("blockquote")
        def make_blockquote(parser):
            b = Blockquote()
            return parser.make_children(b)

        def make_paragraph(parser):
            p = Paragraph(parser.reader.next())
            return p

        def make_datum(parser):
            d = [str(parser.reader.next())]
            return Meta(d, predicate=self.ns['rpubl'].avgorandedatum)

        def make_dnr(parser):
            ds = [x for x in str(parser.reader.next()).strip().split(" ")]
            return Meta(ds, predicate=self.ns['rpubl'].diarienummer)

        def skip_nonessential(parser):
            parser.reader.next()  # return nothing

        p = FSMParser()
        p.initial_state = "body"
        p.initial_constructor = make_body
        p.set_recognizers(is_datum,
                          is_dnr,
                          is_nonessential,
                          is_heading,
                          is_abstract,
                          is_section,
                          is_normal,
                          is_blockquote,
                          is_paragraph)
        p.set_transitions({("body", is_heading): (make_heading, None),
                           ("body", is_nonessential): (skip_nonessential, None),
                           ("body", is_datum): (make_datum, None),
                           ("body", is_dnr): (make_dnr, None),
                           ("body", is_abstract): (make_abstract, "abstract"),
                           ("body", is_section): (make_section, "section"),
                           ("body", is_blockquote): (make_blockquote, "blockquote"),
                           ("body", is_paragraph): (make_paragraph, None),
                           ("abstract", is_paragraph): (make_paragraph, None),
                           ("abstract", is_section): (False, None),
                           ("section", is_paragraph): (make_paragraph, None),
                           ("section", is_nonessential): (skip_nonessential, None),
                           ("section", is_section): (False, None),
                           ("section", is_blockquote): (make_blockquote, "blockquote"),
                           ("section", is_datum): (make_datum, None),
                           ("section", is_dnr): (make_dnr, None),
                           ("blockquote", is_blockquote): (make_paragraph, None),
                           ("blockquote", is_nonessential): (skip_nonessential,  None),
                           ("blockquote", is_section): (False, None),
                           ("blockquote", is_normal): (False, None),
                           ("blockquote", is_datum): (make_datum, None),
                           ("blockquote", is_dnr): (make_dnr, None),
                           })
        return p.parse

    def tabs(self):
        if self.config.tabs:
            return [("JO"), self.dataset_uri()]
        else:
            return []
