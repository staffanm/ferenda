# -*- coding: utf-8 -*-
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
from builtins import *
from future import standard_library
standard_library.install_aliases()

import re
import os
from datetime import datetime
from collections import OrderedDict, Counter
import codecs
from urllib.parse import urljoin
import json
import tempfile

from bs4 import BeautifulSoup
from lxml import etree
import lxml.html
import requests
from layeredconfig import LayeredConfig
from cached_property import cached_property
from rdflib import Literal, URIRef
from rdflib.namespace import DCTERMS

from ferenda import util, decorators
from ferenda.elements import Preformatted, Body
from ferenda import CompositeRepository, CompositeStore
from ferenda import TextReader, PDFAnalyzer
from ferenda import DocumentEntry, Facet, PDFDocumentRepository
from ferenda.pdfreader import StreamingPDFReader, Textbox
from . import (Trips, NoMoreLinks, Regeringen, Riksdagen,
               SwedishLegalSource, SwedishLegalStore, RPUBL, Offtryck)
from .fixedlayoutsource import FixedLayoutStore, FixedLayoutSource
from .swedishlegalsource import lazyread
from .elements import Sidbrytning

def prop_sanitize_identifier(identifier):
    if not identifier:
        return identifier # allow infer_identifier to do it's magic later
    if identifier.startswith("prop"):
        identifier = util.ucfirst(identifier)
    if identifier.startswith("PROP"):
        identifier = identifier.replace("PROP", "Prop")
    if identifier.startswith("Prop "):
        identifier = identifier.replace("Prop ", "Prop. ")
    if re.match("Prop\.\d{4}", identifier): # missing space
        identifier = identifier.replace("Prop.", "Prop. ")
    if "\xa0" in identifier: # Non-breakable space
        identifier = identifier.replace("\xa0", " ")
    if not identifier.startswith("Prop. "):
        identifier = "Prop. " + identifier
    # identify and correct the not-uncommon "2009/2010:87" pattern (should be 2009/10:87)
    m = re.search(r"(\d{4})/(\d{4}):(\d+)$", identifier)
    if m and m.group(2) != "2000" and int(m.group(1)) == int(m.group(2)) - 1:
        identifier = identifier.replace(m.group(2), m.group(2)[-2:])
    if not re.match(r"^Prop\. (19|20)\d{2}(|/\d{2}|/2000):[1-9]\d*$", identifier):
        raise ValueError("Irregular identifier %s" % identifier)
    return Literal(identifier)

class PropAnalyzer(PDFAnalyzer):

    # NOTE: The cutoff used to be 0.5% but it turns out that in
    # particular h2's can be quite rare, occuring maybe two times
    # in an entire document.
    style_significance_threshold = 0.001

    @cached_property
    def documents(self):
        def boxmatch(page, textpattern, bb=None):
            if bb is None:
                bb = page.boundingbox(bottom=page.height / 5)
            for box in bb:
                m = re.match(textpattern, str(box))
                if m:
                    return m.group(1)
            return None
        documents = []
        mainstyles = Counter()
        pagedims = {'pagewidth': util.TopCounter(),
                    'pageheight': util.TopCounter()}
        currentappendix = None
        for pageidx, page in enumerate(self.pdf):
            styles = self.count_styles(pageidx, 1)
            # find the most dominant style on the page. If it uses the
            # EU font, it's a separate section.
            if styles and styles.most_common(1)[0][0][0].startswith("EUAlbertina"):
                currentdoc = 'eudok'
                currentappendix = boxmatch(page, "Bilaga (\d)\s*$")
            else:
                # if there is a text box matching "Bilaga \d" in top
                # margin and the bilagenummer is new and dominant
                # style (family) is different from any of the
                # top 3 currrent dominant styles:
                #
                # NOTE that normally we want to treat appendicies as
                # part of the regular text (so that
                # offtryck_parser.is_appendix et al can do their
                # thing. This heuristic should only catch appendicies
                # that are v. different.
                appendix = boxmatch(page, "Bilaga (\d)\s*$")
                if (appendix and
                    appendix != currentappendix and
                    styles.most_common(1) and 
                    styles.most_common(1)[0][0][0] not in [x[0][0] for x in mainstyles.most_common(3)]):
                    currentdoc = 'appendix'
                elif ".hocr." in self.pdf.filename:
                    # scanned sources have fluctuating page sizes,
                    # plus it's not possible to identify appendicies
                    # by differing page dimensions
                    currentdoc = "main"
                elif pageidx == 0 and boxmatch(page, "(REGERINGENS PROPOSITION)", page.boundingbox(top=page.height * 0.8)):
                    currentdoc = "frontmatter"
                else:
                    if (pagedims['pageheight'] and
                        (abs(pagedims['pageheight'].top() - page.height) > 1 or
                         abs(pagedims['pagewidth'].top() - page.width) > 1)):
                        # if the page dimensions suddenly change,
                        # that's a dead giveaway that some external
                        # appendix has been lifted right into the PDF
                        #
                        # But in some cases dimension change does NOT
                        # mean external appendix. In Prop 2015/16:195,
                        # which is split in 4 pdfs (2 logical volumes)
                        # it's just an artifact due to the 2nd pdf
                        # being properly cropped while the 1st
                        # isn't. In prop 2008/09:140, which
                        # uncharacteristically includes frontmatter, a
                        # dimension change signals the change from
                        # frontmatter to main
                        if currentdoc == "frontmatter":
                            currentdoc = "main"
                        else:
                            currentdoc = 'appendix'
                    else:
                        currentdoc = 'main'
                        currentappendix = appendix
            if currentdoc == "main":
                mainstyles += styles
                pagedims['pagewidth'][page.width] += 1
                pagedims['pageheight'][page.height] += 1
            # update the current document segment tuple or start a new one
            if documents and documents[-1][2] == currentdoc:
                documents[-1][1] += 1
            else:
                documents.append([pageidx, 1, currentdoc])
        return documents

    def guess_pagenumber_select(self, candidates, probable_pagenumber):
        if self.scanned_source:
            # try to avoid assuming that smudges and crap equals
            # lower-case L and other things that might be interpreted
            # as roman numeral
            if util.is_roman(candidates[0]) and str(probable_pagenumber) == "1":
                return 1  # Do not interpret a single 'l' as roman 50
                          # -- it's probably a badly OCR:ed '
            else:
                # be a little more conservative with what a good guess
                # is compared to PDFAnalyzer.guess_pagenumber_select:
                # only accept the smallest candidate larger-or-equal
                # to the probable_pagenumber -- but not if it's a
                # too-large gap. Also, assume no roman numerals
                try:
                    return next(c for c in sorted(candidates) if c >= probable_pagenumber and c <= probable_pagenumber * 2)
                except StopIteration: # no suitable candidate
                    return None
                
        # otherwise fall back to superclass implementation
        return super(PropAnalyzer, self).guess_pagenumber_select(candidates, probable_pagenumber)
                          
    def guess_pagenumber_boxes(self, page):
        """Return a suitable number of textboxes to scan for a possible page number. """
        if self.scanned_source:
            # For scanned source, the default strategy works so-so
            # (many OCR errors may result in misinterpreting things as
            # pagenumbers) so we also take into account the text box
            # property. Only select thin boxes (less than 1/50th of
            # the page width) -- page numbers should stand by
            # themselves and naturally be pretty thin
            return [b for b in list(reversed(page))[:5] + list(page)[:5] if b.width < page.width/50]
        else:
            return super(PropAnalyzer, self).guess_pagenumber_boxes(page)


    def metrics(self, metricspath=None, plotpath=None, startpage=0,
                pagecount=None, force=False):
        docsegments = self.documents
        if len(docsegments) == 1:
            return super(PropAnalyzer, self).metrics(metricspath,
                                                     plotpath,
                                                     startpage,
                                                     pagecount, force)
        else:
            r = []
            exclude = []
            mainidx = None
            for idx, (startpage, pagecount, tag) in enumerate(docsegments):
                r.append(super(PropAnalyzer,
                                 self).metrics(startpage=startpage,
                                               pagecount=pagecount))
                if tag != 'main':
                    exclude.extend(list(range(startpage, startpage+pagecount)))
                elif mainidx is None:
                    mainidx = idx
        r[mainidx]['excludedpages'] = exclude
        # since we don't pass metricspath to super().metrics, that
        # func does not create a metrics.json cache file. So we
        # generate that now (using the same data as we return)
        util.ensure_dir(metricspath)
        with open(metricspath, "w") as fp:
            s = json.dumps(r[mainidx], indent=4, separators=(', ', ': '), sort_keys=True)
            fp.write(s)
        return r[mainidx]

    def count_styles(self, startpage, pagecount):
        # we should avoid counting the styles on the front page, as
        # that page uses a title font, not used anywhere else in the
        # document, which is then mistaken for the h1 font.
        if not startpage:
            startpage = 1
        return super(PropAnalyzer, self).count_styles(startpage, pagecount)

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

    def sanitize_identifier(self, identifier):
        return prop_sanitize_identifier(identifier)


class PropTripsStore(FixedLayoutStore):
    # 1993/94 and 1994/95 has only plaintext (wrapped in .html)
    # 1995/96 to 2006/07 has plaintext + doc
    # 2007/08 onwards has plaintext, doc and pdf
    doctypes = OrderedDict([(".html", b'<!DO'),
                            (".wpd", b'\xffWPC'),
                            (".docx", b'PK\x03\x04'),
                            (".doc", b'\xd0\xcf\x11\xe0'),
                            (".pdf", b'%PDF')])

    def intermediate_path(self, basefile, version=None, attachment=None, suffix=None):
        # we need to select a suitable intermediate suffix based upon
        # the downloaded suffix (pdf->xml, html->txt)
        if self.downloaded_path(basefile).endswith(".html"):
            from ferenda.documentstore import _compressed_suffix
            return self.path(basefile, "intermediate", ".txt" + _compressed_suffix(self.compression))
        else:
            return super(PropTripsStore, self).intermediate_path(basefile, version, attachment, suffix)


# We derive from Trips for downloading, from FixedLayoutSource for
# downloaded_to_intermediate, extract_{head,metadata,body}, and from
# Offtryck for most everything else. FIXME: This is not manageble.
class PropTrips(Trips, Offtryck, FixedLayoutSource):
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
    # should set self.config.lastyear instead
    def download(self, basefile=None):
        if self.config.ipbasedurls:
            self._make_ipbasedurls()
        urlmap_path = self.store.path("urls", "downloaded", ".map",
                                      storage_policy="file")
        self.urlmap = {}
        if os.path.exists(urlmap_path):
            with codecs.open(urlmap_path, encoding="utf-8") as fp:
                for line in fp:
                    url, attachment = line.split("\t")
                    self.urlmap[url] = attachment.strip()
        if basefile:
            return super(PropTrips, self).download(basefile)
        try:
            now = datetime.now()
            if ('lastyear' in self.config and
                    self.config.lastyear and
                    not self.config.refresh):
                maxyear = "%s/%s" % (now.year, (now.year + 1) % 100)
                while self.config.lastyear != maxyear:
                    r = self.inner_download() 
            else:
                self.config.lastyear = ''
                r = self.inner_download()
            self.config.lastyear = "%s/%s" % (now.year - 1,
                                              (now.year % 100))
            LayeredConfig.write(self.config)     # assume we have data to write
            return r
        finally:
            with codecs.open(urlmap_path, "w", encoding="utf-8") as fp:
                for url, attachment in self.urlmap.items():
                    fp.write("%s\t%s\n" % (url, attachment))

    def inner_download(self):
        refresh = self.config.refresh
        updated = False
        for basefile, url in self.download_get_basefiles(None):
            if url in self.urlmap:
                attachment = self.urlmap[url]
            else:
                attachment = self.sniff_attachment(url)
            if attachment:
                self.urlmap[url] = attachment
                attachment += ".html"
            else:
                self.urlmap[url] = ''
                attachment = None  # instead of the empty string
            if (refresh or
                    (not os.path.exists(self.store.downloaded_path(basefile, attachment=attachment)))):
                ret = self.download_single(basefile, url)
                updated = updated or ret
        return updated

    def sniff_attachment(self, url):
        r = requests.get(url, stream=True)
        head = r.raw.read(8000)
        soup = BeautifulSoup(head, "lxml")
        return self.find_attachment(soup)

    def find_attachment(self, soup):
        results = soup.find("div", "search-results-content")
        dokid = results.find("span", string="Dokument:")
        if not dokid:
            return None
        dokid = dokid.next_sibling.strip().split(" ")[-1]
        if "/" in dokid:
            dokid, attachment = dokid.split("/")
        else:
            attachment = None
        return attachment
        
    def _next_year(self, year):
        # "1992/93" -> "1993/94"
        # "1998/99" -> "1999/00"
        assert len(year) == 7, "invalid year specifier %s" % year
        y1, y2 = int(year[:4]) + 1, int(year[-2:]) + 1
        return "%04d/%02d" % (int(y1), int(y2) % 100)

    def _prev_year(self, year):
        # "1993/94" -> "1992/93"
        # "1999/00" -> "1998/99"
        assert len(year) == 7, "invalid year specifier %s" % year
        y1, y2 = int(year[:4]) - 1, int(year[-2:]) - 1
        return "%04d/%02d" % (int(y1), int(y2) % 100)

    def remote_url(self, basefile):
        year, ordinal = basefile.split(":")
        return self.document_url_template % locals()

    def download_get_basefiles_page(self, soup):
        nextpage = None
        for hit in soup.findAll("div", "search-hit-info-num"):
            basefile = hit.text.split(": ", 1)[1].strip()
            m = re.search(self.basefile_regex, basefile)
            if m:
                basefile = m.group()
            else:
                self.log.warning("Couldn't find a basefile in this label: %r" % basefile)
                continue
            docurl = urljoin(self.start_url, hit.parent.a["href"])
            yield(self.sanitize_basefile(basefile), docurl)
        nextpage = soup.find("div", "search-opt-next").a
        if nextpage:
            nextpage = urljoin(self.start_url,
                               nextpage.get("href"))
        else:
            if self.config.lastyear:
                b = self._next_year(self.config.lastyear)
            else:
                now = datetime.now()
                b = "%s/%s" % (now.year - 1, (now.year) % 100)
            self.log.info("Advancing year from %s to %s" % (self.config.lastyear, b))
            self.config.lastyear = b

        raise NoMoreLinks(nextpage)
    
    def download_single(self, basefile, url=None):
        if url is None:
            url = self.remote_url(basefile)
            if not url:  # remote_url failed
                return

        updated = created = False
        checked = True
        mainattachment = None

        if url in self.urlmap:
            attachment = self.urlmap[url]
        else:
            attachment = self.sniff_attachment(url)
        if attachment:
            self.urlmap[url] = attachment
            attachment += ".html"
        else:
            self.urlmap[url] = ''
            attachment = "index.html"
        
        downloaded_path = self.store.downloaded_path(basefile,
                                                     attachment=attachment)
        
        created = not os.path.exists(downloaded_path)
        if self.download_if_needed(url, basefile, filename=downloaded_path):
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
        del text
        attachment = self.find_attachment(soup)

        extraurls = []
        results = soup.find("div", "search-results-content")
        a = results.find("a", string="Hämta Pdf")
        if a:
            extraurls.append(a.get("href"))
        a = results.find("a", string="Hämta Doc") 
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
                                 url.split("get=")[-1])
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

    def sanitize_identifier(self, identifier):
        return prop_sanitize_identifier(identifier)

    # FixedLayoutSource.downloaded_to_intermediate will always convert
    # things to pdf, even html files. But if we only have html
    # (eg. plaintext, we should work with that)
    def downloaded_to_intermediate(self, basefile):
        downloaded_path = self.store.downloaded_path(basefile)
        if downloaded_path.endswith(".html"):
            return self._extract_text(basefile)
        else:
            return super(PropTrips, self).downloaded_to_intermediate(basefile)

    def extract_head(self, fp, basefile):
        # get metadata from plaintext html even if we have doc/pdf,
        # since plaintext is easiest to extract basic metadata from
        txt = self._extract_text_inner(basefile)[:1000]
        return txt.split("-"*64)[0]

    def extract_metadata(self, rawheader, basefile):
        d = self.metadata_from_basefile(basefile)
        lines = [x.strip() for x in rawheader.split("\n\n") if x.strip()]
        d["dcterms:identifier"] = "Prop. " + lines[0].split('\xb7')[1].strip()
        d["dcterms:title"] = lines[1].strip()
        for p in lines[2:]:
            if p.startswith("Ansvarig myndighet: "):
                d["rpubl:departement"] = p.split(": ", 1)[1]
            elif p.startswith("Dokument: "):
                pass
            else:
                self.log.warning("%s: Unknown header %s" % p)
        return d

    def sanitize_metadata(self, attribs, basefile):
        attribs = super(PropTrips, self).sanitize_metadata(attribs, basefile)
        if ('dcterms:title' in attribs and
            'dcterms:identifier' in attribs and
            attribs['dcterms:title'].endswith(attribs['dcterms:identifier'])):
            x = attribs['dcterms:title'][:-len(attribs['dcterms:identifier'])]
            attribs['dcterms:title'] = util.normalize_space(x)
        return attribs
    
    def extract_body(self, fp, basefile):
        if util.name_from_fp(fp).endswith((".txt", ".txt.bz2")):
            bodystring = fp.read()
            if isinstance(bodystring, bytes):
                # fp is opened in bytestream mode
                bodystring = bodystring.decode("utf-8")
            return TextReader(string=bodystring)
        else:
            reader = super(PropTrips, self).extract_body(fp, basefile)
            pdffile = self.store.downloaded_path(basefile, attachment="index.pdf")
            for page in reader:
                page.src = pdffile
            return reader

    def sanitize_body(self, rawbody):
        if isinstance(rawbody, TextReader):
            return rawbody
        else:
            return super(PropTrips, self).sanitize_body(rawbody)

    def get_parser(self, basefile, sanitized, initialstate=None, startpage=None, pagecount=None, parseconfig="default"):
        if isinstance(sanitized, TextReader):
            return self.textparser
        else:
            return super(PropTrips, self).get_parser(basefile, sanitized, initialstate, startpage, pagecount, parseconfig=parseconfig)

    def tokenize(self, reader):
        if isinstance(reader, TextReader):
            return reader.getiterator(reader.readparagraph)
        else:
            return super(PropTrips, self).tokenize(reader)


class PropRiksdagen(Riksdagen):
    alias = "propriksdagen"
    rdf_type = RPUBL.Proposition
    document_type = Riksdagen.PROPOSITION

    def sanitize_identifier(self, identifier):
        return prop_sanitize_identifier(identifier)


class PropKB(Offtryck, PDFDocumentRepository):
    alias = "propkb"
    storage_policy = "dir"
    start_url = "https://riksdagstryck.kb.se/tvakammarriksdagen.html"
    rdf_type = RPUBL.Proposition
    basefile_regex = "prop_(?P<year>\d{4})__+(?P<no>\d+)(?:_(?P<part>\d+)|)"
    document_type = PROPOSITION = True
    SOU = DS = KOMMITTEDIREKTIV = False

    @classmethod
    def get_default_options(cls):
        opts = super(PropKB, cls).get_default_options()
        opts['ocr'] = False
        return opts

    def download_get_first_page(self):
        # if we have already successfully downloaded everything, there
        # is no need to even make a single network request (and we'd
        # have to do at least 100 otherwise) since no new docs will
        # ever be published (normally -- and if they are, just set
        # config.refresh)
        if (not self.config.refresh and
            'lastdownload' in self.config and
            self.config.lastdownload):
            class DummyResp(object):
                def raise_for_status(self):
                    pass
                text = "<h1>no data</h1>"
            return DummyResp()
        else:
            return super(PropKB, self).download_get_first_page()


    @decorators.downloadmax
    def download_get_basefiles(self, source):
        yielded = set()
        if self.download_reverseorder:
            source = reversed(list(source))
        for (element, attribute, link, pos) in source:
            if not element.text_content():
                continue
            if "proposition" in element.text_content():
                resp = self.session.get(link)
                resp.raise_for_status()
                tree = lxml.html.document_fromstring(resp.text)
                tree.make_links_absolute(link, resolve_base_href=True)
                for (subelement, subattribute, sublink, subpos) in tree.iterlinks():
                    if not subelement.text:
                        continue
                    m = re.match(self.basefile_regex, subelement.text)
                    if m:
                        basefile = "%s:%s" % (m.group("year"), m.group("no"))
                        part = m.group("part")
                        if (basefile,part) in yielded:
                            continue
                        if self.get_parse_options(basefile) == "skip":
                            continue
                        if part and int(part) > 1:
                            # do something smart here so that
                            # download() will treat the yielded value
                            # as an attachment to existing
                            # basefile. Or maybe we could download it
                            # ourselves at this point? Let's try that.
                            filename = self.store.downloaded_path(basefile, attachment=part+".pdf")
                            self.download_if_needed(sublink, basefile, archive=self.download_archive, filename=filename)
                        else:
                            yield basefile, sublink
                            yielded.add((basefile,part))

    def metadata_from_basefile(self, basefile):
        attrib = super(PropKB, self).metadata_from_basefile(basefile) 
        year, ordinal = basefile.split(":")
        attrib["rpubl:arsutgava"] = year
        attrib["rpubl:lopnummer"] = ordinal
        return attrib

    @lazyread
    def downloaded_to_intermediate(self, basefile):
        downloaded_path = self.store.downloaded_path(basefile)
        intermediate_path = self.store.intermediate_path(basefile)
        return self.convert_pdf(downloaded_path, intermediate_path)

    def convert_pdf(self, downloaded_path, intermediate_path):
        intermediate_dir = os.path.dirname(intermediate_path)
        keep_xml = "bz2" if self.config.compress == "bz2" else True
        reader = StreamingPDFReader()
        kwargs = {'filename': downloaded_path,
                  'workdir': intermediate_dir,
                  'images': self.config.pdfimages,
                  'keep_xml': keep_xml}
        if self.config.ocr:
            kwargs['ocr_lang'] = 'swe'
        return reader.convert(**kwargs)

    def extract_head(self, fp, basefile):
        return None  # "rawhead" is never used

    def extract_metadata(self, rawhead, basefile):
        # extracting title and other metadata (dep, publication date
        # etc) requires parsing of the body)
        return self.metadata_from_basefile(basefile)

    def extract_body(self, fp, basefile):
        reader = StreamingPDFReader()
        parser = "ocr" if self.config.ocr else "xml"
        intermediate_suffix = ".hocr" if self.config.ocr else ".xml"
        if self.config.compress:
            intermediate_suffix += "." + self.config.compress
        reader.read(fp, parser=parser)
        for attachment in [x for x in sorted(self.store.list_attachments(basefile, "downloaded")) if x.endswith(".pdf")]:
            downloaded_path = self.store.downloaded_path(basefile, attachment=attachment)
            iattachment = attachment.replace(".pdf", intermediate_suffix)
            intermediate_path = self.store.intermediate_path(basefile, attachment=iattachment)
            if not os.path.exists(intermediate_path):
                fp = self.convert_pdf(downloaded_path, intermediate_path)
            else:
                fp = self.store.open_intermediate(basefile, attachment=iattachment)
            reader += StreamingPDFReader().read(fp)

        for page in reader:
            page.src = "index.pdf"  # FIXME: don't hardcode the filename
        return reader

    def postprocess_doc(self, doc):
        # the first thing will be a Sidbrytning; continue scanning text until next sidbrytning
        firstpage = ""
        for thing in doc.body[1:]:
            if isinstance(thing, Sidbrytning):
                break
            elif isinstance(thing, Textbox):
                firstpage += str(thing) + "\n\n"
        m = re.search("proposition till riksdagen *,? *(.*?); gif?ven",
                      util.normalize_space(firstpage), flags=re.I)
        if not m:
            self.log.warning("%s: Couldn't find title in first %s characters (first page)" %
                             (doc.basefile, len(firstpage)))
        else:
            doc.meta.add((URIRef(doc.uri), DCTERMS.title, Literal(m.group(1), lang=self.lang)))
        m = re.search("gif?ven stockholms slott den (\d+ \w+ \d{4})", util.normalize_space(firstpage), flags=re.I)
        if not m:
            self.log.warning("%s: Couldn't find date in first %s characters (first page)" %
                             (doc.basefile, len(firstpage)))
        else:
            d = self.parse_swedish_date(m.group(1).lower())
            doc.meta.add((URIRef(doc.uri), DCTERMS.issued, Literal(d)))


# inherit list_basefiles_for from CompositeStore, basefile_to_pathfrag
# from SwedishLegalStore)
class PropositionerStore(CompositeStore, SwedishLegalStore):
    pass


class Propositioner(CompositeRepository, FixedLayoutSource):
    subrepos = PropRegeringen, PropTrips, PropRiksdagen, PropKB
    alias = "prop"
    xslt_template = "xsl/forarbete.xsl"
    storage_policy = "dir"
    rdf_type = RPUBL.Proposition
    documentstore_class = PropositionerStore
    sparql_annotations = "sparql/describe-with-subdocs.rq"
    sparql_expect_results = False

    # NB: The same logic as in
    # ferenda.sources.legal.se.{Regeringen,Riksdagen}.metadata_from_basefile
    def metadata_from_basefile(self, basefile):
        a = super(Propositioner, self).metadata_from_basefile(basefile)
        a["rpubl:arsutgava"], a["rpubl:lopnummer"] = basefile.split(":", 1)
        return a

    def facets(self):
        return super(Propositioner, self).facets() + [Facet(DCTERMS.title,
                                                       toplevel_only=False)]

    def tabs(self):
        if self.config.tabs:
            return [('Propositioner', self.dataset_uri())]
        else:
            return []

    # For a certain repo, download_path might return *.wpd (good) or
    # *.html (bad, because unformatted plaintext). If it returns bad,
    # we should continue with other repos that might have
    # *.pdf. HOWEVER, if no other repo has it in any format, we'll
    # have to accept the repo that has it as *.html.
    #
    # NOTE: This implementation does not make use of the
    # self.store.basefiles[c] cache, since that only keeps track of
    # which repos has which basefiles, not the format/quality of the
    # source.
    def get_preferred_instances(self, basefile):
        backups = []
        for c in self.subrepos:
            inst = self.get_instance(c)
            source_candidate = inst.store.downloaded_path(basefile)
            if os.path.exists(source_candidate):
                if c.alias != "propregeringen" and source_candidate.endswith(".html"):
                    backups.append(inst)
                else:
                    yield(inst)
        for inst in backups:
            yield(inst)
