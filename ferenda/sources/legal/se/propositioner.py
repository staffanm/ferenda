# -*- coding: utf-8 -*-
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
from builtins import *
from future import standard_library
standard_library.install_aliases()

import re
import os
from datetime import datetime
from collections import OrderedDict
import codecs

from bs4 import BeautifulSoup
from lxml import etree
import requests
from layeredconfig import LayeredConfig

from ferenda import util
from ferenda.elements import Preformatted, Body
from ferenda import CompositeRepository, CompositeStore
from ferenda import TextReader
from ferenda import DocumentEntry
from . import (Trips, NoMoreLinks, Regeringen, Riksdagen,
               SwedishLegalSource, SwedishLegalStore, RPUBL)
from .fixedlayoutsource import FixedLayoutStore, FixedLayoutSource


class PropRegeringen(Regeringen):
    alias = "propregeringen"
    re_basefile_strict = re.compile(r'Prop. (\d{4}/\d{2,4}:\d+)')
    re_basefile_lax = re.compile(
        r'(?:Prop\.?|) ?(\d{4}/\d{2,4}:\d+)', re.IGNORECASE)
    re_urlbasefile_strict = re.compile("proposition/\d+/\d+/[a-z]*\.?-?(\d{6})(\d+)-?/$")
    re_urlbasefile_lax = re.compile("proposition/\d+/\d+/.*?(\d{4}_?\d{2})[_-]?(\d+)")
    rdf_type = RPUBL.Proposition
    document_type = Regeringen.PROPOSITION
    # sparql_annotations = "sparql/prop-annotations.rq"

    def attribs_from_url(self, url):
        attribs = super(PropRegeringen, self).attribs_from_url(url)
        # correct the not uncommon "2007/20:08123" -> "2007/2008:123" issue
        total = attribs["rpubl:arsutgava"] + attribs["rpubl:lopnummer"]
        if total.isdigit() and int(total[:4]) - int(total[4:8]) == - 1:
            # convert to "2007/2008:123" and let santize_basefile make
            # canonical (and warn). This way we don't need to
            # specialcase "1999/2000:123"
            attribs["rpubl:arsutgava"] = total[:8]
            attribs["rpubl:lopnummer"] = total[8:]
        y = attribs["rpubl:arsutgava"]
        if "/" not in y:
            attribs['rpubl:arsutgava'] = "%s/%s" % (y[:4], y[4:])
        return attribs

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
    ar = ""
    start_url = "http://rkrattsbaser.gov.se/prop/adv?dok=P&sort=asc&ar={c.lastyear}"
    document_url_template = "http://rkrattsbaser.gov.se/prop?ar=%(year)s&dok=P&dokid=%(ordinal)s" 

    basefile_regex = "(?P<basefile>\d+/\d+:\d+)$"

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
        opts['lastyear'] = ""
        return opts

    # don't use @recordlastdownload -- download_get_basefiles_page
    # should set self.config.lastbase instead
    def download(self, basefile=None):
        self.config.lastyear = "1997/98"
        if basefile:
            return super(PropTrips, self).download(basefile)
        else:
            if ('lastyear' in self.config and
                    self.config.lastyear and
                    not self.config.refresh):
                now = datetime.now()
                maxyear = "%s/%s" % (now.year, (now.year + 1) % 100)
                while self.config.lastyear != maxyear:
                    r = super(
                        PropTrips,
                        self).download()  # download_get_basefiles_page sets lastbase as it goes along
            else:
                r = super(PropTrips, self).download()
            self.config.lastyear = self._prev_year(self.config.lastyear)
            LayeredConfig.write(self.config)     # assume we have data to write
            return r

    def _next_year(self, year):
        # "1992/93" -> "1993/94"
        # "1998/99" -> "1999/00"
        y1, y2 = int(base[-4:-2]) + 1, int(base[-2:]) + 1
        return "%04d/%02d" % (int(y1), int(y2) % 100)

    def _prev_year(self, year):
        # "1993/94" -> "1992/93"
        # "1999/00" -> "1998/99"
        y1, y2 = int(year[-4:-2]) - 1, int(year[-2:]) - 1
        return "%04d/%02d" % (int(y1), int(y2) % 100)

    def remote_url(self, basefile):
        year, ordinal = basefile.split(":")
        return self.document_url_template % locals()
    
    def download_single(self, basefile, url=None):
        if url is None:
            url = self.remote_url(basefile)
            if not url:  # remote_url failed
                return

        updated = created = False
        checked = True
        mainattachment = None
        filename = self.store.downloaded_path(basefile)
        created = not os.path.exists(filename)

        # since the server doesn't support conditional caching and
        # propositioner are basically never updated once published, we
        # avoid even calling download_if_needed if we already have
        # the doc.
        if (os.path.exists(self.store.downloaded_path(basefile))
                and not self.config.refresh):
            self.log.debug("%s: already exists" % basefile)
            return

        downloaded_path = self.store.downloaded_path(basefile,
                                                     attachment="index.html")
        if self.download_if_needed(url, basefile, filename=filename):
            text = util.readfile(downloaded_path)
            if "<div>Inga tr\xe4ffar</div>" in text:
                self.log.warning("%s: Could not find this prop at %s, might be a bug" % (basefile, url))
                util.robust_remove(downloaded_path)
                return False
            if created:
                self.log.info("%s: downloaded from %s" % (basefile, url))
            else:
                self.log.info(
                    "%s: downloaded new version from %s" % (basefile, url))
            updated = True
        else:
            self.log.debug("%s: exists and is unchanged" % basefile)
            text = util.readfile(downloaded_path)
            
        soup = BeautifulSoup(text, "lxml")
        # here we can look for eg "Dokument: Prop. 1/19" in
        # div.result-inner-box, which signals that the document really
        # is attachment 19 to "Prop. 1997/98:1". FIXME: this means that we really need to keep track of post_id
        extraurls = []
        a = soup.find("a", string="Hämta Pdf")
        if a:
            extraurls.append(a.get("href"))
        a = soup.find("a", string="Hämta Doc") 
        if a:
            extraurls.append(a.get("href"))
        

        # parse downloaded html/text page and find out extraurls
        for url in extraurls:
            if url.endswith('get=doc'):
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
            elif url.endswith('get=pdf'):
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
    xslt_template = "xsl/forarbete.xsl"
    storage_policy = "dir"
    rdf_type = RPUBL.Proposition
    documentstore_class = PropositionerStore

    # NB: The same logic as in
    # ferenda.sources.legal.se.{Regeringen,Riksdagen}.metadata_from_basefile
    def metadata_from_basefile(self, basefile):
        a = super(Propositioner, self).metadata_from_basefile(basefile)
        a["rpubl:arsutgava"], a["rpubl:lopnummer"] = basefile.split(":", 1)
        return a

    def tabs(self):
        if self.config.tabs:
            return [('Propositioner', self.dataset_uri())]
        else:
            return []
