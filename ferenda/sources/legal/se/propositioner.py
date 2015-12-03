# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import re
import sys
import os
from datetime import datetime
import codecs

from bs4 import BeautifulSoup
from lxml import etree
import requests
from layeredconfig import LayeredConfig
from six import text_type as str

from ferenda import util, errors
from ferenda.elements import UnicodeElement, CompoundElement, \
    Heading, Preformatted, Paragraph, Section, Link, ListItem, \
    Body, serialize
from ferenda import CompositeRepository, CompositeStore
from ferenda import PDFDocumentRepository
from ferenda import Describer
from ferenda import TextReader
from ferenda import PDFReader
from ferenda import DocumentEntry
from ferenda.compat import OrderedDict
from ferenda.decorators import managedparsing
from . import Trips, NoMoreLinks
from . import Regeringen
from . import Riksdagen
from . import RPUBL
from . import SwedishLegalSource, SwedishLegalStore
from .fixedlayoutsource import FixedLayoutStore, FixedLayoutSource
from .swedishlegalsource import offtryck_parser, offtryck_gluefunc


class PropRegeringen(Regeringen):
    alias = "propregeringen"
    re_basefile_strict = re.compile(r'Prop. (\d{4}/\d{2,4}:\d+)')
    re_basefile_lax = re.compile(
        r'(?:Prop\.?|) ?(\d{4}/\d{2,4}:\d+)', re.IGNORECASE)
    rdf_type = RPUBL.Proposition
    document_type = Regeringen.PROPOSITION
    # sparql_annotations = "res/sparql/prop-annotations.rq"
    sparql_annotations = None  # don't even bother creating an annotation file

class PropTripsStore(FixedLayoutStore):
    # 1999/94 and 1994/95 has only plaintext (wrapped in .html)
    # 1995/96 to 2006/07 has plaintext + doc
    # 2007/08 onwards has plaintext, doc and pdf
    downloaded_suffix = ".html"
    doctypes = OrderedDict([(".pdf", b'%PDF'),
                            (".doc", b'\xd0\xcf\x11\xe0'),
                            (".docx", b'PK\x03\x04'),
                            (".html", b'<!DO')])

    def intermediate_path(self, basefile):
        if self.downloaded_path(basefile).endswith(".html"):
            return self.path(basefile, "intermediate", ".txt")
        else:
            return super(PropTripsStore, self).intermediate_path(basefile)

class PropTrips(Trips, FixedLayoutSource):
    alias = "proptrips"
    base = "THWALLAPROP"
    app = "prop"

    basefile_regex = "(?P<basefile>\d+/\d+:\d+)$"
    download_params = [{'maxpage': 101, 'app': app, 'base': base}]

    downloaded_suffix = ".html"
    rdf_type = RPUBL.Proposition
    KOMMITTEDIREKTIV = SOU = DS = None
    PROPOSITION = "prop"
    document_type = PROPOSITION

    storage_policy = "dir"
    documentstore_class = PropTripsStore
    urispace_segment = "prop"

    
    @classmethod
    def get_default_options(cls):
        opts = super(PropTrips, cls).get_default_options()
        opts['lastbase'] = "THWALLAPROP"
        return opts

    # don't use @recordlastdownload -- download_get_basefiles_page
    # should set self.config.lastbase instead
    def download(self, basefile=None):
        if basefile:
            return super(PropTrips, self).download(basefile)
        else:
            if ('lastbase' in self.config and
                    self.config.lastbase and
                    not self.config.refresh):
                now = datetime.now()
                maxbase = "PROPARKIV%s%s" % (now.year % 100, (now.year + 1) % 100)
                while self.config.lastbase != maxbase:
                    # override "THWALLAPROP" with eg "PROPARKIV0809"
                    self.download_params[0]['base'] = self.config.lastbase
                    r = super(
                        PropTrips,
                        self).download()  # download_get_basefiles_page sets lastbase as it goes along
            else:
                r = super(PropTrips, self).download()
            self.config.lastbase = self._prev_base(self.config.lastbase)
            LayeredConfig.write(self.config)      # assume we have data to write
            return r

    def _basefile_to_base(self, basefile):
        # 1992/93:23 -> "PROPARKIV9293"
        # 1999/2000:23 -> "PROPARKIV9900"
        (y1, y2, idx) = re.split("[:/]", basefile)
        return "PROPARKIV%02d%02d" % (int(y1) % 100, int(y2) % 100)

    def _next_base(self, base):
        # "PROPARKIV9293" -> "PROPARKIV9394"
        # "PROPARKIV9899" -> "PROPARKIV9900"
        y1, y2 = int(base[-4:-2]) + 1, int(base[-2:]) + 1
        return "PROPARKIV%02d%02d" % (int(y1) % 100, int(y2) % 100)

    def _prev_base(self, base):
        # "PROPARKIV9394" -> "PROPARKIV9293"
        # "PROPARKIV9900" -> "PROPARKIV9899"
        y1, y2 = int(base[-4:-2]) - 1, int(base[-2:]) - 1
        return "PROPARKIV%02d%02d" % (int(y1) % 100, int(y2) % 100)

    def download_get_basefiles_page(self, pagetree):

        # feed the lxml tree into beautifulsoup by serializing it to a
        # string -- is there a better way?
        soup = BeautifulSoup(etree.tostring(pagetree))
        for tr in soup.findAll("tr"):
            if ((not tr.find("a")) or
                    not re.match(self.basefile_regex, tr.find("a").text)):
                # FIXME: Maybe re.search instead of .match to find
                # "Prop. 2012/13:152"
                continue
            # First, look at desc (third td):
            descnodes = [util.normalize_space(x) for x
                         in tr.find_all("td")[2]
                         if isinstance(x, str)]
            bilaga = None
            if len(descnodes) > 1:
                if descnodes[1].startswith("Bilaga:"):
                    bilaga = util.normalize_space(descnodes[0].split(",")[-1])
            desc = "\n".join(descnodes)

            # then, find basefile (second td)
            tds = tr.find_all("td")
            td = tds[1]
            basefile = td.a.text
            assert re.match(self.basefile_regex, basefile)

            basefile = self.sanitize_basefile(basefile)

            # assume entries are strictly sorted from ancient to
            # recent. Therefore, as soon as we encounter a new time
            # period (eg 1998/99) we can update self.config.lastbase
            self.config.lastbase = self._basefile_to_base(basefile)

            url = td.a['href']

            # self.download_single(basefile, refresh=refresh, url=url)

            # and, if present, extra files (in td 4+5)
            extraurls = []
            for td in tr.findAll("td")[3:]:
                extraurls.append(td.a['href'])

            # we slightly abuse the protocol between
            # download_get_basefiles and this generator -- instead of
            # yielding just two strings, we yield two tuples with some
            # extra information that download_single will need.

            yield (basefile, bilaga), (url, extraurls)

        nextpage = None
        for element, attribute, link, pos in pagetree.iterlinks():
            if element.text and element.text.strip() == "Fler poster":
                nextpage = link

        if nextpage is None:
            b = self._next_base(self.config.lastbase)
            self.log.info("Advancing lastbase from %s to %s" % (self.config.lastbase, b))
            self.config.lastbase = b
        raise NoMoreLinks(nextpage)

    document_url_template = ("http://rkrattsbaser.gov.se/cgi-bin/thw?"
                             "${HTML}=prop_lst&${OOHTML}=prop_dok"
                             "&${SNHTML}=prop_err&${HILITE}=1&${MAXPAGE}=26"
                             "&${TRIPSHOW}=format=THW"
                             "&${SAVEHTML}=/prop/prop_form2.html"
                             "&${BASE}=%(base)s&${FREETEXT}=&${FREETEXT}="
                             "&PRUB=&DOK=p&PNR=%(pnr)s&ORG=")

    def remote_url(self, basefile):
        base = self._basefile_to_base(basefile)
        pnr = basefile.split(":")[1]
        return self.document_url_template % {'base': base,
                                             'pnr': pnr}

    def download_single(self, basefile, url=None):
        if url is None:
            url = self.remote_url(basefile)
            if not url:  # remote_url failed
                return

        # unpack the tuples we may recieve instead of plain strings
        mainattachment = None
        if isinstance(basefile, tuple):
            basefile, attachment = basefile
            if attachment:
                mainattachment = attachment + ".html"
        if isinstance(url, tuple):
            url, extraurls = url
        updated = created = False
        checked = True

        filename = self.store.downloaded_path(basefile, attachment=mainattachment)
        created = not os.path.exists(filename)

        # since the server doesn't support conditional caching and
        # propositioner are basically never updated once published, we
        # avoid even calling download_if_needed if we already have
        # the doc.
        if (os.path.exists(self.store.downloaded_path(basefile))
                and not self.config.refresh):
            self.log.debug("%s: already exists" % basefile)
            return

        if self.download_if_needed(url, basefile, filename=filename):
            if created:
                self.log.info("%s: downloaded from %s" % (basefile, url))
            else:
                self.log.info(
                    "%s: downloaded new version from %s" % (basefile, url))
            updated = True
        else:
            self.log.debug("%s: exists and is unchanged" % basefile)

        for url in extraurls:
            if url.endswith('msword.application'):
                # NOTE: We cannot be sure that this is
                # actually a Word (CDF) file. For older files
                # it might be a WordPerfect file (.wpd) or a
                # RDF file, for newer it might be a .docx. We
                # cannot be sure until we've downloaded it.
                # So we quickly read the first 4 bytes
                r = requests.get(url, stream=True)
                sig = r.raw.read(4)
                # r.raw.close()
                #bodyidx = head.index("\n\n")
                #sig = head[bodyidx:bodyidx+4]
                if sig == b'\xffWPC':
                    doctype = ".wpd"
                elif sig == b'\xd0\xcf\x11\xe0':
                    doctype = ".doc"
                elif sig == b'PK\x03\x04':
                    doctype = ".docx"
                elif sig == b'{\\rt':
                    doctype = ".rtf"
                else:
                    self.log.error(
                        "%s: Attached file has signature %r -- don't know what type this is" % (basefile, sig))
                    continue
            elif url.endswith('pdf.application'):
                doctype = ".pdf"
            else:
                self.log.warning("Unknown doc type %s" %
                                 td.a['href'].split("=")[-1])
                doctype = None
            if doctype:
                if attachment:
                    filename = self.store.downloaded_path(
                        basefile, attachment=attachment + doctype)
                else:
                    filename = self.store.downloaded_path(
                        basefile,
                        attachment="index" +
                        doctype)
                self.log.debug("%s: downloading attachment %s" % (basefile, filename))
                self.download_if_needed(url, basefile, filename=filename)

        if mainattachment is None:
            entry = DocumentEntry(self.store.documententry_path(basefile))
            now = datetime.now()
            entry.orig_url = url
            if created:
                entry.orig_created = now
            if updated:
                entry.orig_updated = now
            if checked:
                entry.orig_checked = now
            entry.save()

        return updated

    # Correct some invalid identifiers spotted in the wild:
    # 1999/20 -> 1999/2000
    # 2000/2001 -> 2000/01
    # 1999/98 -> 1999/2000
    def sanitize_basefile(self, basefile):
        (y1, y2, idx) = re.split("[:/]", basefile)
        assert len(
            y1) == 4, "Basefile %s is invalid beyond sanitization" % basefile
        if y1 == "1999" and y2 != "2000":
            sanitized = "1999/2000:" + idx
            self.log.warning("Basefile given as %s, correcting to %s" %
                             (basefile, sanitized))
        elif (y1 != "1999" and
              (len(y2) != 2 or  # eg "2000/001"
               int(y1[2:]) + 1 != int(y2))):  # eg "1999/98

            sanitized = "%s/%02d:%s" % (y1, int(y1[2:]) + 1, idx)
            self.log.warning("Basefile given as %s, correcting to %s" %
                             (basefile, sanitized))
        else:
            sanitized = basefile
        return sanitized

    def downloaded_to_intermediate(self, basefile):
        intermediate_path = self.store.intermediate_path(basefile)
        if intermediate_path.endswith(".txt"):
            # extract txt from file, return fp to it
            downloaded_path = self.store.downloaded_path(basefile)
            html = codecs.open(
                downloaded_path, encoding="iso-8859-1").read()
            util.writefile(intermediate_path, util.extract_text(
                html, '<pre>', '</pre>'), encoding="utf-8")
            return open(intermediate_path, "rb")
        else:
             return super(PropTrips, self).downloaded_to_intermediate(basefile)

    def extract_head(self, fp, basefile):
        # regardless of whether fp points to a pdf->xml file, a
        # word->docbook file, or a plaintext-wrapped-in-html file,
        # we'll use the latter to extract identifier and title since
        # it's quick and easy.
        downloaded_path = self.store.downloaded_path(
            basefile, attachment="index.html")
        html = codecs.open(downloaded_path, encoding="iso-8859-1").read()
        return util.extract_text(html, '<pre>', '</pre>')[:400]

    def extract_metadata(self, chunk, basefile):
        attribs = self.metadata_from_basefile(basefile)
        for p in re.split("\n\n+", chunk):
            if p.startswith("Titel: "):
                attribs["dcterms:title"] = p.split(": ", 1)[1]
            elif p.startswith("Dokument: "):
                attribs["dcterms:identifier"] = p.split(": ", 1)[1]
        return attribs

    def sanitize_metadata(self, attribs, basefile):
        attribs = super(PropTrips, self).sanitize_metadata(attribs, basefile)
        if ('dcterms:title' in attribs and
            'dcterms:identifier' in attribs and
            attribs['dcterms:title'].endswith(attribs['dcterms:title'])):
            x = attribs['dcterms:title'][:-len(attribs['dcterms:identifier'])]
            attribs['dcterms:title'] = util.normalize_space(x)
        return attribs
    
    def extract_body(self, fp, basefile):
        if util.name_from_fp(fp).endswith(".txt"):
            # fp is opened in bytestream mode
            return TextReader(string=fp.read().decode("utf-8"))
        else:
            return super(PropTrips, self).extract_body(fp, basefile)

    @staticmethod
    def textparser(chunks):
        b = Body()
        for p in chunks:
            if not p.strip():
                continue
            elif not b and 'Obs! Dokumenten i denna databas kan vara ofullständiga.' in p:
                continue
            elif not b and p.strip().startswith("Dokument:"):
                # We already know this
                continue
            elif not b and p.strip().startswith("Titel:"):
                continue
            else:
                b.append(Preformatted([p]))
        return b

    def get_parser(self, basefile, sanitized):
        if self.store.intermediate_path(basefile).endswith(".txt"):
            return self.textparser
        else:
            return super(PropTrips, self).get_parser(basefile, sanitized)

    def tokenize(self, reader):
        if isinstance(reader, TextReader):
            return reader.getiterator(reader.readparagraph)
        else:
            return super(PropTrips, self).tokenize(reader)


class PropRiksdagen(Riksdagen):
    alias = "propriksdagen"
    rdf_type = RPUBL.Proposition
    document_type = Riksdagen.PROPOSITION

# inherit list_basefiles_for from CompositeStore, basefile_to_pathfrag
# from SwedishLegalStore)


class PropositionerStore(CompositeStore, SwedishLegalStore):
    pass


class Propositioner(CompositeRepository, SwedishLegalSource):
    subrepos = PropRegeringen, PropTrips, PropRiksdagen
    alias = "prop"
    xslt_template = "res/xsl/forarbete.xsl"
    storage_policy = "dir"
    rdf_type = RPUBL.Proposition
    documentstore_class = PropositionerStore
    sparql_annotations = None  # don't even bother creating an annotation file

    def tabs(self):
        if self.config.tabs:
            return [('Propositioner', self.dataset_uri())]
        else:
            return []
