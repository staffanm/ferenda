# -*- coding: utf-8 -*-
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
from builtins import *

from tempfile import mktemp
from urllib.parse import urljoin, unquote, urlparse
from xml.sax.saxutils import escape as xml_escape
from io import BytesIO
import os
import re
import json
import functools
from collections import OrderedDict
try:
    from functools import lru_cache
except ImportError:
    from backports.functools_lru_cache import lru_cache

from rdflib import URIRef, Literal, Namespace
from bs4 import BeautifulSoup
import requests
import lxml.html
import datetime
from rdflib import RDF, Graph
from rdflib.resource import Resource
from rdflib.namespace import DCTERMS, SKOS
from layeredconfig import LayeredConfig

from . import RPUBL, RINFOEX, SwedishLegalSource, FixedLayoutSource
from .fixedlayoutsource import FixedLayoutStore
from .swedishlegalsource import SwedishCitationParser, SwedishLegalStore
from ferenda import TextReader, Describer, Facet, PDFReader, DocumentEntry, DocumentRepository, PDFReader
from ferenda import util, decorators, errors, fulltextindex
from ferenda.elements import Body, Page, Preformatted, Link
from ferenda.elements.html import elements_from_soup
from ferenda.sources.legal.se.legalref import LegalRef

PROV = Namespace(util.ns['prov'])

# NOTE: Since the main parse logic operates on the output of
# pdftotext, not pdftohtml, there is no real gain in subclassing
# FixedLayoutSource even though the goals of that repo is very similar
# to most MyndFskrBase derived repos. Also, there are repos that do
# not contain PDF files (DVFS).

class RequiredTextMissing(errors.ParseError): pass

class MyndFskrStore(FixedLayoutStore):
    # downloaded_suffixes = [".pdf", ".html", ".docx"]
    pass

    
def recordlastbasefile(f):
    """Decorator for download_get_basefiles that automatically stores last
    downloaded basefile in config.last_basefile, and automatically
    stops reading from the generator that download_get_basefiles
    provide once it sees older basefiles that we already have (useful
    for multi-page listings).

    """
    @functools.wraps(f)
    def wrapper(self, *args, **kwargs):
        def fsnr(s):
            if "/" in s:
                return s.split("/")[-1]
            else:
                return s
        if 'last_basefile' in self.config:
            new_last_basefile = self.config.last_basefile
        else:
            new_last_basefile = "0000:000"
        for basefile, link in f(self, *args, **kwargs):
            if (not self.config.refresh) and 'last_basefile' in self.config:
                if util.split_numalpha(fsnr(basefile)) <= util.split_numalpha(self.config.last_basefile):
                    self.log.debug("config.last_basefile is %s, not examining basefile %s or any other after that" % (self.config.last_basefile, basefile))
                    return

            # FIXME: this can be pretty inaccurate if the source provides multiple FS:s (eg KVFS and the older KVVFS). We should only look at the fsnr part if the fsname is prepended
            if util.split_numalpha(fsnr(basefile)) > util.split_numalpha(new_last_basefile):
                new_last_basefile = fsnr(basefile)
            yield basefile, link
            
        if 'last_basefile' in self.config:
            self.config.last_basefile = new_last_basefile
        else:
            self.log.warning("Unable to record last_basefile = %s, please add this manually to ferenda.ini" % new_last_basefile)
        LayeredConfig.write(self.config)
    return wrapper

class MyndFskrBase(FixedLayoutSource):
    """A abstract base class for fetching and parsing regulations from
    various swedish government agencies. These documents often have a
    similar structure both linguistically and graphically (most of the
    time they are in similar PDF documents), enabling us to parse them
    in a generalized way. (Downloading them often requires
    special-case code, though.)

    """
    source_encoding = "utf-8"
    downloaded_suffix = ".pdf"
    alias = 'myndfskr'
    storage_policy = 'dir'
    xslt_template = "xsl/myndfskr.xsl"

    rdf_type = (RPUBL.Myndighetsforeskrift, RPUBL.AllmannaRad)
    # FIXME: For docs of rdf:type rpubl:KonsolideradGrundforfattning,
    # not all of the above should be required (rpubl:beslutadAv,
    # rpubl:beslutsdatum, rpubl:forfattningssamling (in fact, that one
    # shoud not be present), rpubl:ikrafttradandedatum,
    # rpubl:utkomFranTryck
    
    basefile_regex = re.compile('(?P<basefile>\d{4}[:/_-]\d{1,3})(?:|\.\w+)$')
    document_url_regex = re.compile('.*(?P<basefile>\d{4}[:/_-]\d{1,3}).pdf$')
    download_accept_404 = True  # because the occasional 404 is to be expected
    download_record_last_download = False
    nextpage_regex = None
    nextpage_url_regex = None
    download_rewrite_url = False # iff True, use remote_url to rewrite download links instead of
    # accepting found links as-is. If it's a callable, call that with
    # basefile, URL and expect a rewritten URL.
    landingpage = False # if true, any basefile/url pair discovered by
                        # download_get_basefiles returns a HTML page,
                        # on which the link to the real PDF file
                        # exists.
    landingpage_url_regex = None
    download_formid = None  # if the paging uses forms, POSTs and other forms of insanity
    download_stay_on_site = False
    documentstore_class = MyndFskrStore

    # FIXME: Should use self.get_parse_options
    blacklist = set(["fohmfs/2014:1",  # Föreskriftsförteckning, inte föreskrift
                     "myhfs/2013:2",   # Annan förteckning "FK" utan beslut
                     "myhfs/2013:5",   #   -""-
                     "myhfs/2014:4",   #   -""-
                     "myhfs/2012:1",   # Saknar bara beslutsdatum, förbiseende? Borde kunna fixas med baseprops
                    ])

    # for some badly written docs, certain metadata properties cannot
    # be found. We list missing properties here, as a last resort.
    # FIXME: This should use the options get_parse_options systems
    # instead (howeever, that needs to be made more flexible with
    # subkeys/multiple options
    baseprops = {'nfs/2004:5': {"rpubl:beslutadAv": "Naturvårdsverket"},
                 'sosfs/1982:13': {"rpubl:beslutadAv": "Socialstyrelsen"},
                 'sjvfs/1991:2': {"dcterms:identifier": "SJVFS 1991:2"}
                 }


    def __init__(self, config=None, **kwargs):
        super(MyndFskrBase, self).__init__(config, **kwargs)
        # unconditionally set downloaded_suffixes, since the
        # conditions for this re-set in DocumentRepository.__init__ is
        # too rigid
#        if hasattr(self, 'downloaded_suffixes'):
#            self.store.downloaded_suffixes = self.downloaded_suffixes
#        else:
#            self.store.downloaded_suffixes = [self.downloaded_suffix]

    @classmethod
    def get_default_options(cls):
        opts = super(MyndFskrBase, cls).get_default_options()
        opts['pdfimages'] = True
        if 'cssfiles' not in opts:
            opts['cssfiles'] = []
        opts['cssfiles'].append('css/pdfview.css')
        if 'jsfiles' not in opts:
            opts['jsfiles'] = []
        opts['jsfiles'].append('js/pdfviewer.js')
        return opts

    def remote_url(self, basefile):
        # if we already know the remote url, don't go to the landing page
        if os.path.exists(self.store.documententry_path(basefile)):
            entry = DocumentEntry(self.store.documententry_path(basefile))
            return entry.orig_url
        else:
            return super(MyndFskrBase, self).remote_url(basefile)

    def get_required_predicates(self, doc):
        rdftype = doc.meta.value(URIRef(doc.uri), RDF.type)
        req = [RDF.type, DCTERMS.title,
               DCTERMS.identifier, RPUBL.arsutgava,
               DCTERMS.publisher, RPUBL.beslutadAv,
               RPUBL.beslutsdatum,
               RPUBL.forfattningssamling,
               RPUBL.ikrafttradandedatum, RPUBL.lopnummer,
               RPUBL.utkomFranTryck, PROV.wasGeneratedBy]
        if rdftype == RPUBL.Myndighetsforeskrift:
            return req
        elif rdftype == RPUBL.Myndighetsforeskrift:
            return req + [RPUBL.bemyndigande]
        elif rdftype == RPUBL.KonsolideradGrundforfattning:
            return [RDF.type, DCTERMS.title,
                    DCTERMS.identifier, RPUBL.arsutgava,
                    DCTERMS.publisher, RPUBL.lopnummer,
                    PROV.wasGeneratedBy]
        else:
            return super(MyndFskrBase, self).get_required_predicates(doc)

    def forfattningssamlingar(self):
        return [self.alias]

    def sanitize_basefile(self, basefile):
        segments = re.split('[ \./:_-]+', basefile.lower())
        # force "01" to "1" (and check integerity (not integrity))
        segments[-1] = str(int(segments[-1]))
        if len(segments) == 2:
            basefile = "%s:%s" % tuple(segments)
        elif len(segments) == 3:
            basefile = "%s/%s:%s" % tuple(segments)
        elif len(segments) == 4 and segments[1] == "fs":  # eg for ELSÄK-FS, HSLF-FS and others
            basefile = "%s%s/%s:%s" % tuple(segments) # eliminate the hyphen in the fs name
        else:
            raise ValueError("Can't sanitize %s" % basefile)
        if not any((basefile.startswith(fs + "/") for fs
                    in self.forfattningssamlingar())):
            return self.forfattningssamlingar()[0] + "/" + basefile
        else:
            return basefile

    @decorators.downloadmax
    def download_get_basefiles(self, source):
        # this is an extended version of
        # DocumentRepository.download_get_basefiles which handles
        # "next page" navigation and also ensures that the default
        # basefilepattern is "myndfs/2015:1", not just "2015:1"
        # (through sanitize_basefile)
        yielded = set()
        while source:
            nextform = nexturl = None
            for (element, attribute, link, pos) in source:
                # FIXME: Maybe do a full HTTP decoding later, but this
                # should not cause any regressons, maybe
                link = link.replace("%20", " ")
                if element.tag not in ("a", "form"):
                    continue
                # Three step process to find basefiles depending on
                # attributes that subclasses can customize
                # basefile_regex match. If not, examine link url to
                # see if document_url_regex
                # print("examining %s (%s)" % (link, bool(re.match(self.document_url_regex, link))))
                # continue
                elementtext = " ".join(element.itertext())
                m = None
                if self.download_stay_on_site and urlparse(self.start_url).netloc != urlparse(link).netloc:
                    continue
                if (self.landingpage and self.landingpage_url_regex and
                    re.match(self.landingpage_url_regex, link)):
                    m = re.match(self.landingpage_url_regex, link)
                elif (self.basefile_regex and
                      elementtext and
                      re.search(self.basefile_regex, elementtext)):
                    m = re.search(self.basefile_regex, elementtext)
                elif (not self.landingpage and
                      self.document_url_regex and
                      re.match(self.document_url_regex, link)):
                    m = re.match(self.document_url_regex, link)
                if m:
                    basefile = self.sanitize_basefile(m.group("basefile"))
                    # since download_rewrite_url is potentially
                    # expensive (might do a HTTP request), we should
                    # perhaps check if we really need to download
                    # this. NB: this is duplicating logic from
                    # DocumentRepository.download.
                    if (os.path.exists(self.store.downloaded_path(basefile))
                        and not self.config.refresh):
                        continue
                    if basefile not in yielded:
                        yield (basefile, link)
                        yielded.add(basefile)
                if (self.nextpage_regex and elementtext and
                        re.search(self.nextpage_regex, elementtext)):
                    nexturl = link
                elif (self.nextpage_url_regex and
                      re.search(self.nextpage_url_regex, link)):
                    nexturl = link
                if (self.download_formid and
                        element.tag == "form" and
                        element.get("id") == self.download_formid):
                    nextform = element
            if nextform is not None and nexturl is not None:
                resp = self.download_post_form(nextform, nexturl)
            elif nexturl is not None:
                resp = self.session.get(nexturl)
            else:
                resp = None
                source = None

            if resp:
                tree = lxml.html.document_fromstring(resp.text)
                tree.make_links_absolute(resp.url,
                                         resolve_base_href=True)
                source = tree.iterlinks()

    def download_single(self, basefile, url=None, orig_url=None):
        if self.download_rewrite_url:
            if callable(self.download_rewrite_url):
                url = self.download_rewrite_url(basefile, url)
            else:
                url = self.remote_url(basefile)
        orig_url = None
        if self.landingpage:
            # get landingpage, find real url on it (as determined by
            # .document_url_regex or .basefile_regex)
            resp = self.session.get(url)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "lxml")
            if self.document_url_regex:
                # FIXME: Maybe sanity check that the basefile matched
                # is the same basefile as provided to this function?
                link = soup.find("a", href=self.document_url_regex)
            if link is None and self.basefile_regex:
                link = soup.find("a", text=self.basefile_regex)
            if link:
                orig_url = url
                url = urljoin(orig_url, link.get("href"))
            else:
                self.log.warning("%s: Couldn't find document from landing page %s" % (basefile, url))
        ret = super(MyndFskrBase, self).download_single(basefile, url, orig_url)
        if self.downloaded_suffix == ".pdf":
            # assure that the downloaded resource really is a PDF, or
            # possibly rename it if it's one of the other supported
            # types
            downloaded_file = self.store.downloaded_path(basefile)
            with open(downloaded_file, "rb") as fp:
                sig = fp.read(4)
            for suffix, typesig in self.store.doctypes.items():
                if sig == typesig:
                    if suffix != ".pdf":
                        other_file = downloaded_file.replace(".pdf", suffix)
                        util.robust_rename(downloaded_file, other_file)
                    break
            else:
                other_file = downloaded_file.replace(".pdf", ".bak")
                util.robust_rename(downloaded_file, other_file)
                raise errors.DownloadFileNotFoundError("%s: downloaded file has sig %r that doesn't match any expected filetype (%s), saved at %s" % (basefile, sig, ",".join(self.store.doctypes.keys()), other_file))
        return ret

    def download_post_form(self, form, url):
        raise NotImplementedError

    def _basefile_frag_to_altlabel(self, basefilefrag):
        # optionally map fs identifier to match skos:altLabel.
        return {'ELSAKFS': 'ELSÄK-FS',
                'HSLFFS': 'HSLF-FS',
                'FOHMFS': 'FoHMFS',
                'RAFS': 'RA-FS',
                'SVKFS': 'SvKFS'}.get(basefilefrag, basefilefrag)

    @lru_cache(maxsize=None)
    def metadata_from_basefile(self, basefile):
        a = super(MyndFskrBase, self).metadata_from_basefile(basefile)
        # munge basefile or classname to find the skos:altLabel of the
        # forfattningssamling we're dealing with
        assert "/" in basefile, "%s is not a valid basefile (should be something like %s/%s)" % (self.__class__.__name__.lower(), basefile)
        segments = basefile.split("/")
        if len(segments) > 2 and segments[0] == "konsolidering":
            a["rdf:type"] = RPUBL.KonsolideradGrundforfattning
            a["rpubl:konsoliderar"] = URIRef(self.canonical_uri(basefile.split("/",1)[1]))
            # FIXME: Technically, we're not deriving
            # dcterms:issued from the basefile alone
            # (consolidation_date might read PDF and/or HTML files
            # to get this data). However, due to the order of
            # calls in SwedishLegalSource.canonical_uri, this
            # method is required to return all metadata needed to
            # construct the URI, which means we need to come up
            # with a date (or really any identifying string, like
            # a fsnummer) at this point.
            a["dcterms:issued"] = self.consolidation_date(basefile)
            segments.pop(0)
        else:
            # only set rpubl:forfattningssamling on real acts
            # (actually published in a författningssamling). Partly
            # because this is correct (an KonsolideradGrundforfattning
            # is not published in a författningssamling), partly
            # because this avoids matching the wrong coin:template
            # when minting URIs for them.
            fslabel = self._basefile_frag_to_altlabel(segments[0].upper())
            a["rpubl:forfattningssamling"] = self.lookup_resource(fslabel,
                                                                  SKOS.altLabel)
        fs, realbasefile = segments
        # fs = fs.upper()
        # fs = self._basefile_frag_to_altlabel(fs)
        a["rpubl:arsutgava"], a["rpubl:lopnummer"] = realbasefile.split(":", 1)
        return a

    def consolidation_date(self, basefile):
        # subclasses should override this and dig out real data somewhere
        return datetime.date.today() 

    urispace_segment = ""
    
    def basefile_from_uri(self, uri):
        # this should map
        # https://lagen.nu/sjvfs/2014:9 to basefile sjvfs/2014:9
        # https://lagen.nu/dfs/2007:8 -> dfs/2007:8
        # https://lagen.nu/afs/2011:19/konsolidering/2018-04-17 -> konsolidering/afs/2011:19
        basefile = super(MyndFskrBase, self).basefile_from_uri(uri)
        if basefile is None:
            return basefile
        # re-arrange konsolideringsinformation
        prefix = ""
        if "/konsolidering/" in basefile:
            prefix = "konsolidering/"
            basefile = prefix + basefile.split("/konsolidering/")[0]
        # since basefiles are always wihout hyphens, but URIs for
        # författningssamlingar like HSLF-FS will contain a hyphen, we
        # remove it here.
        basefile = basefile.replace("-", "")
        for fs in self.forfattningssamlingar():
            # FIXME: use self.coin_base (self.urispace_base) instead.
            if basefile.startswith(prefix + fs):
                return basefile

    def extract_head(self, fp, basefile, force_ocr=False, attachment=None):
        infile = self.store.downloaded_path(basefile, attachment=attachment)
        tmpfile = self.store.path(basefile, 'intermediate', '.pdf')
        outfile = self.store.path(basefile, 'intermediate', '.txt')
        if not util.outfile_is_newer([infile], outfile):
            if infile.endswith(".pdf") or not os.path.exists(tmpfile):
                # if infile does not end with pdf, an existing tmpfile
                # means that there has been an eg. doc -> pdf
                # conversion done by downloaded_to_intermediate. Don't
                # overwrite that one!
                util.copy_if_different(infile, tmpfile)
            with open(tmpfile, "rb") as fp:
                if fp.read(4) != b'%PDF':
                    raise errors.ParseError("%s is not a PDF file" % tmpfile)
            # this command will create a file named as the val of outfile
            util.runcmd("pdftotext %s" % tmpfile, require_success=True)
            # check to see if the outfile actually contains any text. It
            # might just be a series of scanned images.
            text = util.readfile(outfile)
            if not text.strip() or force_ocr:
                os.unlink(outfile)
                # OK, it's scanned images. We extract these, put them in a
                # tif file, and OCR them with tesseract.
                self.log.debug("%s: No text in PDF, trying OCR" % basefile)
                p = PDFReader()
                p._tesseract(tmpfile, os.path.dirname(outfile), "swe", False)
                tmptif = self.store.path(basefile, 'intermediate', '.tif')
                util.robust_remove(tmptif)
        # remove control chars so that they don't end up in the XML
        # (control chars might stem from text segments with weird
        # character encoding, see pdfreader.BaseTextDecoder)
        bytebuffer = util.readfile(outfile, "rb")
        newbuffer = BytesIO()
        warnings = []
        for idx, b in enumerate(bytebuffer):
            # allow CR, LF, FF, TAB
            if b < 0x20 and b not in (0xa, 0xd, 0xc, 0x9):
                warnings.append(idx)
            else:
                newbuffer.write(bytes((b,)))
        if warnings:
            self.log.warning("%s: Invalid character(s) at byte pos %s" %
                             (basefile, ", ".join([str(x) for x in warnings])))
        newbuffer.seek(0)
        text = newbuffer.getvalue().decode("utf-8")
        # if there's less than 100 chars on each page, chances are it's
        # just watermarks or leftovers from the scanning toolchain,
        # and that the real text is in non-OCR:ed images.
        if len(text) / (text.count("\x0c") + 1) < 100:
            self.log.warning("%s: Extracted text from PDF suspiciously short "
                             "(%s bytes per page, %s total)" %
                             (basefile,
                              len(text) / text.count("\x0c") + 1,
                              len(text)))
            # parse_metadata_from_textreader will raise an error if it
            # can't find what it needs, at which time we might
            # consider OCR:ing. FIXME: Do something with this
            # parameter!
            self.might_need_ocr = True 
        else:
            self.might_need_ocr = False
        util.robust_remove(tmpfile)
        text = self.sanitize_text(text, basefile)
        return TextReader(string=text, encoding=self.source_encoding,
                          linesep=TextReader.UNIX)

    def extract_metadata(self, reader, basefile):
        props = self.metadata_from_basefile(basefile)
        if props.get("rdf:type", "").endswith("#KonsolideradGrundforfattning"):
            props = self.parse_metadata_from_consolidated(reader, props, basefile)
        else:
            try:
                props = self.parse_metadata_from_textreader(reader, props, basefile)
            except RequiredTextMissing:
                if self.might_need_ocr:
                    self.log.warning("%s: reprocessing using OCR" % basefile)
                    reader = self.textreader_from_basefile(basefile, force_ocr=True)
                    props = self.parse_metadata_from_textreader(reader, props, basefile)
                else:
                    raise
        return props

    # subclasses should override this and make to add a suitable set
    # of triples (particularly rpubl:konsolideringsunderlag) to
    # doc.meta.
    def parse_metadata_from_consolidated(self, reader, props, basefile):
        return props
    
    def textreader_from_basefile(self, basefile, force_ocr=False, attachment=None):
        infile = self.store.downloaded_path(basefile, attachment=attachment)
        tmpfile = self.store.path(basefile, 'intermediate', '.pdf')
        outfile = self.store.path(basefile, 'intermediate', '.txt')
        return self.textreader_from_basefile_pdftotext(infile, tmpfile, outfile, basefile, force_ocr)

    def textreader_from_basefile_pdftotext(self, infile, tmpfile, outfile, basefile, force_ocr=False):
        if not util.outfile_is_newer([infile], outfile):
            util.copy_if_different(infile, tmpfile)
            with open(tmpfile, "rb") as fp:
                if fp.read(4) != b'%PDF':
                    raise errors.ParseError("%s is not a PDF file" % tmpfile)
            # this command will create a file named as the val of outfile
            util.runcmd("pdftotext %s" % tmpfile, require_success=True)
            # check to see if the outfile actually contains any text. It
            # might just be a series of scanned images.
            text = util.readfile(outfile)
            if not text.strip() or force_ocr:
                os.unlink(outfile)
                # OK, it's scanned images. We extract these, put them in a
                # tif file, and OCR them with tesseract.
                self.log.debug("%s: No text in PDF, trying OCR" % basefile)
                p = PDFReader()
                p._tesseract(tmpfile, os.path.dirname(outfile), "swe", False)
                tmptif = self.store.path(basefile, 'intermediate', '.tif')
                util.robust_remove(tmptif)

                
        # remove control chars so that they don't end up in the XML
        # (control chars might stem from text segments with weird
        # character encoding, see pdfreader.BaseTextDecoder)
        bytebuffer = util.readfile(outfile, "rb")
        newbuffer = BytesIO()
        warnings = []
        for idx, b in enumerate(bytebuffer):
            # allow CR, LF, FF, TAB
            if b < 0x20 and b not in (0xa, 0xd, 0xc, 0x9):
                warnings.append(idx)
            else:
                newbuffer.write(bytes((b,)))
        if warnings:
            self.log.warning("%s: Invalid character(s) at byte pos %s" %
                             (basefile, ", ".join([str(x) for x in warnings])))
        newbuffer.seek(0)
        text = newbuffer.getvalue().decode("utf-8")
        # if there's less than 100 chars on each page, chances are it's
        # just watermarks or leftovers from the scanning toolchain,
        # and that the real text is in non-OCR:ed images.
        if len(text) / (text.count("\x0c") + 1) < 100:
            self.log.warning("%s: Extracted text from PDF suspiciously short "
                             "(%s bytes per page, %s total)" %
                             (basefile,
                              len(text) / text.count("\x0c") + 1,
                              len(text)))
            # parse_metadata_from_textreader will raise an error if it
            # can't find what it needs, at which time we might
            # consider OCR:ing. FIXME: Do something with this
            # parameter!
            self.might_need_ocr = True 
        else:
            self.might_need_ocr = False
        util.robust_remove(tmpfile)
        text = self.sanitize_text(text, basefile)
        return TextReader(string=text, encoding=self.source_encoding,
                          linesep=TextReader.UNIX)

    def sanitize_text(self, text, basefile):
        return text

    def fwdtests(self):
        return {'dcterms:issn': ['^ISSN (\d+\-\d+)$'],
                'dcterms:title':
                ['((?:Föreskrifter|[\w ]+s (?:föreskrifter|allmänna råd)).*?)[;\n]\n'],
                'dcterms:identifier': ['^([A-ZÅÄÖ-]+FS\s\s?\d{4}:\d+)$'],
                'rpubl:utkomFranTryck':
                ['Utkom från\strycket\s+den\s(\d+ \w+ \d{4})',
                 'Utkom från\strycket\s+(\d{4}-\d{2}-\d{2})'],
                'rpubl:omtryckAv': ['^(Omtryck)$'],
                'rpubl:genomforDirektiv': ['Celex (3\d{2,4}\w\d{4})'],
                'rpubl:beslutsdatum':
                ['(?:har beslutats|[Bb]eslutade|beslutat|[Bb]eslutad)(?: den|) (\d+ \w+( \d{4}|))',
                 'Beslutade av (?:[A-ZÅÄÖ][\w ]+) den (\d+ \w+ \d{4}).',
                 'utfärdad den (\d+ \w+ \d{4}) tillkännages härmed i andra hand.',
                 '(?:utfärdad|meddelad)e? den (\d+ \w+ \d{4}).'],
                'rpubl:beslutadAv':
                ['\s(?:meddelar|lämnar|föreskriver|beslutar)\s([A-ZÅÄÖ][\w ]+?)\d?\s',
                 '\n\s*([A-ZÅÄÖ][\w ]+?)\d? (?:meddelar|lämnar|föreskriver|beslutar)',
                 ],
                'rpubl:bemyndigande':
                [' ?(?:meddelar|föreskriver|Föreskrifterna meddelas|Föreskrifterna upphävs)\d?,? (?:följande |)med stöd av\s(.*?) ?(?:att|efter\ssamråd|dels|följande|i fråga om|och lämnar allmänna råd|och beslutar följande allmänna råd|\.\n)',
                 '^Med stöd av (.*)\s(?:meddelar|föreskriver)']
                }

    def revtests(self):
        return {'rpubl:ikrafttradandedatum':
                ['(?:Denna författning|Dessa föreskrifter|Dessa allmänna råd|Dessa föreskrifter och allmänna råd)\d* träder i ?kraft den (\d+ \w+ \d{4})',
                 'Dessa föreskrifter träder i kraft, (?:.*), i övrigt den (\d+ \w+ \d{4})',
                 'ska(?:ll|)\supphöra att gälla (?:den |)(\d+ \w+ \d{4}|denna dag|vid utgången av \w+ \d{4})',
                 'träder i kraft den dag då författningen enligt uppgift på den (utkom från trycket)'],
                'rpubl:upphaver':
                ['träder i kraft den (?:\d+ \w+ \d{4}), då(.*)ska upphöra att gälla',
                 'ska(?:ll|)\supphöra att gälla vid utgången av \w+ \d{4}, nämligen(.*?)\n\n',
                 'att (.*) skall upphöra att gälla (denna dag|vid utgången av \w+ \d{4})']
                }

                 

    def parse_metadata_from_textreader(self, reader, props, basefile):
        # 1. Find some of the properties on the first page (or the
        #    2nd, or 3rd... continue past TOC pages, cover pages etc
        #    until the "real" first page is found) NB: FFFS 2007:1
        #    has ten (10) TOC pages!
        pagecount = 0
        # It's an open question if we should require all properties on
        # the same page or if we can glean one from page 1, another
        # from page 2 and so on. AFS 2014:44 requires that we glean
        # dcterms:title from page 1 and rpubl:beslutsdatum from page
        # 2.
        props.update(self.baseprops.get(basefile, {}))
        for page in reader.getiterator(reader.readpage):
            pagecount += 1
            for (prop, tests) in list(self.fwdtests().items()):
                if prop in props:
                    continue
                for test in tests:
                    m = re.search(
                        test, page, re.MULTILINE | re.DOTALL | re.UNICODE)
                    if m:
                        props[prop] = util.normalize_space(m.group(1))
                        break
            # Single required propery. If we find this, we're done (ie
            # we've skipped past the toc/cover pages).
            if 'rpubl:beslutsdatum' in props:
                break
            self.log.debug("%s: Couldn't find required props on page %s" %
                           (basefile, pagecount))
        if 'rpubl:beslutsdatum' not in props:
            # raise errors.ParseError(
            self.log.warning(
                "%s: Couldn't find required properties on any page, giving up" %
                basefile)

        # 2. Find some of the properties on the last 'real' page (not
        #    counting appendicies)
        reader.seek(0)
        pagesrev = reversed(list(reader.getiterator(reader.readpage)))
        # The language used to expres these two properties differ
        # quite a lot, more than what is reasonable to express in a
        # single regex. We therefore define a set of possible
        # expressions and try them in turn.
        revtests = self.revtests()
        cnt = 0
        for page in pagesrev:
            cnt += 1
            # Normalize the whitespace in each paragraph so that a
            # linebreak in the middle of the natural language
            # expression doesn't break our regexes.
            page = "\n\n".join(
                [util.normalize_space(x) for x in page.split("\n\n")])

            for (prop, tests) in list(revtests.items()):
                if prop in props:
                    continue
                for test in tests:
                    # Not re.DOTALL -- we've normalized whitespace and
                    # don't want to match across paragraphs
                    m = re.search(test, page, re.MULTILINE | re.UNICODE)
                    if m:
                        props[prop] = util.normalize_space(m.group(1))

            # Single required propery. If we find this, we're done
            if 'rpubl:ikrafttradandedatum' in props:
                break
        return props

    def sanitize_metadata(self, props, basefile):
        """Correct those irregularities in the extracted metadata that we can
           find

        """
        konsolidering = props.get("rdf:type", "").endswith("#KonsolideradGrundforfattning")
        # common false positive
        if 'dcterms:title' in props:
            if 'denna f\xf6rfattning har beslutats den' in props['dcterms:title']:
                del props['dcterms:title']
            elif ("\nbeslutade den " in props['dcterms:title'] or
                  "; beslutade den " in props['dcterms:title']):
                # sometimes the title isn't separated with two
                # newlines from the rest of the text
                props['dcterms:title'] = props[
                    'dcterms:title'].split("beslutade den ")[0]
        if 'rpubl:bemyndigande' in props:
            props['rpubl:bemyndigande'] = props[
                'rpubl:bemyndigande'].replace('\u2013', '-')
        if 'dcterms:identifier' in props:
            # "DVFS 2012-4" -> "DVFS 2012:4"
            if re.search("\d{4}-\d+", props['dcterms:identifier']):
                props['dcterms:identifier'] = re.sub(r"(\d{4})-(\d+)", r"\1:\2", props['dcterms:identifier'])
            # if the found dcterms:identifier differs from what has
            # been inferred by metadata_from_basefile, the keys
            # rpubl:arsutgava, rpubl:lopnummer and possibly
            # rpubl:forfattningssamling might be wrong. Re-set these
            # now that we have the correct identifier
            if not konsolidering:
                fs, year, no = re.split("[ :]", props['dcterms:identifier'])
                if year != props['rpubl:arsutgava'] or no != props['rpubl:lopnummer']:
                    realbasefile = self.sanitize_basefile(props['dcterms:identifier'])
                    self.log.warning("Assumed basefile was %s but turned out to be %s" % (basefile, realbasefile))
                    props.update(self.metadata_from_basefile(realbasefile))
        else:
            # do a a simple inference from basefile and populate props
            parts = re.split('[/:_]', basefile.upper())
            if konsolidering:
                parts.pop(0)
            (pub, year, ordinal) = parts
            pub = self._basefile_frag_to_altlabel(pub)
            props['dcterms:identifier'] = "%s %s:%s" % (pub, year, ordinal)
            if konsolidering:
                props['dcterms:identifier'] += " (konsoliderad)"
        return props

    def polish_metadata(self, attributes, basefile, infer_nodes=True):
        """Clean up data, including converting a string->string dict to a
        proper RDF graph.

        """
        def makeurl(attributes):
            resource = self.attributes_to_resource(attributes)
            return self.minter.space.coin_uri(resource)

        parser = SwedishCitationParser(LegalRef(LegalRef.LAGRUM),
                                       self.minter,
                                       self.commondata)
        # FIXME: this code should go into canonical_uri, if we can
        # find a way to give it access to attributes['dcterms:identifier']
        konsolidering = attributes.get("rdf:type", "").endswith("#KonsolideradGrundforfattning")

        # publisher for the series == publisher for the document
        if "dcterms:publisher" not in attributes:
            publisher = self.commondata.value(attributes['rpubl:forfattningssamling'],
                                              DCTERMS.publisher)
            assert publisher, "Found no publisher for fs %s" % fs
            attributes["dcterms:publisher"] = publisher

        if 'rpubl:beslutadAv' in attributes:
            # The agencies sometimes doesn't use it's official name!
            if attributes['rpubl:beslutadAv'] == "Räddningsverket":  
                self.log.warning("rpubl:beslutadAv was '%s', "
                                 "correcting to 'Statens räddningsverk'" %
                                 attributes['rpubl:beslutadAv'])
                attributes['rpubl:beslutadAv'] = "Statens räddningsverk"
            if attributes['rpubl:beslutadAv'] == "Jordbruksverket":
                self.log.warning("rpubl:beslutadAv was '%s', "
                                 "correcting to 'Statens jordbruksverk'" %
                                 attributes['rpubl:beslutadAv'])
                attributes['rpubl:beslutadAv'] = "Statens jordbruksverk"
            try:
                attributes['rpubl:beslutadAv'] = self.lookup_resource(attributes['rpubl:beslutadAv'])
            except KeyError as e:
                beslutad_av = attributes['rpubl:beslutadAv']
                del attributes['rpubl:beslutadAv']
                if self.alias == "ffs":
                    # These documents are often enacted by entities
                    # like Chefen för Flygvapnet, Försvarets
                    # sjukvårdsstyrelse, Generalläkaren, Krigsarkivet,
                    # Överbefälhavaren. We have no resources for those
                    # and probably won't have (are they even
                    # enumerable?)
                    self.log.warning("Couldn't look up entity '%s'" %
                                     (beslutad_av))
                else:
                    raise e
                
        if 'dcterms:title' in attributes:
            if re.search('^(Föreskrifter|[\w ]+s föreskrifter) om ändring (i|av) ',
                         attributes['dcterms:title'], re.UNICODE):
                # There should be something like FOOFS 2013:42 (or
                # possibly just 2013:42) in the title. The regex is
                # forgiving about spurious spaces, seee LVFS 1998:5
                m = re.search('(?P<fs>[A-ZÅÄÖ-]+FS|) ?(?P<year>\d{4}) ?:(?P<ordinal>\d+)',
                              attributes['dcterms:title'])
                if not m:
                    # raise errors.ParseError(
                    self.log.warning(
                        "Couldn't find reference to change act in title %r" %
                        (attributes['dcterms:title']))
                    # in some cases (eg dvfs/2001:2) the fs number is
                    # omitted in the title, but is part of the main
                    # body text (though not in a standardized form)
                else:
                    parts = m.groupdict()
                    if not parts['fs']:
                        parts["fs"] = attributes['dcterms:identifier'].split(" ")[0]

                    origuri = makeurl({'rdf:type': RPUBL.Myndighetsforeskrift,
                                       'rpubl:forfattningssamling':
                                       self.lookup_resource(parts["fs"], SKOS.altLabel),
                                       'rpubl:arsutgava': parts["year"],
                                       'rpubl:lopnummer': parts["ordinal"]})
                    attributes["rpubl:andrar"] =  URIRef(origuri)

            # FIXME: is this a sensible value for rpubl:upphaver?
            if (re.search('^(Föreskrifter|[\w ]+s föreskrifter) om upphävande '
                          'av', attributes['dcterms:title'], re.UNICODE)
                    and not 'rpubl:upphaver' in attributes):
                attributes['rpubl:upphaver'] = attributes['dcterms:title']
            # finally type the title as a swedish-language literal
            attributes['dcterms:title'] = Literal(attributes['dcterms:title'], lang="sv")

        for key, pred in (('rpubl:utkomFranTryck', RPUBL.utkomFranTryck),
                          ('rpubl:beslutsdatum', RPUBL.beslutsdatum),
                          ('rpubl:ikrafttradandedatum', RPUBL.ikrafttradandedatum)):
            if key in attributes:
                if (key == 'rpubl:ikrafttradandedatum' and 
                    attributes[key] in ('denna dag', 'utkom från trycket')):
                    if attributes[key] == 'denna dag':
                        attributes[key] = attributes['rpubl:beslutsdatum']
                    elif attributes[key] == 'utkom från trycket':
                        attributes[key] = attributes['rpubl:utkomFranTryck']
                try:
                    attributes[key] = Literal(self.parse_swedish_date(attributes[key]))
                except ValueError as e:
                    self.log.warning("Couldn't parse date '%s' for %s: %s" % (attributes[key], key, e))
                    # and then go on

        if 'rpubl:genomforDirektiv' in attributes:
            attributes['rpubl:genomforDirektiv'] = URIRef(makeurl(
                {'rdf:type': RINFOEX.EUDirektiv, # FIXME: standardize this type
                 'rpubl:celexNummer':
                 attributes['rpubl:genomforDirektiv']}))

        has_bemyndiganden = False
        if 'rpubl:bemyndigande' in attributes:
            # dehyphenate (note that normalize_space already has changed "\n" to " "...
            attributes['rpubl:bemyndigande'] = attributes['rpubl:bemyndigande'].replace("\xad ", "")
            result = parser.parse_string(attributes['rpubl:bemyndigande'])
            bemyndiganden = [x.uri for x in result if hasattr(x, 'uri')]

            # some of these uris need to be filtered away due to
            # over-matching by parser.parse
            filtered_bemyndiganden = []
            for bem_uri in bemyndiganden:
                keep = True
                for compare in bemyndiganden:
                    if (len(compare) > len(bem_uri) and
                            compare.startswith(bem_uri)):
                        keep = False
                if keep:
                    filtered_bemyndiganden.append(bem_uri)
            attributes['rpubl:bemyndigande'] = [URIRef(x) for x in filtered_bemyndiganden]

        if 'rpubl:upphaver' in attributes:
            upphaver = []
            for upph in re.findall('([A-ZÅÄÖ-]+FS \d{4}:\d+)',
                                   util.normalize_space(attributes['rpubl:upphaver'])):
                (fs, year, ordinal) = re.split('[ :]', upph)
                upphaver.append(makeurl(
                    {'rdf:type': RPUBL.Myndighetsforeskrift,
                     'rpubl:forfattningssamling': self.lookup_resource(fs, SKOS.altLabel),
                     'rpubl:arsutgava': year,
                     'rpubl:lopnummer': ordinal}))
            attributes['rpubl:upphaver'] = [URIRef(x) for x in upphaver]

        if 'rdf:type' not in attributes:
            if ('dcterms:title' in attributes and
                "allmänna råd" in attributes['dcterms:title'] and
                    "föreskrifter" not in attributes['dcterms:title']):
                attributes['rdf:type'] = RPUBL.AllmannaRad
            else:
                attributes['rdf:type'] = RPUBL.Myndighetsforeskrift
        resource = self.attributes_to_resource(attributes)
        uri = URIRef(self.minter.space.coin_uri(resource))
        for (p, o) in list(resource.graph.predicate_objects(
                resource.identifier)):
            resource.graph.remove((resource.identifier, p, o))
            # remove those dcterms:issued triples we only used to be
            # able to mint a URI
            if p != DCTERMS.issued or o.datatype is not None:
                resource.graph.add((uri, p, o))
        return resource.graph.resource(uri)

    def infer_identifier(self, basefile):
        p = self.store.distilled_path(basefile)
        if not os.path.exists(p):
            raise ValueError("No distilled file for basefile %s at %s" % (basefile, p))

        with self.store.open_distilled(basefile) as fp:
            g = Graph().parse(data=fp.read())
        uri = self.canonical_uri(basefile)
        return str(g.value(URIRef(uri), DCTERMS.identifier))

    def postprocess_doc(self, doc):
        super(MyndFskrBase, self).postprocess_doc(doc)
        if getattr(doc.body, 'tagname', None) != "body":
            doc.body.tagname = "body"
        doc.body.uri = doc.uri

    def facets(self):
        return [Facet(RDF.type),
                Facet(DCTERMS.title),
                Facet(DCTERMS.publisher),
                Facet(DCTERMS.identifier),
                Facet(RPUBL.arsutgava,
                      indexingtype=fulltextindex.Label(),
                      use_for_toc=True)]

    def tabs(self):
        return [(self.__class__.__name__, self.dataset_uri())]


class AFS(MyndFskrBase):
    alias = "afs"
    start_url = "https://www.av.se/arbetsmiljoarbete-och-inspektioner/publikationer/foreskrifter/foreskrifter-listade-i-nummerordning/"
    landingpage = True

    basefile_regex = re.compile("^(?P<basefile>AFS \d+: ?\d+)")
    # we need a slighly more forgiving regex beause of AFS 2017:1,
    # which has the url "...afs-1-2017.pdf" ...
    document_url_regex = re.compile('.*(?P<basefile>\d+[:/_-]\d+).pdf$')

    # Note that the url for AFS 2015:6 doesn't include the basefile at
    # all. There seems to be no way of constructing a
    # document_url_regex that matches that, but not invalid PDFs (such
    # as consolidated versions). The following is too greedy.
    # document_url_regex =
    # re.compile(".*/publikationer/foreskrifter/.*\.pdf$")

    def download_single(self, basefile, url=None):
        # the basefile might be the lastest change act, while the url
        # could be a landing page for the base act. The most prominent
        # link ("Ladda ner pdf") could be to an official base act, or
        # to an unofficial consolidated version up to and including
        # the latest change act. So, yeah.
        assert not url.endswith(".pdf"), ("expected landing page for %s, got direct pdf"
                                          " link %s" % (basefile, url))
        resp = self.session.get(url)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        title = soup.find("h1").text
        # afs/2017:4 -> "AFS 2017:4"
        identifier = basefile.upper().replace("/", " ")
        # AFS 2017:4 -> 2017:4
        short_identifier = identifier.split(" ")[1] 
        # the test of wheter base act: It doesn't contain any change
        # acts.
        changeheader = soup.find(["h2", "h3"], text="Ursprungs- och ändringsföreskrifter")
        is_baseact = not(changeheader)
        if is_baseact:
            link = soup.find("a", text="Ladda ner pdf")
            pdfurl = urljoin(url, link["href"])
            # do something smart to actually download the basefile
            # from the pdfurl (saving url as orig_url). We'd like to
            # call DocumentRepository.download_single, since
            # super(...).download_single will call
            # MyndFskrBase.download_single, which does too much. This
            # is a clear sign that I don't understand OOP
            # design. Anyway, this might work.
            DocumentRepository.download_single(self, basefile, pdfurl, url)
        else:
            if not changeheader:
                self.log.error("%s: Can't find a list of change acts at %s" % (basefile, url))
                return False
            pdfs = changeheader.parent.find_all("a", href=re.compile("\.pdf$"))
            # first, get the actual basefile we're looking for (assume
            # there really is one)
            norm = util.normalize_space
            match = lambda x: identifier in norm(x.text) or short_identifier in norm(x.text)
            links = [x for x in pdfs if match(x)]
            # a (short) list of identifiers that isn't present in the
            # list of change acts, even though they should
            whitelist = ['AFS 1994:53',]
            if not links:
                if identifier not in whitelist:
                    self.log.error("Can't find PDF link to %s amongst %s" % (identifier, [x.text for x in pdfs]))
                else:
                    raise errors.DocumentRemovedError(basefile, dummyfile=self.store.downloaded_path(basefile))
                return False
            link = [x for x in pdfs if match(x)][0]
            pdfurl = urljoin(url, link["href"])
            # note: the actual downloading (call to
            # DocumentRepository.download_single) happens at the very
            # end
            
            # then, 1) find out what change act the consolidated
            # version might be updated to. FIXME: we don't DO anything
            # with this information!
            ids = [norm(x.text).split(" ")[1] for x in pdfs if re.match("AFS \d+:\d+", norm(x.text))]
            updated_to = sorted(ids, key=util.split_numalpha)[-1]

            # 2) find the url to the consolidated pdf and store that
            # as a separate basefile, using the html page as an
            # attachment
            base_basefile = re.search("AFS \d+:\d+", title).group(0).lower().replace(" ", "/")
            link = soup.find("a", text="Ladda ner pdf")
            consolidated_pdfurl = urljoin(url, link["href"])
            consolidated_basefile = "konsolidering/%s" % base_basefile
            DocumentRepository.download_single(self, consolidated_basefile, consolidated_pdfurl)
            with self.store.open_downloaded(consolidated_basefile, "w", attachment="landingpage.html") as fp:
                fp.write(resp.text)
            
            # 4) Actually download the main basefile
            return DocumentRepository.download_single(self, basefile, pdfurl, url)

    def parse_metadata_from_consolidated(self, reader, props, basefile):
        super(AFS, self).parse_metadata_from_consolidated(reader, props, basefile)
        with self.store.open_downloaded(basefile, attachment="landingpage.html") as fp:
            soup = BeautifulSoup(fp.read(), "lxml")
        changeheader = soup.find(["h2", "h3"], text="Ursprungs- och ändringsföreskrifter")
        pdfs = changeheader.parent.find_all("a", href=re.compile("\.pdf$"))
        norm = util.normalize_space
        # in some cases the leading AFS is missing
        matcher = re.compile("(?:|AFS )(\d+:\d+)").match
        fsnummer = [matcher(norm(x.text)).group(1) for x in pdfs if matcher(norm(x.text))]
        props['rpubl:konsolideringsunderlag'] = []
        for f in fsnummer:
            kons_uri = self.canonical_uri(self.sanitize_basefile(f))
            props['rpubl:konsolideringsunderlag'].append(URIRef(kons_uri))

        title = soup.title.text
        if ", föreskrifter" in title:
            title = title.split(", föreskrifter")[0].strip()
        identifier = "%s (konsoliderad tom. %s)" % (
            re.search("AFS \d+:\d+", title).group(0),
            self.consolidation_date(basefile))
        props['dcterms:identifier'] = identifier
        props['dcterms:title'] = Literal(title, lang="sv")
        props['dcterms:publisher'] = self.lookup_resource("Arbetsmiljöverket")
        return props

    @lru_cache(maxsize=None)
    def consolidation_date(self, basefile):
        reader = self.textreader_from_basefile(basefile)
        # look at the first TWO pages for consolidation info
        for page in reader.readpage(), reader.readpage():
            # All these variants exists:
            m = re.search(r"Ändringar (?:införda|gjorda|är gjorda) (?:t\.o\.m\.?|till och med) ?(?:|den )(\d+ \w+ \d+|\d+-\d+-\d+)", page)
            if m:
                return self.parse_swedish_date(m.group(1))
        else:
            self.log.warning("%s: Cannot find consolidation date" % basefile)
            return ""

    def sanitize_text(self, text, basefile):
        # 'afs/2014:39' -> 'AFS 2014:39'
        probable_id = basefile.upper().replace("/", " ")
        newtext = ""
        margin = ""
        inmargin = False
        datematch = re.compile("den \d+ \w+ \d{4}$").search
        for line in text.split("\n"):
            newline = True
            if line.endswith(probable_id) and not margin and len(
                    line) > len(probable_id):  # and possibly other sanity checks
                inmargin = True
                margin += probable_id + "\n"
                newline = line[:line.index(probable_id)]
            elif inmargin and line.endswith("Utkom från trycket"):
                margin += "Utkom från trycket\n"
                newline = line[:line.index("Utkom från trycket")]
            elif inmargin and datematch(line):
                m = datematch(line)
                margin += m.group(0) + "\n"
                newline = line[:m.start()]
            elif inmargin and line == "":
                inmargin = False
                newline = "\n" + margin + "\n"
            else:
                newline = line
            if newline:
                if newline is True:
                    newline = ""
            newtext += newline + "\n"
        return newtext


class BOLFS(MyndFskrBase):
    alias = "bolfs"
    start_url = "http://www.bolagsverket.se/om/oss/verksamhet/styr/forfattningssamling"
    download_iterlinks = False

    @decorators.downloadmax
    def download_get_basefiles(self, source):
        # FIXME: The id (given in a h3) is not linked, and the link
        # does not *reliably* contain the id: given link. Therefore,
        # we get all basefiles from the h3:s and find corresponding
        # links
        soup = BeautifulSoup(source, "lxml") # source is HTML text,
                                             # since
                                             # download_iterlinks is
                                             # False
        for h in soup.find("div", id="block-container").find_all("h3"):
            linklist = h.parent.find_next_sibling("ul")
            if linklist:
                el = linklist.find("a")
                yield self.sanitize_basefile(h.text), urljoin(self.start_url, el.get("href"))


class DIFS(MyndFskrBase):
    alias = "difs"
    start_url = "http://www.datainspektionen.se/lagar-och-regler/datainspektionens-foreskrifter/"


class DVFS(MyndFskrBase):
    alias = "dvfs"
    start_url = "http://www.domstol.se/Ladda-ner--bestall/Verksamhetsstyrning/DVFS/DVFS1/"
    downloaded_suffix = ".html"

    nextpage_regex = re.compile(">")
    nextpage_url_regex = None
    basefile_regex = re.compile("^\s*(?P<basefile>\d{4}:\d+)")
    download_formid = "aspnetForm"
    download_iterlinks = False
    download_record_last_download = True

    @decorators.downloadmax
    def download_get_basefiles(self, source):
        # Adapted version of MyndFskrBase.download_get_basefile that
        # downloads each landing page found in the regular list to
        # find the URLs for base and change acts (the regular list
        # only lists base acts)
        re_bf = re.compile("^\d{4}:\d+")
        while source:
            nextform = nexturl = None
            soup = BeautifulSoup(source, "lxml")
            for el in soup.find("div", id="readme").find_all("a"):
                elementtext = el.text.strip()
                m = re.search(self.basefile_regex, elementtext)
                # Look at the date (given as <br>[YYYY-MM-DD]
                # following the link) and only look for additional
                # basefiles if the date is newer than the last
                # recorded change for that basefile. 
                if m:
                    if not self.config.refresh and self.config.lastdownload:
                        changedatestr = el.find_next_sibling("br").next_sibling.strip()[1:-1]
                        changedate = util.strptime(changedatestr, "%Y-%m-%d")
                        if self.config.lastdownload.date() > changedate.date():
                            self.log.debug("%s: Changedate %s is older than lastdownload %s, not going any further" % (m.group(0), changedatestr, str(self.config.lastdownload.date())))
                            return # does this work in a generator?
                    link = urljoin(self.start_url, el.get("href"))
                    self.log.debug("%s: Looking at %s for additional basefiles" %
                                   (m.group("basefile"), link))
                    resp = self.session.get(link)
                    resp.raise_for_status()
                    subsoup = BeautifulSoup(resp.text, "lxml")
                    found = False
                    for sublink in subsoup.find("div", id="readme").find_all("a", text=re_bf):
                        basefile = re_bf.match(sublink.text).group(0)
                        yield self.sanitize_basefile(basefile), urljoin(link, sublink["href"])
                if (self.nextpage_regex and elementtext and
                        re.search(self.nextpage_regex, elementtext)):
                    nexturl = el.get("href")
            if nexturl:
                nextform = soup.find("form", id="aspnetForm")
            if nextform is not None and nexturl is not None:
                resp = self.download_post_form(nextform, nexturl)
            else:
                resp = None
                source = None
            if resp:
                source = resp.text

    def download_post_form(self, form, url):
        # nexturl == "javascript:__doPostBack('ctl00$MainRegion$"
        #            "MainContentRegion$LeftContentRegion$ctl01$"
        #            "epiNewsList$ctl09$PagingID15','')"
        etgt, earg = [m.group(1) for m in re.finditer("'([^']*)'", url)]
        form = lxml.html.document_fromstring(str(form)).forms[0]
        form.make_links_absolute(self.start_url, resolve_base_href=True)

        fields = dict(form.fields)

        fields['__EVENTTARGET'] = etgt
        fields['__EVENTARGUMENT'] = earg
        for k, v in fields.items():
            if v is None:
                fields[k] = ''
        # using the files argument to requests.post forces the
        # multipart/form-data encoding
        req = requests.Request(
            "POST", form.get("action"), cookies=self.session.cookies, files=fields).prepare()
        # Then we need to remove filename from req.body in an
        # unsupported manner in order not to upset the
        # sensitive server
        body = req.body
        if isinstance(body, bytes):
            body = body.decode()  # should be pure ascii
        req.body = re.sub(
            '; filename="[\w\-\/]+"', '', body).encode()
        req.headers['Content-Length'] = str(len(req.body))
        # self.log.debug("posting to event %s" % etgt)
        resp = self.session.send(req, allow_redirects=True)
        return resp

    def main_from_soup(self, soup):
        main = soup.find("div", id="readme")
        if main:
            main.find("div", "rs_skip").decompose()
            # find title of this fs and remove unneeded markup (messes
            # up the get_text call in textreader_from_basefile)
            oldtitle = main.h2
            if oldtitle is None:
                for t in main.find_all("h1"):
                    if re.match("(Domstolsverkets föreskrifter|Föreskrifter)", t.text):
                        oldtitle = t
                        break
            if oldtitle:
                newtitle = soup.new_tag(oldtitle.name)
                newtitle.string = oldtitle.get_text(" ")
                oldtitle.replace_with(newtitle)
            return main
        elif soup.find("title").text == "Sveriges Domstolar - 404":
            e = errors.DocumentRemovedError()
            e.dummyfile = self.store.parsed_path(basefile)
            raise e

    def maintext_from_soup(self, soup):
        main = self.main_from_soup(soup)
        return main.get_text("\n\n", strip=True)
        
    def textreader_from_basefile(self, basefile, force_ocr=False, attachment=None):
        infile = self.store.downloaded_path(basefile)
        soup = BeautifulSoup(util.readfile(infile), "lxml")
        text = self.maintext_from_soup(soup)
        text = self.sanitize_text(text, basefile)
        return TextReader(string=text)

    def extract_head(self, fp, basefile, force_ocr=False, attachment=None):
        return self.textreader_from_basefile(basefile)

    def parse_open(self, basefile):
        return self.store.open_downloaded(basefile)

    def parse_body(self, fp, basefile):
        main = self.main_from_soup(BeautifulSoup(fp, "lxml"))
        return Body([elements_from_soup(main)],
                    uri=None)

    def fwdtests(self):
        t = super(DVFS, self).fwdtests()
        t["dcterms:identifier"] = ['(DVFS\s\s?\d{4}[:\-]\d+)']
        return t


class EIFS(MyndFskrBase):
    alias = "eifs"
    start_url = "https://www.ei.se/sv/Publikationer/Foreskrifter/"
    basefile_regex = None
    document_url_regex = re.compile('.*(?P<basefile>EIFS_\d{4}_\d+).pdf$')

    def sanitize_basefile(self, basefile):
        basefile = basefile.replace("_", "/", 1)
        basefile = basefile.replace("_", ":", 1)
        return super(EIFS, self).sanitize_basefile(basefile)

class ELSAKFS(MyndFskrBase):
    alias = "elsakfs"  # real name is ELSÄK-FS, but avoid swedchars, uppercase and dashes
    start_url = "https://www.elsakerhetsverket.se/om-oss/lag-och-ratt/foreskrifter/"
    download_stay_on_site = True
    landingpage_basefile_regex = re.compile("^ELSÄK-FS (?P<basefile>\d{4}:\d+)\s*$")
    basefile_regex = re.compile("^ELSÄK-FS (?P<basefile>\d{4}:\d+)\s*$")
    basefile_pdf_regex = re.compile("^ELSÄK-FS (?P<basefile>\d{4}:\d+)(?P<typ>| - ändringsföreskrift| - konsoliderad version| - ursprunglig lydelse) \(pdf, \d,\d MB\)")


    @decorators.downloadmax
    @recordlastbasefile
    def download_get_basefiles(self, source):
        def linkmatcher(tag):
            return tag.name == "a" and self.basefile_pdf_regex.match(util.normalize_space(tag.get_text()))
        yielded = set()
        for (element, attribute, link, pos) in source:
            if element.tag != "a":
                continue
            elementtext = " ".join(element.itertext())
            m = self.landingpage_basefile_regex.match(elementtext)
            if m:
                # return if basefile is larger than self.config.last_basefile
                basefile = m.group("basefile")
                if self.download_stay_on_site and urlparse(self.start_url).netloc != urlparse(link).netloc:
                    continue

                self.log.debug("%s: Getting landing page %s" % (basefile, link))
                resp = self.session.get(link)
                resp.raise_for_status()
                soup = BeautifulSoup(resp.text, "lxml")
                d = soup.find("div", "maincontent")
                els = d.find_all(linkmatcher)
                if not els:
                    self.log.warning("Could not find valid PDF links on landing page %s for basefile %s" % (link, basefile))
                for el in els:
                    m = self.basefile_pdf_regex.match(util.normalize_space(el.get_text()))
                    sub_basefile = "elsakfs/" + m.group("basefile")
                    if m.group("typ") == " - konsoliderad version":
                        sub_basefile = "konsolidering/" + sub_basefile
                    if sub_basefile not in yielded:
                        self.log.debug("yielding %s, %s" % (sub_basefile,
                                                            urljoin(link, el.get("href"))))
                        yield (sub_basefile, urljoin(link, el.get("href")))
                        yielded.add(sub_basefile)
        
        
    # this repo has a mismatch between basefile prefix and the URI
    # space slug. This is easily fixed.
    def sanitize_basefile(self, basefile):
        basefile = basefile.lower().replace("elsäk-fs", "elsakfs")
        return super(ELSAKFS, self).sanitize_basefile(basefile)

    def basefile_from_uri(self, uri):
        uri = uri.replace("elsaek-fs", "elsakfs")
        return super(ELSAKFS, self).basefile_from_uri(uri)

    def fwdtests(self):
        t = super(ELSAKFS, self).fwdtests()
        # it's hard to match "...föreskriver X följande" if X contains
        # spaces ("följande" can be pretty much anything else)
        t["rpubl:beslutadAv"].insert(0, '(?:meddelar|föreskriver)\s(Sveriges geologiska undersökning)')
        return t

    def parse_metadata_from_consolidated(self, reader, props, basefile):
        super(ELSAKFS, self).parse_metadata_from_consolidated(reader, props, basefile)
        # TODO: find out dcterms:issued ("Lydelse per den 8 juni
        # 2016"), rpubl:konsolideringsunderlag ("Ändringar genom
        # ELSÄK-FS 2016:4 införda") and dcterms:title
        # ("Elsäkerhetsverkets föreskrifter om elektromagnetisk
        # kompatibilitet")
        
        props["dcterms:publisher"] = self.lookup_resource("Elsäkerhetsverket")
        return props


class FFFS(MyndFskrBase):
    alias = "fffs"
    start_url = "https://www.fi.se/sv/vara-register/forteckning-fffs/"
    landingpage = True
    landingpage_url_regex = re.compile(".*/sok-fffs/\d{4}/((?P<baseact>\d{5,}/)|)(?P<basefile>\d{5,})/$")
    document_url_regex = re.compile(".*/contentassets/.*\.pdf$")
    def forfattningssamlingar(self):
        return ["fffs", "bffs"]

    def sanitize_basefile(self, basefile):
        # basefiles as captured by the document_url_regex is missing
        # the colon separator. Re-introduce that.
        if basefile.isdigit and len(basefile) > 4:
            basefile = "%s:%s" % (basefile[:4], basefile[4:])
        return super(FFFS, self).sanitize_basefile(basefile)

    def fwdtests(self):
        t = super(FFFS, self).fwdtests()
        # This matches old BFFS 1991:15 (basefile fffs/1991:15)
        t["dcterms:title"].append('^(Upphävande av .*?)\n\n')
        return t

        
class FFS(MyndFskrBase):
    alias = "ffs"
    start_url = "http://www.forsvarsmakten.se/sv/om-myndigheten/dokument/lagrum"
    document_url_regex = re.compile(".*/lagrum/gallande-ffs.*/ffs.*(?P<basefile>\d{4}[\.:/_-]\d{1,3})[^/]*.pdf$")

   
    
class KFMFS(MyndFskrBase):
    alias = "kfmfs"
    start_url = "http://www.kronofogden.se/Foreskrifter.html"
    download_iterlinks = False
    # note that the above URL contains one (1) link to an old RSFS,
    # which has been subsequently expired by SKVFS 2017:12. Don't know
    # why they're still publishing it...

    @decorators.downloadmax
    def download_get_basefiles(self, source):
        soup = BeautifulSoup(source, "lxml")
        for ns in soup.find("h2", text="Föreskrifter").parent.find_all(
                text=re.compile("KFMFS")):
            m = self.basefile_regex.search(ns.strip())
            basefile = m.group("basefile")
            link = ns.parent.find("a", href=re.compile(".*\.pdf"))
            yield self.sanitize_basefile(basefile), urljoin(self.start_url, link["href"])
    

class KOVFS(MyndFskrBase):
    alias = "kovfs"
    download_iterlinks = False
    # start_url = "http://publikationer.konsumentverket.se/sv/sok/kovfs"

    # since Konsumentverket uses a inaccessible Angular webshop from
    # hell for publishing KOVFS, it seems that the simplest way of
    # getting a list of basefile/pdf-link pairs is to craft a special
    # JSON-RPC call to the backend endpoint of the company hosting the
    # webshop, and then call another endpoint with a list of internal
    # document ids. Seriously, fuck this. Don't break the web.
    start_url = "https://shop.textalk.se/backend/jsonrpc/v1/?language=sv&webshop=55743"

    def download_get_first_page(self):
        payload = '{"id":10,"jsonrpc":"2.0","method":"Article.list","params":[{"uid":true,"name":"sv","articleNumber":true,"introductionText":true,"price":true,"url":"sv","images":true,"unit":true,"articlegroup":true,"news":true,"choices":true,"isBuyable":true,"presentationOnly":true,"choiceSchema":true},{"filters":{"search":{"term":"kovfs*"}},"offset":0,"limit":48,"sort":"name","descending":false}]}'
        resp = self.session.post(self.start_url, data=payload)
        # to avoid auto-detecting the charset, which yields a lot of
        # debug log entries we can do without, as we're sure of the
        # encoding
        resp.encoding = "utf-8"
        return resp

    @decorators.downloadmax
    def download_get_basefiles(self, source):
        # source is resp.text but we'd rather have resp.json(). But
        # we'll parse it ourselves
        resp = json.loads(source)
        docs = {}
        for result in resp['result']:
            # KOVFS YYYY:NN = 13 chars
            basefile = result['name']['sv'][:13].strip()
            if self.basefile_regex.search(basefile):
                uid = str(result['uid'])
                docs[uid] = basefile
        articleurl = "http://konsumentverket.shoptools.textalk.se/ro-api/55743/editions/preselected_for_articles.json?article_ids=[%s]" % ",".join(docs.keys())
        resp = self.session.get(articleurl)
        res = resp.json()
        for uid in res.keys():
            yield(self.sanitize_basefile(docs[uid]), res[uid]['preselected']['url'])


class KVFS(MyndFskrBase):
    alias = "kvfs"
    start_url = ("https://www.kriminalvarden.se/om-kriminalvarden/"
                 "publikationer/regelverk/search")
    # (finns även konsoliderade på http://www.kriminalvarden.se/
    #  om-kriminalvarden/styrning-och-regelverk/lagar-forordningar-och-
    #  foreskrifter)

    download_iterlinks = False
    basefile_regex = re.compile("(?P<basefile>KVV?FS \d{4}:\d+)")

    def download_get_first_page(self, paging=1):
        self.log.debug("POSTing to search, paging=%s" % paging)
        params = {'publicationKeyword':'',
                  'sortOrder': 'publish',
                  'paging': str(paging)}
        headers = {'accept': 'application/json, text/javascript, */*; q=0.01'}
        resp = self.session.post(self.start_url, data=params, headers=headers)
        return resp
    

    def forfattningssamlingar(self):
        return ["kvfs", "kvvfs"]
    
    @decorators.downloadmax
    @recordlastbasefile
    def download_get_basefiles(self, source):
        lasthref = None
        done = False
        paging = 1
        while not done:
            partial = json.loads(source)['PartialViewHtml']
            soup = BeautifulSoup(partial, "lxml") # source is HTML text,
                                                 # since
                                                 # download_iterlinks is
                                                 # False
            for h in soup.find_all("h3"):
                m = self.basefile_regex.match(h.text.strip())
                if not m:
                    continue
                el = h.parent.parent.find("a")
                if el:
                    yield self.sanitize_basefile(m.group("basefile")), urljoin(self.start_url,el.get("href"))
            nextlink = soup.find("ul", "pagination").find_all("a")[-1] # last link is Next
            if nextlink and nextlink["href"] != lasthref:
                paging += 1
                resp = self.download_get_first_page(paging)
                resp.raise_for_status()
                source = resp.text
                lasthref = nextlink["href"]
            else:
                done = True

    def forfattningssamlingar(self):
        return ["kvfs", "kvvfs"]

class LMFS(MyndFskrBase):
    alias = "lmfs"
    start_url = "http://www.lantmateriet.se/sv/Om-Lantmateriet/Rattsinformation/Foreskrifter/"
    basefile_regex = re.compile('(?P<basefile>LMV?FS \d{4}:\d{1,3})')

    def forfattningssamlingar(self):
        return ["lmfs", "lmvfs"]

    def fwdtests(self):
        t = super(LMFS, self).fwdtests()
        # it's hard to match "...föreskriver X följande" if X contains
        # spaces ("följande" can be pretty much anything else)
        t["rpubl:beslutadAv"].insert(0, '(?:meddelar|föreskriver)\s(Statens\s+lantmäteriverk)')
        return t


class LVFS(MyndFskrBase):
    alias = "lvfs"
    start_url = "http://www.lakemedelsverket.se/overgripande/Lagar--regler/Lakemedelsverkets-foreskrifter---LVFS/"
    basefile_regex = None # urls are consistent enough and contain FS
                          # information, which link text lacks
    document_url_regex = re.compile(".*/(?P<basefile>[LVHSF\-]+FS_ ?\d{4}[_\-]\d+)\.pdf$")

    def sanitize_basefile(self, basefile):
        # fix accidental misspellings found in 2015:35 and 2017:31
        basefile = basefile.replace("HSLFS", "HSLF").replace("HLFS", "HSLF")
        return super(LVFS, self).sanitize_basefile(basefile)
    
    def forfattningssamlingar(self):
        return ["hslffs", "lvfs"]

    def fwdtests(self):
        t = super(LVFS, self).fwdtests()
        # extra lax regex needed for LVFS 1992:4
        t["rpubl:beslutsdatum"].append("^den (\d+ \w+ \d{4})$")
        return t

class MIGRFS(MyndFskrBase):
    alias = "migrfs"
    start_url = "https://www.migrationsverket.se/Om-Migrationsverket/Vart-uppdrag/Styrning-och-uppfoljning/Foreskrifter.html"
    basefile_regex = re.compile("(?P<basefile>(MIGR|SIV)FS \d+[:/]\d+)$")

    def sanitize_basefile(self, basefile):
        # older MIGRFS uses non-standard identifiers like MIGRFS
        # 04/2017. We normalize this to migrfs/2017-4 because who do
        # they think they are?
        if re.search("\d{1,2}/\d{4}$", basefile):
            fs, ordinal, year = re.split("[ /]", basefile)
            basefile = "%s %s:%s" % (fs, year, int(ordinal))
        return super(MIGRFS, self).sanitize_basefile(basefile)
    
    def forfattningssamlingar(self):
        return ["migrfs", "sivfs"]

    def fwdtests(self):
        t = super(MIGRFS, self).fwdtests()
        # it's hard to match "...föreskriver X följande" if X contains
        # spaces ("följande" can be pretty much anything else)
        t["rpubl:beslutadAv"].insert(0, '(?:meddelar|föreskriver)\s(Statens\s+invandrarverk)')
        return t

class MPRTFS(MyndFskrBase):
    alias = "mprtfs"
    start_url = "http://www.mprt.se/sv/blanketter--publikationer/foreskrifter/"
    basefile_regex = re.compile("^(?P<basefile>(MPRTFS|MRTVFS|RTVFS) \d+:\d+)$")
    document_url_regex = None
    def forfattningssamlingar(self):
        return ["mprtfs", "mrtvfs", "rtvfs"]


class MSBFS(MyndFskrBase):
    alias = "msbfs"
    start_url = "https://www.msb.se/sv/regler/gallande-regler/"
    # FIXME: start_url now requres a POST but with a bunch of
    # viewstate crap to yield a full list
    download_iterlinks = False # download_get_basefiles will be called
                               # with start_url text, not result from
                               # .iterlinks()

    basefile_regex = re.compile("^(?P<basefile>(MSBFS|SRVFS|KBMFS|SÄIFS) \d+:\d+)")

    def forfattningssamlingar(self):
        return ["msbfs", "srvfs", "kbmfs", "säifs"]

    # this repo has basefiles eg "säifs/2000:6" but the uri will be on
    # the form "../saeifs/2000:6" so we do a special-case transform
    def basefile_from_uri(self, uri):
        uri = uri.replace("/saeifs/", "/säifs/")
        return super(MyndFskrBase, self).basefile_from_uri(uri)

    @recordlastbasefile
    def download_get_basefiles(self, source):
        selectedpage = 1
        while source:
            soup = BeautifulSoup(source, "lxml")
            for link_el in soup.find_all("a", "law"):
                m = self.basefile_regex.match(link_el.string)
                if m:
                    link = urljoin(self.start_url, link_el.get("href"))
                    basefile = self.sanitize_basefile(m.group("basefile"))
                    yield basefile, link
                else:
                    self.log.warning("Link titled %s ought to be a basefile, but isn't" % link_el.string)
            if soup.find("a", "pagination-next") and not soup.find("li", "pagination-next disabled"):
                selectedpage += 1
                self.log.debug("Downloading %s, selectedpage %s" % (self.start_url, selectedpage))
                resp = self.session.post(self.start_url, {"searchQuery": "",
                                                          "sortOrder": "DescendingYear",
                                                          "amountToShow": "10",
                                                          "selectedpage": selectedpage})
                source = resp.text
            else:
                source = None
                

    def fwdtests(self):
        t = super(MSBFS, self).fwdtests()
        # cf. NFS.fwdtests()
        t["rpubl:beslutadAv"].insert(0, '(?:meddelar|föreskriver) (Statens räddningsverk)')
        return t
            

class MYHFS(MyndFskrBase):
    #  (id vs länk)
    alias = "myhfs"
    start_url = "https://www.myh.se/Lagar-regler-och-tillsyn/Foreskrifter/"
    download_iterlinks = False

    @decorators.downloadmax
    def download_get_basefiles(self, source):
        soup = BeautifulSoup(source, "lxml")
        for basefile in soup.find("div", "article-text").find_all("strong", text=re.compile("\d+:\d+")):
            link = basefile.find_parent("td").find_next_sibling("td").a
            yield self.sanitize_basefile(basefile.text.strip()), urljoin(self.start_url, link["href"])
        

class NFS(MyndFskrBase):
    alias = "nfs"
    start_url = "http://www.naturvardsverket.se/nfs"
    basefile_regex = re.compile("^(?P<basefile>S?NFS \d+:\d+)$")
    document_url_regex = None
    nextpage_regex = "Nästa"
    storage_policy = "dir"

    def sanitize_basefile(self, basefile):
        basefile = basefile.replace(" ", "/")
        return super(NFS, self).sanitize_basefile(basefile)

    def forfattningssamlingar(self):
        return ["nfs", "snfs"]

    def download_single(self, basefile, url):
        if url.endswith(".pdf"):
            return super(NFS, self).download_single(basefile, url)

        # NB: the basefile we got might be a later change act. first
        # order of business is to identify the base act basefile
        resp = self.session.get(url)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        # nfs/2017:4 -> "NFS 2017:4"
        identifier = basefile.upper().replace("/", " ")
        # SNFS 1987:4 -> 1987:4
        short_identifier = identifier.split(" ")[1] 

        base_basefile = None
        basehead = soup.find("h3", text=re.compile("Grundföreskrift$"))
        if basehead:
            m = re.match("(S?NFS)\s+(\d+:\d+)", util.normalize_space(basehead.text))
            base_basefile = m.group(1).lower() + "/" + m.group(2)
        # find all pdf links, identify consolidated version if present
        # [1:] in order to skip header
        rows = soup.find("table", "regulations-table").find_all("tr")[1:]
        links = []
        for row in rows:
            title = util.normalize_space(row.find("h3").text)
            link = row.find("a", href=re.compile("\.pdf$", re.I))
            if not link:
                continue
            if "Konsoliderad" in title or "-k" in link.get("href"):
                # in order to download this, we need to know the
                # base_basefile. Normally, that row will have
                # "Grundförfattning" somewhere in the title, but not
                # always...
                if not base_basefile:
                    # we could wither get the row with the lowest
                    # fsnummer, or the last row. Lets try with the
                    # last one
                    m = re.match("(S?NFS)\s+(\d+:\d+)", util.normalize_space(rows[-1].h3.text))
                    if m:
                        base_basefile = m.group(1).lower() + "/" + m.group(2)
                    else:
                        assert base_basefile, "%s: Found consolidated version, but no base act" % (basefile)
                consolidated_pdfurl = urljoin(url, link["href"])
                consolidated_basefile = "konsolidering/%s" % base_basefile
                ret = DocumentRepository.download_single(self, consolidated_basefile, consolidated_pdfurl)
                # save the landing page as it contains information
                # about the consolidation date
                with self.store.open_downloaded(consolidated_basefile, "w", attachment="landingpage.html") as fp:
                    fp.write(resp.text)
                return ret
            elif identifier in title:
                pdfurl = urljoin(url, link["href"])
                # we assume that we encounter any consolidated
                # versions before this one, so once we download it
                # we're done!
                return DocumentRepository.download_single(self, basefile, pdfurl, url)
        else:
            self.log.error("%s: Couldn't find appropriate PDF version at %s" % (basefile, url))

    def parse_metadata_from_consolidated(self, reader, props, basefile):
        # we need identifier, title and publisher (which may be
        # Naturvårdsverket (NFS) or Statens naturvårdsverk (SNFS). And
        # also all konsolideringsunderlag

        super(NFS, self).parse_metadata_from_consolidated(reader, props, basefile)
        with self.store.open_downloaded(basefile, attachment="landingpage.html") as fp:
            soup = BeautifulSoup(fp.read(), "lxml")

        # [2:] == skip header and first real row (that only contains
        # the consolidated version
        matcher = re.compile("(S?NFS \d+:\d+)").match
        norm = util.normalize_space

        props['rpubl:konsolideringsunderlag'] = []
        start = False
        rows = soup.find("table", "regulations-table").find_all("tr")
        for row in rows[self._consolidation_row_index(rows)+1:]:
            title = norm(row.h3.text)
            fsnummer = matcher(title).group(1)
            konsolideringsunderlag = self.canonical_uri(self.sanitize_basefile(fsnummer))
            props['rpubl:konsolideringsunderlag'].append(URIRef(konsolideringsunderlag))
        title = soup.h1.text
        segments = basefile.split("/")
        identifier = "%s %s (konsoliderad tom. %s)" % (segments[1].upper(), segments[2], self.consolidation_date(basefile))
        publisher = "Statens naturvårdsverk" if segments[1] == "snfs" else "Naturvårdsverket"
        props["dcterms:identifier"] = identifier
        props["dcterms:title"] = Literal(title, lang="sv")
        props["dcterms:publisher"] = self.lookup_resource(publisher)
        return props

    def _consolidation_row_index(self, rows):
        for idx, row in enumerate(rows):
            title = row.h3
            if not title:
                continue
            title = util.normalize_space(row.h3.text)
            if "Konsoliderad" in title:
                return idx
        return None
        
    @lru_cache(maxsize=None)
    def consolidation_date(self, basefile):
        # try to find consolidation date on stored landingpage
        with self.store.open_downloaded(basefile, attachment="landingpage.html") as fp:
            soup = BeautifulSoup(fp.read(), "lxml")
        rows = soup.find("table", "regulations-table").find_all("tr")
        rowidx = self._consolidation_row_index(rows)
        if rowidx:
            tr_text = rows[rowidx].text
            m = re.search('\d{4}-\d{2}-\d{2}', tr_text)
            if m:
                return datetime.datetime.strptime(m.group(0), '%Y-%m-%d').date()
        self.log.warning("%s: Could not find consolidation date" % basefile)
        return super(NFS, self).consolidation_date(basefile)

    def fwdtests(self):
        t = super(NFS, self).fwdtests()
        # it's hard to match "...föreskriver X följande" if X contains spaces ("följande" can be pretty much anything else)
        t["rpubl:beslutadAv"].insert(0, '(?:meddelar|föreskriver)\s([Ss]tatens\s*naturvårdsverk)')
        return t

    def sanitize_text(self, text, basefile):
        # rudimentary dehyphenation for a special case (snfs/1994:2)
        return text.replace("Statens na—\n\nturvårdsverk", "Statens naturvårdsverk")


class RAFS(MyndFskrBase):
    #  (efter POST)
    alias = "rafs"
    start_url = "https://riksarkivet.se/rafs"
    download_iterlinks = False
    landingpage = True

    def download_get_first_page(self):
        resp = self.session.get(self.start_url)
        tree = lxml.html.document_fromstring(resp.text)
        tree.make_links_absolute(self.start_url, resolve_base_href=True)
        form = tree.forms[1]
        assert form.action == self.start_url
        fields = dict(form.fields)

        formid = 'ctl00$cphMasterFirstRow$ctl02$InsertFieldWithControlsOnInit1$SearchRafsForm_ascx1$'
        fields['__EVENTTARGET'] = formid + 'lnkVisaAllaGiltiga'
        fields['__EVENTARGUMENT'] = ''
        for f in ('btAdvancedSearch', 'btSimpleSearch', 'chkSokUpphavda'):
            del fields[formid + f]
        for f in ('tbSearch', 'tbRafsnr', 'tbRubrik', 'tbBemyndigande', 'tbGrundforfattning', 'tbFulltext'):
            fields[formid + f] = ''
        resp = self.session.post(self.start_url, data=fields)
        assert 'Antal träffar:' in resp.text, "ASP.net event lnkVisaAllaGiltiga was not properly called"
        return resp

    @decorators.downloadmax
    def download_get_basefiles(self, source):
        soup = BeautifulSoup(source, "lxml")
        for item in soup.find_all("div", "dataitem"):
            link = urljoin(self.start_url, item.a["href"])
            basefile = item.find("dt", text="Nummer:").find_next_sibling("dd").text
            yield self.sanitize_basefile(basefile), link
            
    
class RGKFS(MyndFskrBase):
    alias = "rgkfs"
    start_url = "https://www.riksgalden.se/sv/omriksgalden/Pressrum/publicerat/Foreskrifter/"
    download_iterlinks = False

    @decorators.downloadmax
    def download_get_basefiles(self, source):
        soup = BeautifulSoup(source, "lxml")
        for th in soup.find_all("th", text="RGKFS"):
            for item in th.find_parent("table").find_all("td", text=re.compile("^\d{4}:\d+$")):
                link = item.find_next_sibling("td").a
                if link and link["href"].endswith(".pdf"):
                    yield self.sanitize_basefile(item.text.strip()), urljoin(self.start_url, link["href"])


# This is newly renamed from RNFS
class RIFS(MyndFskrBase):
    alias = "rifs"
    start_url = "https://www.revisorsinspektionen.se/regelverk/samtliga-foreskrifter/"
    basefile_regex = re.compile('(?P<basefile>(RIFS|RNFS) \d{4}[:/_-]\d{1,3})$')
    document_url_regex = None

    def forfattningssamlingar(self):
        return ["rifs", "rnfs"]


class SIFS(MyndFskrBase):
    alias = "sifs"
    start_url = "https://www.spelinspektionen.se/foreskrifter-och-lagar/nya-foreskrifter/"
    basefile_regex = re.compile('(?P<basefile>[SL]IFS \d{4}:\d{1,3})')

    def forfattningssamlingar(self):
        return ["sifs", "lifs"]

class SJVFS(MyndFskrBase):
    alias = "sjvfs"
    start_url = "http://www.jordbruksverket.se/forfattningar/forfattningssamling.4.5aec661121e2613852800012537.html"
    download_iterlinks = False

    def forfattningssamlingar(self):
        return ["sjvfs", "dfs", "lvfs"]

    @decorators.downloadmax
    def download_get_basefiles(self, source):
        soup = BeautifulSoup(source, "lxml")
        main = soup.find_all("ul", "consid-submenu")
        assert len(main) == 1
        extra = []
        for a in list(main[0].find_all("a")):
            # only fetch subsections that start with a year, not
            # "Allmänna råd"/"Notiser"/"Meddelanden"
            label = a.text.split()[0]
            if not label.isdigit():
                continue
            # if lastdownload was 2015-02-24, dont download 2014
            # and earlier
            if (not self.config.refresh and
                    'lastdownload' in self.config and self.config.lastdownload and
                    self.config.lastdownload.year > int(label)):
                continue
            url = urljoin(self.start_url, a['href'])
            self.log.debug("Fetching index page for %s" % (a.text))
            subsoup = BeautifulSoup(self.session.get(url).text, "lxml")
            submain = subsoup.find("div", "pagecontent")
            for a in submain.find_all("a", href=re.compile(".pdf$", re.I)):
                if re.search('\d{4}:\d+', a.text):
                    m = re.search('(\w+FS|) ?(\d{4}:\d+)', a.text)
                    fs = m.group(1).lower()
                    fsnr = m.group(2)
                    if not fs:
                        fs = "sjvfs"
                    basefile = "%s/%s" % (fs, fsnr)
                    suburl = unquote(urljoin(url, a['href']))
                    yield(basefile, suburl)


class SKVFS(MyndFskrBase):
    alias = "skvfs"
    source_encoding = "utf-8"
    storage_policy = "dir"
    downloaded_suffix = ".html"
    download_record_last_download = True
    start_url = "https://www4.skatteverket.se/rattsligvagledning/115.html"
    # also consolidated versions
    # http://www.skatteverket.se/rattsinformation/lagrummet/foreskrifterkonsoliderade/aldrear.4.19b9f599116a9e8ef3680004242.html

    def __init__(self, config=None, **kwargs):
        super(SKVFS, self).__init__(config, **kwargs)
        self.store.doctypes = OrderedDict([
            (".html", b'<!DO'),
            (".pdf", b'%PDF')])
            

    def forfattningssamlingar(self):
        return ["skvfs", "rsfs"]

    # URL's are highly unpredictable. We must find the URL for every
    # resource we want to download, we cannot transform the resource
    # id into a URL
    def download_get_basefiles(self, source):
        startyear = str(
            self.config.lastdownload.year) if 'lastdownload' in self.config and not self.config.refresh else "0"

        years = set()
        for (element, attribute, link, pos) in source:
            # the "/rattsligvagledning/edition/" is to avoid false
            # positives in a hidden mobile menu
            if not attribute == "href" or not element.text or not re.match(
                    '\d{4}', element.text) or "/rattsligvagledning/edition/" in element.get("href"):
                continue
            year = element.text
            if year >= startyear and year not in years:   # string comparison is ok in this case
                years.add(year)
                self.log.debug("SKVFS: Downloading year %s from %s" % (year, link))
                resp = self.session.get(link)
                resp.raise_for_status()
                soup = BeautifulSoup(resp.text, "lxml")
                for basefile_el in soup.find_all("td", text=re.compile("^\w+FS \d+:\d+")):
                    relurl = basefile_el.find_next_sibling("td").a["href"]
                    basefile = self.sanitize_basefile(basefile_el.get_text().replace(" ", "/"))
                    yield basefile, urljoin(link, relurl)

    def download_single(self, basefile, url):
        # The HTML version is the one we always can count on being
        # present. The PDF version exists for acts 2007 or
        # later. Treat the HTML version as the main version and the
        # eventual PDF as an attachment
        # this also updates the docentry
        html_downloaded = super(SKVFS, self).download_single(basefile, url)
        # try to find link to a PDF in what was just downloaded
        soup = BeautifulSoup(util.readfile(self.store.downloaded_path(basefile)), "lxml")
        pdffilename = self.store.downloaded_path(basefile,
                                                 attachment="index.pdf")
        if (self.config.refresh or not(os.path.exists(pdffilename))):
            pdflinkel = soup.find(href=re.compile('\.pdf$'))
            if pdflinkel:
                pdflink = urljoin(url, pdflinkel.get("href"))
                self.log.debug("%s: Found PDF at %s" % (basefile, pdflink))
                pdf_downloaded = self.download_if_needed(
                    pdflink,
                    basefile,
                    filename=pdffilename)
                return html_downloaded and pdf_downloaded
            else:
                return False
        else:
            return html_downloaded

    # adapted from DVFS
    def textreader_from_basefile(self, basefile, force_ocr=False, attachment=None):
        outfile = self.store.path(basefile, 'intermediate', '.txt')
        # prefer the PDF attachment to the html page
        infile = self.store.downloaded_path(basefile, attachment="index.pdf")
        if os.path.exists(infile):
            tmpfile = self.store.intermediate_path(basefile, attachment="index.pdf")
            return self.textreader_from_basefile_pdftotext(infile, tmpfile, outfile, basefile)
        else:
            infile = self.store.downloaded_path(basefile)
            soup = BeautifulSoup(util.readfile(infile), "lxml")
            return TextReader(string=self.maintext_from_soup(soup))

    def main_from_soup(self, soup):
        h = soup.find("h1", id="pageheader")
        body = soup.find("div", "body")
        if body:
            update = body.find("div", "update")
            if update:
                # collapse this div into a single plaintext string
                # (removing links etc) so that SKVFS identifiers
                # refered to doesn't get misidentified as the main
                # identifier for the document
                new_tag = soup.new_tag("div", **{'class': 'update'})
                new_tag.string = update.get_text()
                update.replace_with(new_tag)
            main = soup.new_tag("div", role="main")
            main.append(h)
            main.append(body)
            return main
        else:
            raise errors.ParseError("Didn't find a text body element")

    def maintext_from_soup(self, soup):
        main = self.main_from_soup(soup)
        return main.get_text("\n\n", strip=True)

    def parse_body(self, fp, basefile):
        if os.path.exists(self.store.downloaded_path(basefile, attachment="index.pdf")):
            return super(SKVFS, self).parse_body(fp, basefile)
        else:
            main = self.main_from_soup(BeautifulSoup(fp, "lxml"))
            return Body([elements_from_soup(main)],
                        uri=None)

    def parse_open(self, basefile, version=None):
        if os.path.exists(self.store.downloaded_path(basefile, version, attachment="index.pdf")):
            return super(SKVFS, self).parse_open(basefile, attachment="index.pdf", version=version)
        else:
            return self.store.open_downloaded(basefile, version=version)

    def extract_head(self, fp, basefile, force_ocr=False, attachment=None):
        if os.path.exists(self.store.downloaded_path(basefile, attachment="index.pdf")):
            return super(SKVFS, self).extract_head(fp, basefile, force_ocr,"index.pdf")
        else:
            # we only have HTML. Lets assume our implementation of
            # textreader_from_basefile can handle this
            return self.textreader_from_basefile(basefile)

class SOSFS(MyndFskrBase):
    # NOTE: Now that Socialstyrelsen publishes in HSLF-FS this is
    # kinda misnamed, but other docrepos handle other agencies parts
    # of HSLF-FS, so we'll keep it
    alias = "sosfs"
    start_url = "http://www.socialstyrelsen.se/sosfs"
    storage_policy = "dir"  # must be able to handle attachments
    download_iterlinks = False
    downloaded_suffixes = [".pdf", ".html"]

    def forfattningssamlingar(self):
        return ["hslffs", "sosfs"]

    def _basefile_from_text(self, linktext):
        if linktext:
            # normalize any embedded nonbreakable spaces and similar
            # crap
            linktext = util.normalize_space(linktext)
            # if fs is missing, we should prepend either SOSFS or
            # HSLF-FS to it, depending on year (< 2015 -> SOSFS, >
            # 2015 -> HSLFS, if == 2015, raise hands and scream)
            m = re.search("(SOSFS\s+|HSLF-FS\s+|)(\d+):(\d+)", linktext)
            if m:
                fs, year, no = m.groups()
                if not fs:
                    if int(year) < 2015:
                        fs = "SOSFS "
                    elif int(year) > 2015:
                        fs = "HSLF-FS "
                    else:
                        raise ValueError("Can't guess fs from %s" % m.group(0))
                return self.sanitize_basefile("%s%s:%s" % (fs, year, no))

    @decorators.downloadmax
    def download_get_basefiles(self, source):
        soup = BeautifulSoup(source, "lxml")
        for td in soup.find_all("td", "col3"):
            txt = td.get_text().strip()
            basefile = self._basefile_from_text(txt)
            if basefile is None:
                continue
            link_el = td.find_previous_sibling("td").a
            link = urljoin(self.start_url, link_el.get("href"))
            if link.startswith("javascript:"):
                continue
            if txt.startswith("Konsoliderad"):
                basefile = "konsolidering/%s" % basefile
            # FIXME: This yields a single basefile that's something
            # like "hslffs/hslf/fs 2017:27" (note the embedded nbsp --
            # basefile_from_text should handle this probably
            yield basefile, link

    def download_single(self, basefile, url, orig_url=None):
        resp = self.session.get(url)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        if basefile.startswith("konsolidering"):
            # that HTML page is the best available representation of
            # the consolidated version, and we already have it, so we
            # could save it, but if we call
            # DocumentRepository.download_single(self, basefile, link,
            # url), our documententry JSON file will be updated.
            # and since we're here already, download all PDF
            # base/change acts we can find (some might not be linked
            # from the front page)
            with self.store.open_downloaded(basefile, "wb", attachment="index.html") as fp:
                fp.write(resp.content)            
            DocumentRepository.download_single(self, basefile, url, orig_url)
            linkhead = soup.find(text=re.compile(
                "(Ladda ner eller beställ|Beställ eller ladda ner)"))
            if linkhead:
                for link_el in linkhead.find_parent("div").find_all("a"):
                    if '/publikationer' in link_el.get("href"):
                        link = urljoin(url, link_el["href"])
                        subbasefile = self._basefile_from_text(link_el.text)
                        if (subbasefile and
                            (self.config.refresh or
                             not os.path.exists(self.store.downloaded_path(subbasefile)))):
                            self.download_single(subbasefile, link)
            else:
                self.log.warning("%s: Can't find links to base/change"
                                 " acts" % basefile)
        else:
            # the url will be to a HTML landing page. We extract the link
            # to the actual PDF file and then call default impl of
            # download_single in order to update documententry.
            link_el = soup.find("a", text=re.compile("^\s*Ladda ner\s*$"))
            if link_el:
                link = urljoin(url, link_el.get("href"))
                return DocumentRepository.download_single(self, basefile, link, url)
            else:
                self.log.warning("%s: No link to PDF file found at %s" % (basefile, url))
                return False

    def sanitize_text(self, text, basefile):
        # sosfs 1996:21 is so badly scanned that tesseract fails to
        # find the only needed property (the text "Ansvarig utgivare")
        # on the proper first page
        if basefile == "sosfs/1996:21":
            text = text.replace("Ansvarigutgiyare", "Ansvarig utgivare")
        return text

    def parse_metadata_from_consolidated(self, reader, props, basefile):
        super(SOSFS, self).parse_metadata_from_consolidated(reader, props, basefile)
        with self.store.open_downloaded(basefile, attachment="index.html") as fp:
            soup = BeautifulSoup(fp.read(), "lxml")
        props['rpubl:konsolideringsunderlag'] = []
        for fsnummer in self.consolidation_basis(soup):
            konsolideringsunderlag = self.canonical_uri(self.sanitize_basefile(fsnummer))
            props['rpubl:konsolideringsunderlag'].append(URIRef(konsolideringsunderlag))
        
        title = util.normalize_space(soup.title.text)
        if title.startswith("Senaste version av "):
            title = title.replace("Senaste version av ", "")
        identifier = "%s (konsoliderad tom. %s)" % (
            re.search("(SOSFS|HSLF-FS) \d+:\d+", title).group(0),
            self.consolidation_date(basefile))
        props['dcterms:identifier'] = identifier
        props['dcterms:title'] = Literal(title, lang="sv")
        props['dcterms:publisher'] = self.lookup_resource("Socialstyrelsen")
        return props

    @lru_cache(maxsize=None)
    def consolidation_date(self, basefile):
        with self.store.open_downloaded(basefile, attachment="index.html") as fp:
            soup = BeautifulSoup(fp.read(), "lxml")
        changeleader = soup.find("strong", text=re.compile("^Ändrad"))
        if changeleader:
            # because of incosistent HTML, we'll have to include the
            # entire paragraph this <strong> tag occurs in, but not
            # anything after the first <br>, if present
            afterbr = False
            for n in list(changeleader.parent.children):
                if getattr(n, 'name', None) == "br":
                    afterbr = True
                if afterbr:
                    n.extract()
            change = util.normalize_space(changeleader.parent.text)
        else:
            changeleader = soup.find("p", text=re.compile("^Ändrad: t.o.m."))
            if changeleader:
                change = util.normalize_space(changeleader.text)
            else:
                # at this point we'll need to locate the "Ladda ner
                # eller beställ" box and find the most recent change
                # act, and assume that the consolidated version is
                # consolidated up to and including that
                change = sorted(self.consolidation_basis(soup), key=util.split_numalpha)[-1]
        assert len(re.findall('(\d+:\d+)', change)) == 1, "Didn't find exactly one change (fsnummer) in '%s'" % change
        return re.search("(SOSFS |HSLF-FS |)(\d+:\d+)", change).group(2)

    def consolidation_basis(self, soup):
        res = []
        linkhead = soup.find(text=re.compile(
            "(Ladda ne[rd] (och|eller) beställ|Beställ eller ladda ne[rd])"))
        for link_el in linkhead.find_parent("div").find_all("a"):
            if '/publikationer' not in link_el.get("href"):
                continue
            fsnummer = self._basefile_from_text(link_el.text)
            if fsnummer:
                res.append(fsnummer)
        return res

    def maintext_from_soup(self, soup):
        main = soup.find("div", id="socextPageBody").find("div", "ms-rtestate-field")
        assert main
        return str(main)

    def parse_open(self, basefile):
        if basefile.startswith("konsolidering"):
            return self.store.open_downloaded(basefile, attachment="index.html")
        else:
            return super(SOSFS,self).parse_open(basefile)

    def extract_head(self, fp, basefile, force_ocr=False, attachment=None):
        if basefile.startswith("konsolidering"):
            # we only have HTML
            return self.textreader_from_basefile(basefile)
        else:
            # we have PDF
            return super(SOSFS, self).extract_head(fp, basefile, force_ocr, attachment)

    def textreader_from_basefile(self, basefile, force_ocr=False, attachment=None):
        if basefile.startswith("konsolidering/"):
            return None   # the textreader won't be used for extracting metadata anyway
        else:
            return super(SOSFS, self).textreader_from_basefile(basefile, force_ocr, attachment)


    def extract_head(self, fp, basefile, force_ocr=False, attachment=None):
        if basefile.startswith("konsolidering/"):
            # konsoliderade files are only available as HTML, not PDF,
            # and the base extract_head expects to run pdftotext on a
            # real PDF file. Let's assume we have overridden
            # textreader_from_basefile to handle this.
            return self.textreader_from_basefile(basefile)
        else:
            return super(SOSFS, self).extract_head(fp, basefile, force_ocr)

    def parse_body(self, fp, basefile):
        if basefile.startswith("konsolidering"):
            main = self.maintext_from_soup(BeautifulSoup(fp, "lxml"))
            return Body([elements_from_soup(main)],
                        uri=None)
        else:
            return super(SOSFS,self).parse_body(fp, basefile)

    def fwdtests(self):
        t = super(SOSFS, self).fwdtests()
        t["dcterms:identifier"] = ['^([A-ZÅÄÖ-]+FS\s\s?\d{4}:\d+)']
        return t

    def parse_metadata_from_textreader(self, reader, props, basefile):
        # cue past the first cover pages until we find the first real page
        page = 1
        try:
            while ("Ansvarig utgivare" not in reader.peekchunk('\f') and
                   "Utgivare" not in reader.peekchunk('\f')):
                self.log.debug("%s: Skipping cover page %s" %
                               (basefile, page))
                reader.readpage()
                page += 1
        except IOError:   # read past end of file
            util.robust_remove(self.store.path(basefile,
                                               'intermediate', '.txt'))
            raise RequiredTextMissing("%s: Could not find proper first page" %
                                      basefile)
        return super(SOSFS, self).parse_metadata_from_textreader(reader, props, basefile)


# The previous implementation of STAFS.download_single was just too
# complicated and also incorrect. It has similar requirements like
# FFFS, maybe we could abstract the downloading of base act HTML pages
# that link to base and change acts in PDF and optionally consolidated
# versions, other things.
# 
# class STAFS(MyndFskrBase):
#     alias = "stafs"
#     start_url = ("http://www.swedac.se/sv/Det-handlar-om-fortroende/"
#                  "Lagar-och-regler/Gallande-foreskrifter-i-nummerordning/")
#     basefile_regex = re.compile("^STAFS (?P<basefile>\d{4}:\d+)$")
#     storage_policy = "dir"
#     re_identifier = re.compile('STAFS[ _]+(\d{4}[:/_-]\d+)')


class STFS(MyndFskrBase):
    # (id vs länk)
    alias = "stfs"
    start_url = "https://www.sametinget.se/dokument?cat_id=52"
    download_iterlinks = False
    
    @decorators.downloadmax
    @recordlastbasefile
    def download_get_basefiles(self, source):
        done = False
        soup = BeautifulSoup(source, "lxml")
        while not done:
            for item in soup.find_all("div", "item"):
                title = item.h3.text.strip() # eg. 'STFS 2018:1 Föreskrifter om partistöd'
                basefile = " ".join(title.split(" ")[:2])
                link = item.find("a", href=re.compile("file_id=\d+$"))
                yield self.sanitize_basefile(basefile), urljoin(self.start_url, link["href"])
            nextpage = soup.find("a", text="»")
            if nextpage:
                nexturl = urljoin(self.start_url, nextpage["href"])
                self.log.debug("getting page %s" % nexturl)
                resp = self.session.get(nexturl)
                resp.raise_for_status()
                soup = BeautifulSoup(resp.text, "lxml")
            else:
                done = True

class SvKFS(MyndFskrBase):
    alias = "svkfs"
    start_url = "http://www.svk.se/om-oss/foreskrifter/"
    basefile_regex = re.compile("^SvKFS (?P<basefile>\d{4}:\d{1,3})")
