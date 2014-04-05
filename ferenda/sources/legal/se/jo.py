# -*- coding: utf-8 -*-
from __future__ import unicode_literals

# From python stdlib
import re, os

# 3rd party modules
import lxml.html
import requests
from six import text_type as str
from rdflib import Literal
from datetime import datetime, timedelta

# My own stuff
from ferenda import decorators
from ferenda import PDFDocumentRepository, FSMParser, Describer
from . import SwedishLegalSource, RPUBL
from .swedishlegalsource import UnorderedSection
from ferenda.elements import CompoundElement, Body, Paragraph, Heading
from ferenda.elements.html import Span

class Abstract(CompoundElement):
    tagname = "div"
    classname = "beslutikorthet"

class Blockquote(CompoundElement):
    tagname = "blockquote"

class Meta(CompoundElement):
    pass
    
class JO(SwedishLegalSource, PDFDocumentRepository):
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
    downloaded_suffix = ".pdf" # might need to change

    @decorators.action
    @decorators.recordlastdownload
    def download(self, basefile=None):
        if self.config.lastdownload and not self.config.refresh:
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
            headnote_url = self.headnote_url_template % {'basefile':basefile}
            resp = requests.get(headnote_url)
            if "1 totalt antal träffar" in resp.text:
                with self.store.open_downloaded(basefile, mode="wb", attachment="headnote.html") as fp:
                    fp.write(resp.content)
                self.log.debug("%s: downloaded headnote from %s" %
                               (basefile, headnote_url))
            else:
                self.log.warn("Could not find unique headnote for %s at %s" %
                              (basefile, headnote_url))
        return ret

        
    @decorators.managedparsing
    def parse(self, doc):
        # reset global state
        UnorderedSection.counter = 0
        def gluecondition(textbox, nextbox, prevbox):
            linespacing = nextbox.height / 1.5 # allow for large linespacing
            return (textbox.getfont()['size'] == nextbox.getfont()['size'] and 
                    textbox.top + textbox.height + linespacing >= nextbox.top)

        reader = self.pdfreader_from_basefile(doc.basefile)
        # bloxed = reader.drawboxes("glued.pdf", gluecondition)
        iterator = reader.textboxes(gluecondition)
        desc = Describer(doc.meta, doc.uri)
        doc.body = self.removemeta(self.structure(doc, iterator),
                                   Describer(doc.meta, doc.uri))

        # add basic metadata
        desc.rdftype(self.rdf_type)
        desc.value(self.ns['prov'].wasGeneratedBy, self.qualified_class_name())
        desc.value(self.ns['dct'].identifier, "JO dnr %s" % doc.basefile)
        # dct:issued is required, but we only have rpubl.avgorandedatum
        desc.value(self.ns['dct'].issued,
                desc.getvalue(self.ns['rpubl'].avgorandedatum))

        
        # if the headnote is present, do more (incl replacing title?)
        if "headnote.html" in list(self.store.list_attachments(doc.basefile, "downloaded")):
            self.parse_headnote(desc)
        return True

    def parse_headnote(self, desc):
        pass
        
    def removemeta(self, tree, desc):
        tmp = []
        for node in tree:
            if isinstance(node, Meta):
                for el in node: # should contain a list of RDF Literal objects
                    desc.value(node.predicate, el)
            elif isinstance(node, list):
                tmp.append(self.removemeta(node, desc))
            else:
                tmp.append(node)
        tree[:] = tmp[:]
        return tree
            
                
    def structure(self, doc, chunks):
        def is_heading(parser):
            return parser.reader.peek().getfont()['size'] == '17'

        def is_dnr(parser):
            chunk = parser.reader.peek()
            if (chunk.getfont()['size'] == '12' and
                re.match('\d+-\d{2,4}', str(chunk))):
                return True

        def is_datum(parser):
            chunk = parser.reader.peek()
            if (chunk.getfont()['size'] == '12' and
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
            if chunk.getfont()['size'] == '14' and chunk[0].tag == "b" and not strchunk.endswith("."):
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
            h = Meta([Literal(str(parser.reader.next()).strip(), lang="sv")],
                     predicate=self.ns['dct'].title)
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
            d = [Literal(str(parser.reader.next()).strip(),
                         datatype=self.ns['xsd'].date)]
            return Meta(d, predicate=self.ns['rpubl'].avgorandedatum)

        def make_dnr(parser):
            ds = [Literal(x) for x in str(parser.reader.next()).strip().split(" ")]
            return Meta(ds, predicate=self.ns['rpubl'].diarienummer)

        def skip_nonessential(parser):
            parser.reader.next() # return nothing

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
        p.debug = os.environ.get('FERENDA_FSMDEBUG', False)
        return p.parse(chunks)

    def create_external_resources(self, doc):
        pass
