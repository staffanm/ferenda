# -*- coding: utf-8 -*-
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
from builtins import *

from tempfile import mktemp
from urllib.parse import urljoin, unquote
from xml.sax.saxutils import escape as xml_escape
from io import BytesIO
import os
import re
import json
from collections import OrderedDict

from rdflib import URIRef, Literal, Namespace
from bs4 import BeautifulSoup
import requests
import lxml.html
import datetime
from rdflib import RDF, Graph
from rdflib.resource import Resource
from rdflib.namespace import DCTERMS, SKOS

from . import RPUBL, RINFOEX, SwedishLegalSource, FixedLayoutSource
from .fixedlayoutsource import FixedLayoutStore
from .swedishlegalsource import SwedishCitationParser, SwedishLegalStore
from ferenda import TextReader, Describer, Facet, PDFReader, DocumentEntry, DocumentRepository
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
    downloaded_suffixes = [".pdf", ".html"]

    
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
    xslt_template = "xsl/paged.xsl"

    rdf_type = (RPUBL.Myndighetsforeskrift, RPUBL.AllmannaRad)
    # FIXME: For docs of rdf:type rpubl:KonsolideradGrundforfattning,
    # not all of the above should be required (rpubl:beslutadAv,
    # rpubl:beslutsdatum, rpubl:forfattningssamling (in fact, that one
    # shoud not be present), rpubl:ikrafttradandedatum,
    # rpubl:utkomFranTryck
    
    basefile_regex = re.compile('(?P<basefile>\d{4}[:/_-]\d{1,3})(?:|\.\w+)$')
    document_url_regex = re.compile('.*(?P<basefile>\d{4}[:/_-]\d{1,3}).pdf$')
    download_accept_404 = True  # because the occasional 404 is to be expected

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
    baseprops = {'nfs/2004:5': {"rpubl:beslutadAv": "Naturvårdsverket"}}


    def __init__(self, config=None, **kwargs):
        super(MyndFskrBase, self).__init__(config, **kwargs)
        # unconditionally set downloaded_suffixes, since the
        # conditions for this re-set in DocumentRepository.__init__ is
        # too rigid
        if hasattr(self, 'downloaded_suffixes'):
            self.store.downloaded_suffixes = self.downloaded_suffixes
        else:
            self.store.downloaded_suffixes = [self.downloaded_suffix]
        
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

    def required_predicates(self, doc):
        return [RDF.type, DCTERMS.title,
                DCTERMS.identifier, RPUBL.arsutgava,
                DCTERMS.publisher, RPUBL.beslutadAv,
                RPUBL.beslutsdatum,
                RPUBL.forfattningssamling,
                RPUBL.ikrafttradandedatum, RPUBL.lopnummer,
                RPUBL.utkomFranTryck, PROV.wasGeneratedBy]

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
        elif len(segments) == 4 and segments[1] == "fs":  # eg for HSLF-FS and others
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
                if (self.landingpage and self.landingpage_url_regex and
                    re.match(self.landingpage_url_regex, link)):
                    m = re.match(self.landingpage_url_regex, link)
                elif (self.basefile_regex and
                        elementtext and
                        re.search(self.basefile_regex, elementtext)):
                    m = re.search(self.basefile_regex, elementtext)
                elif self.document_url_regex and re.match(self.document_url_regex, link):
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

    def download_single(self, basefile, url=None):
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
            # assure that the downloaded resource really is a PDF
            downloaded_file = self.store.downloaded_path(basefile)
            with open(downloaded_file, "rb") as fp:
                sig = fp.read(4)
            if sig != b'%PDF':
                other_file = downloaded_file.replace(".pdf", ".bak")
                util.robust_rename(downloaded_file, other_file)
                raise errors.DownloadFileNotFoundError("%s: Assumed PDF, but downloaded file has sig %r"
                                                       " (saved at %s)" % (basefile, sig, other_file))
        return ret

    def download_post_form(self, form, url):
        raise NotImplementedError

    def _basefile_frag_to_altlabel(self, basefilefrag):
        # optionally map fs identifier to match skos:altLabel.
        return {'ELSAKFS': 'ELSÄK-FS',
                'HSLFFS': 'HSLF-FS',
                'FOHMFS': 'FoHMFS',
                'SVKFS': 'SvKFS'}.get(basefilefrag, basefilefrag)
    
    def metadata_from_basefile(self, basefile):
        a = super(MyndFskrBase, self).metadata_from_basefile(basefile)
        # munge basefile or classname to find the skos:altLabel of the
        # forfattningssamling we're dealing with
        if "/" in basefile:
            segments = basefile.split("/")
            if len(segments) > 2 and segments[0] == "konsolidering":
                a["rdf:type"] = RPUBL.KonsolideradGrundforfattning
                a["rpubl:konsoliderar"] = self.canonical_uri(basefile.split("/",1)[1])
                a["dcterms:issued"] = datetime.date.today()
                segments.pop(0)
            fs, realbasefile = segments
            fs = fs.upper()
        else:
            fs = self.__class__.__name__
            realbasefile = basefile
        fs = self._basefile_frag_to_altlabel(fs)
        a["rpubl:arsutgava"], a["rpubl:lopnummer"] = realbasefile.split(":", 1)
        a["rpubl:forfattningssamling"] = self.lookup_resource(fs, SKOS.altLabel)
        return a

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

    # principial call chain
    # parse
    #    blacklist
    #    textreader_from_basefile  -- parse_open
    #       textreader_from_basefile_pdftotext
    #          sanitize_text
    #    parse_metadata_from_textreader
    #       fwdtests
    #       revtests
    #       sanitize_metadata
    #       polish_metadata
    #          _basefile_frag_to_altlabel
    #          makeurl
    #             attributes_to_resource
    #       infer_metadata
    #    parse_open
    #    parse_body
    #    postprocess_doc(doc)
    #    parse_entry_update(doc)

    @decorators.action
    @decorators.managedparsing
    def parse(self, doc):
        # This has a similar structure to DocumentRepository.parse but
        # works on PDF docs converted to plaintext, instead of HTML
        # trees. FIXME: We should convert the general structure to
        # what SwedishLegalSource uses.
        
        # Some documents are just beyond usable and/or completely
        # uninteresting from a legal information point of view. We
        # keep a hardcoded black list to skip these.
        if doc.basefile in self.blacklist:
            raise errors.DocumentRemovedError("%s is blacklisted" % doc.basefile,
                                       dummyfile=self.store.parsed_path(doc.basefile))
        orig_basefile = doc.basefile
        ret = True
        if doc.basefile.startswith("konsolidering/"):
            self.parse_metadata_from_consolidated(doc)
        else:
            reader = self.textreader_from_basefile(doc.basefile)
            try:
                self.parse_metadata_from_textreader(reader, doc)
            except RequiredTextMissing:
                if self.might_need_ocr:
                    self.log.warning("%s: reprocessing using OCR" % doc.basefile)
                    reader = self.textreader_from_basefile(doc.basefile, force_ocr=True)
                    self.parse_metadata_from_textreader(reader, doc)
                else:
                    raise

            # the parse_metadata_from_textreader step might determine
            # that the assumed basefile was wrong (ie during download
            # we thought it would be fffs/1991:15, but upon parsing,
            # we discovered it should be bffs/1991:15. This is
            # communicated by returning the truthy value of the new
            # basefile.
            if doc.basefile != orig_basefile:
                assert doc.basefile # it must be truthy, if it's False or None we have a problem 
                ret = doc.basefile
        
        # now treat the body like PDFReader does
        fp = self.parse_open(orig_basefile)
        if orig_basefile != doc.basefile:
            # if basefile has changed, parse_open still needed the
            # original basefile. But afterwards, move any created
            # intermediate files to their correct place.
            old_dir = os.path.dirname(self.store.intermediate_path(orig_basefile))
            new_dir = os.path.dirname(self.store.intermediate_path(doc.basefile))
            util.ensure_dir(new_dir)
            if os.path.exists(new_dir):
                util.robust_remove(new_dir)
            # I'm not sure it's wise to remove the entire olddir, as
            # this will cause self.parse_open to run pdftohtml again
            # and again when using --force. But at least it will work.
            os.rename(old_dir, new_dir)
        
        doc.body = self.parse_body(fp, doc.basefile)
        if getattr(doc.body, 'tagname', None) != "body":
            doc.body.tagname = "body"
        doc.body.uri = doc.uri
        self.postprocess_doc(doc)
        self.parse_entry_update(doc)
        return ret 

    # subclasses should override this and make to add a suitable set
    # of triples (particularly rpubl:konsolideringsunderlag) to
    # doc.meta. Also maybe correct dcterms:issued?
    def parse_metadata_from_consolidated(self,doc):
        # the resource is a BNode, but we already know the URI (it's doc.uri)
        resource = self.attributes_to_resource(
            self.metadata_from_basefile(doc.basefile), infer_nodes=False)
        for p, o in resource.predicate_objects():
            if isinstance(p, Resource):
                p = p.identifier
            if isinstance(o, Resource):
                o = o.identifier
            doc.meta.add((URIRef(doc.uri), p, o))
        
    
    def textreader_from_basefile(self, basefile, force_ocr=False):
        infile = self.store.downloaded_path(basefile)
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
                ['\s(?:meddelar|föreskriver)\s([A-ZÅÄÖ][\w ]+?)\d?\s',
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

                 

    def parse_metadata_from_textreader(self, reader, doc):
        g = doc.meta

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
        props = self.baseprops.get(doc.basefile, {})
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
                           (doc.basefile, pagecount))
        if 'rpubl:beslutsdatum' not in props:
            # raise errors.ParseError(
            self.log.warning(
                "%s: Couldn't find required properties on any page, giving up" %
                doc.basefile)

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

        self.sanitize_metadata(props, doc)
        self.polish_metadata(props, doc)
        self.infer_metadata(doc.meta.resource(doc.uri), doc.basefile)
        return doc

    def sanitize_metadata(self, props, doc):
        """Correct those irregularities in the extracted metadata that we can
           find

        """

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

    def polish_metadata(self, props, doc):
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
        # find a way to give it access to props['dcterms:identifier']
        if 'dcterms:identifier' in props:
            (pub, year, ordinal) = re.split('[ :]',
                                            props['dcterms:identifier'])
        else:
            # do a a simple inference from basefile and populate props
            (pub, year, ordinal) = re.split('[/:_]', doc.basefile.upper())
            pub = self._basefile_frag_to_altlabel(pub)
            props['dcterms:identifier'] = "%s %s:%s" % (pub, year, ordinal)
            self.log.warning("%s: Couldn't find dcterms:identifier, inferred %s from basefile" %
                             (doc.basefile, props['dcterms:identifier']))
        attrs = {'rdf:type': RPUBL.Myndighetsforeskrift,
                 'rpubl:forfattningssamling':
                 self.lookup_resource(pub, SKOS.altLabel),
                 'rpubl:arsutgava': year,
                 'rpubl:lopnummer': ordinal}
        uri = makeurl(attrs)

        if doc.uri is not None and uri != doc.uri:
            self.log.warning(
                "Assumed URI would be %s but it turns out to be %s" %
                (doc.uri, uri))
            newbasefile = self.basefile_from_uri(uri)
            if newbasefile:
                # change the basefile we're dealing with. Touch
                # self.store.parsed_path(basefile) first so we don't
                # regenerate. 
                with self.store.open_parsed(doc.basefile, "w"):
                    pass
                doc.basefile = newbasefile
        doc.uri = uri
        desc = Describer(doc.meta, doc.uri)

        fs = self.lookup_resource(pub, SKOS.altLabel)
        desc.rel(RPUBL.forfattningssamling, fs)
        # publisher for the series == publisher for the document
        publisher = self.commondata.value(fs, DCTERMS.publisher)
        assert publisher, "Found no publisher for fs %s" % fs
        desc.rel(DCTERMS.publisher, publisher)

        desc.value(RPUBL.arsutgava, year)
        desc.value(RPUBL.lopnummer, ordinal)
        desc.value(DCTERMS.identifier, props['dcterms:identifier'])
        if 'rpubl:beslutadAv' in props:
            try:
                beslutad_av = props['rpubl:beslutadAv']
                if beslutad_av == "Räddningsverket":  # The agency sometimes doesn't use it's official name!
                    self.log.warning("%s: rpubl:beslutadAv was '%s', "
                                     "correcting to 'Statens räddningsverk'" %
                                     (doc.basefile, beslutad_av))
                    beslutad_av = "Statens räddningsverk" 
                desc.rel(RPUBL.beslutadAv,
                         self.lookup_resource(beslutad_av))
            except KeyError as e:
                if self.alias == "ffs":
                    # These documents are often enacted by entities
                    # like Chefen för Flygvapnet, Försvarets
                    # sjukvårdsstyrelse, Generalläkaren, Krigsarkivet,
                    # Överbefälhavaren. We have no resources for those
                    # and probably won't have (are they even
                    # enumerable?)
                    self.log.warning("%s: Couldn't look up entity '%s'" %
                                     (doc.basefile, props['rpubl:beslutadAv']))
                else:
                    # there are other examples of where a entity might
                    # not be resolved, like lvfs/1999:25, where a bad
                    # OCR has resulted in "Läkea\nmedelsverket"
                    # (instead of the proper Läkemedelsverket). Keep a
                    # blacklist for now, until we can determine the
                    # size of this problem.
                    if doc.basefile in ("lvfs/1995:25"):
                        self.log.warning("%s: Unknown entity '%s'" %
                                         (doc.basefile, props['rpubl:beslutadAv']))
                    else:
                        raise e

        if 'dcterms:issn' in props:
            desc.value(DCTERMS.issn, props['dcterms:issn'])

        if 'dcterms:title' in props:
            desc.value(DCTERMS.title,
                       Literal(util.normalize_space(
                           props['dcterms:title']), lang="sv"))

            if re.search('^(Föreskrifter|[\w ]+s föreskrifter) om ändring i ',
                         props['dcterms:title'], re.UNICODE):
                # There should be something like FOOFS 2013:42 (or
                # possibly just 2013:42) in the title. The regex is forgiving about spurious spaces, seee LVFS 1998:5
                m = re.search('(?P<pub>[A-ZÅÄÖ-]+FS|) ?(?P<year>\d{4}) ?:(?P<ordinal>\d+)',
                              props['dcterms:title'])
                if not m:
                    # raise errors.ParseError(
                    self.log.warning(
                        "%s: Couldn't find reference to change act in title %r" %
                        (doc.basefile, props['dcterms:title']))
                else:
                    parts = m.groupdict()
                    if not parts['pub']:
                        parts["pub"] = props['dcterms:identifier'].split(" ")[0]

                    origuri = makeurl({'rdf:type': RPUBL.Myndighetsforeskrift,
                                       'rpubl:forfattningssamling':
                                       self.lookup_resource(parts["pub"], SKOS.altLabel),
                                       'rpubl:arsutgava': parts["year"],
                                       'rpubl:lopnummer': parts["ordinal"]})
                    desc.rel(RPUBL.andrar,
                             URIRef(origuri))

            # FIXME: is this a sensible value for rpubl:upphaver
            if (re.search('^(Föreskrifter|[\w ]+s föreskrifter) om upphävande '
                          'av', props['dcterms:title'], re.UNICODE)
                    and not 'rpubl:upphaver' in props):
                props['rpubl:upphaver'] = props['dcterms:title']

        for key, pred in (('rpubl:utkomFranTryck', RPUBL.utkomFranTryck),
                          ('rpubl:beslutsdatum', RPUBL.beslutsdatum),
                          ('rpubl:ikrafttradandedatum', RPUBL.ikrafttradandedatum)):
            if key in props:
                # FIXME: how does this even work
                if (props[key] == 'denna dag' and
                        key == 'rpubl:ikrafttradandedatum'):
                    desc.value(RPUBL.ikrafttradandedatum,
                               self.parse_swedish_date(props['rpubl:beslutsdatum']))
                elif (props[key] == 'utkom från trycket' and
                      key == 'rpubl:ikrafttradandedatum'):
                    desc.value(RPUBL.ikrafttradandedatum,
                               self.parse_swedish_date(props['rpubl:utkomFranTryck']))
                else:
                    try:
                        date = self.parse_swedish_date(props[key].lower())
                        desc.value(pred,
                                   self.parse_swedish_date(props[key].lower()))
                    except ValueError as e:
                        self.log.error("%s: Couldn't parse %s as a date" % (doc.basefile, props[key].lower()))
                        

        if 'rpubl:genomforDirektiv' in props:
            diruri = makeurl({'rdf:type': RINFOEX.EUDirektiv, # FIXME: standardize this type
                              'rpubl:celexNummer':
                              props['rpubl:genomforDirektiv']})
            desc.rel(RPUBL.genomforDirektiv, diruri)

        has_bemyndiganden = False
        if 'rpubl:bemyndigande' in props:
            # dehyphenate (note that normalize_space already has changed "\n" to " "...
            props['rpubl:bemyndigande'] = props['rpubl:bemyndigande'].replace("\xad ", "")
            result = parser.parse_string(props['rpubl:bemyndigande'])
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

            for bem_uri in filtered_bemyndiganden:
                desc.rel(RPUBL.bemyndigande, bem_uri)

        if 'rpubl:upphaver' in props:
            for upph in re.findall('([A-ZÅÄÖ-]+FS \d{4}:\d+)',
                                   util.normalize_space(props['rpubl:upphaver'])):
                (pub, year, ordinal) = re.split('[ :]', upph)
                upphuri = makeurl({'rdf:type': RPUBL.Myndighetsforeskrift,
                                   'rpubl:forfattningssamling':
                                   self.lookup_resource(pub, SKOS.altLabel),
                                   'rpubl:arsutgava': year,
                                   'rpubl:lopnummer': ordinal})
                desc.rel(RPUBL.upphaver, upphuri)

        if ('dcterms:title' in props and
            "allmänna råd" in props['dcterms:title'] and
                "föreskrifter" not in props['dcterms:title']):
            rdftype = RPUBL.AllmannaRad
        else:
            rdftype = RPUBL.Myndighetsforeskrift
        desc.rdftype(rdftype)
        desc.value(self.ns['prov'].wasGeneratedBy, self.qualified_class_name())
        if RPUBL.bemyndigande in self.required_predicates:
            self.required_predicates.pop(self.required_predicates.index(RPUBL.bemyndigande))
        if rdftype == RPUBL.Myndighetsforeskrift:
            self.required_predicates.append(RPUBL.bemyndigande)


    def infer_identifier(self, basefile):
        p = self.store.distilled_path(basefile)
        if not os.path.exists(p):
            raise ValueError("No distilled file for basefile %s at %s" % (basefile, p))

        with self.store.open_distilled(basefile) as fp:
            g = Graph().parse(data=fp.read())
        uri = self.canonical_uri(basefile)
        return str(g.value(URIRef(uri), DCTERMS.identifier))

    # FIXME: THis is copied verbatim from PDFDocumentRepository --
    # should we inherit from that as well? Or should FixedLayoutSource
    # do that? SHould this function do things depending on config.pdfimages?
    def create_external_resources(self, doc):
        resources = []
        if isinstance(doc.body, Body):
            # document wasn't derived from a PDF file, probably from HTML instead
            return resources
        
        cssfile = self.store.parsed_path(doc.basefile, attachment="index.css")
        urltransform = self.get_url_transform_func([self], os.path.dirname(cssfile),
                                                   develurl=self.config.develurl)
        resources.append(cssfile)
        util.ensure_dir(cssfile)
        with open(cssfile, "w") as fp:
            # Create CSS header with fontspecs
            assert isinstance(doc.body, PDFReader), "doc.body is %s, not PDFReader -- still need to access fontspecs etc" % type(doc.body)
            for spec in list(doc.body.fontspec.values()):
                fp.write(".fontspec%s {font: %spx %s; color: %s;}\n" %
                         (spec['id'], spec['size'], spec['family'],
                          spec.get('color', 'black')))

            # 2 Copy all created png files to their correct locations
            for cnt, page in enumerate(doc.body):
                if page.background:
                    src = self.store.intermediate_path(
                        doc.basefile, attachment=os.path.basename(page.background))
                    dest = self.store.parsed_path(
                        doc.basefile, attachment=os.path.basename(page.background))
                    resources.append(dest)
                    if util.copy_if_different(src, dest):
                        self.log.debug("Copied %s to %s" % (src, dest))
                    desturi = "%s?dir=parsed&attachment=%s" % (doc.uri, os.path.basename(dest))
                    desturi = urltransform(desturi)
                    background = " background: url('%s') no-repeat grey;" % desturi
                else:
                    background = ""
                fp.write("#page%03d {width: %spx; height: %spx;%s}\n" %
                         (cnt+1, page.width, page.height, background))
        return resources


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
        # the test of wheter base act: the basefile matches the
        # identifier in the title. If not, this is a change act.
        is_baseact = identifier in title
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
            changeheader = soup.find(["h2", "h3"], text="Ursprungs- och ändringsföreskrifter")
            if not changeheader:
                self.log.error("%s: Can't find a list of change acts at %s" % (basefile, url))
                return False
            pdfs = changeheader.parent.find_all("a", href=re.compile("\.pdf$"))
            # first, get the actual basefile we're looking for (assume
            # there really is one)
            norm = util.normalize_space
            match = lambda x: identifier in norm(x.text) or short_identifier in norm(x.text)
            links = [x for x in pdfs if match(x)]
            if not links:
                self.log.error("Can't find PDF link to %s amongst %s" % (identifier, [x.text for x in pdfs]))
                return False
            link = [x for x in pdfs if match(x)][0]
            pdfurl = urljoin(url, link["href"])
            # note: the actual downloading (call to
            # DocumentRepository.download_single) happens at the very
            # end
            
            # then, 1) find out what change act the consolidated
            # version might be updated to
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

    def parse_metadata_from_consolidated(self,doc):
        super(AFS, self).parse_metadata_from_consolidated(doc)
        with self.store.open_downloaded(doc.basefile, attachment="landingpage.html") as fp:
            soup = BeautifulSoup(fp.read(), "lxml")
        changeheader = soup.find(["h2", "h3"], text="Ursprungs- och ändringsföreskrifter")
        pdfs = changeheader.parent.find_all("a", href=re.compile("\.pdf$"))
        norm = util.normalize_space
        # FIXME: in some cases the leading AFS is missing
        matcher = re.compile("(?:|AFS )(\d+:\d+)").match
        fsnummer = [matcher(norm(x.text)).group(1) for x in pdfs if matcher(norm(x.text))]
        for f in fsnummer:
            konsolideringsunderlag = self.canonical_uri(self.sanitize_basefile(f))
            doc.meta.add((URIRef(doc.uri), RPUBL.konsolideringsunderlag, URIRef(konsolideringsunderlag)))

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

    nextpage_regex = ">"
    nextpage_url_regex = None
    basefile_regex = "^\s*(?P<basefile>\d{4}:\d+)"
    download_rewrite_url = True
    download_formid = "aspnetForm"

    def remote_url(self, basefile):
        if "/" in basefile:
            basefile = basefile.split("/")[1]
        if basefile in ("2017:12", "2014:15"):  # single exception to the URL pattern
            basefile = basefile.replace(":", "-")
        elif basefile == "2017:11": # ok, so not single exception, but...
            basefile = "Domstolsverkets-forfattningssamling-DVFS-" + basefile
        return "http://www.domstol.se/Ladda-ner--bestall/Verksamhetsstyrning/DVFS/DVFS2/%s/" % basefile.replace(
            ":", "")

    def download_post_form(self, form, url):
        # nexturl == "javascript:__doPostBack('ctl00$MainRegion$"
        #            "MainContentRegion$LeftContentRegion$ctl01$"
        #            "epiNewsList$ctl09$PagingID15','')"
        etgt, earg = [m.group(1) for m in re.finditer("'([^']*)'", url)]
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

    def maintext_from_soup(self, soup):
        main = soup.find("div", id="readme")
        if main:
            main.find("div", "rs_skip").decompose()
            return main
        elif soup.find("title").text == "Sveriges Domstolar - 404":
            e = errors.DocumentRemovedError()
            e.dummyfile = self.store.parsed_path(basefile)
            raise e

    def textreader_from_basefile(self, basefile):
        infile = self.store.downloaded_path(basefile)
        soup = BeautifulSoup(util.readfile(infile), "lxml")
        main = self.maintext_from_soup(soup)
        maintext = main.get_text("\n\n", strip=True)
        return TextReader(string=maintext)

    def parse_open(self, basefile):
        return self.store.open_downloaded(basefile)

    def parse_body(self, fp, basefile):
        main = self.maintext_from_soup(BeautifulSoup(fp, "lxml"))
        return Body([elements_from_soup(main)],
                    uri=None)

    def fwdtests(self):
        t = super(DVFS, self).fwdtests()
        t["dcterms:identifier"] = ['(DVFS\s\s?\d{4}:\d+)']
        return t


class EIFS(MyndFskrBase):
    alias = "eifs"
    start_url = "http://www.ei.se/sv/Publikationer/Foreskrifter/"
    basefile_regex = None
    document_url_regex = re.compile('.*(?P<basefile>EIFS_\d{4}_\d+).pdf$')

    def sanitize_basefile(self, basefile):
        basefile = basefile.replace("_", "/", 1)
        basefile = basefile.replace("_", ":", 1)
        return super(EIFS, self).sanitize_basefile(basefile)

class ELSAKFS(MyndFskrBase):
    alias = "elsakfs"  # real name is ELSÄK-FS, but avoid swedchars, uppercase and dashes
    start_url = "https://www.elsakerhetsverket.se/om-oss/lag-och-ratt/foreskrifter/"
    landingpage = True

    # this repo has a mismatch between basefile prefix and the URI
    # space slug. This is easily fixed.
    def basefile_from_uri(self, uri):
        basefile = super(MyndFskrBase, self).basefile_from_uri(uri)
        if basefile.startswith("elsaek-fs"):
                return basefile.replace("elsaek-fs", "elsakfs")

    def fwdtests(self):
        t = super(ELSAKFS, self).fwdtests()
        # it's hard to match "...föreskriver X följande" if X contains
        # spaces ("följande" can be pretty much anything else)
        t["rpubl:beslutadAv"].insert(0, '(?:meddelar|föreskriver)\s(Sveriges geologiska undersökning)')
        return t


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
        return self.session.post(self.start_url, data=payload)

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
    start_url = ("http://www.kriminalvarden.se/om-kriminalvarden/"
                 "publikationer/regelverk")
    # (finns även konsoliderade på http://www.kriminalvarden.se/
    #  om-kriminalvarden/styrning-och-regelverk/lagar-forordningar-och-
    #  foreskrifter)
    download_iterlinks = False
    basefile_regex = re.compile("(?P<basefile>KVV?FS \d{4}:\d+)")

    def forfattningssamlingar(self):
        return ["kvfs", "kvvfs"]
    
    @decorators.downloadmax
    def download_get_basefiles(self, source):
        lasthref = None
        done = False
        while not done:
            soup = BeautifulSoup(source, "lxml") # source is HTML text,
                                                 # since
                                                 # download_iterlinks is
                                                 # False
            for h in soup.find("section", "publications-list").find_all("h3"):
                m = self.basefile_regex.match(h.text)
                if not m:
                    continue
                el = h.parent.parent.find("a")
                if el:
                    yield self.sanitize_basefile(m.group("basefile")), urljoin(self.start_url, el.get("href"))
            nextlink = soup.find("ul", "pagination").find_all("a")[-1] # last link is Next
            if nextlink and nextlink["href"] != lasthref:
                source = self.session.get(urljoin(self.start_url, nextlink["href"])).text
                lasthref = nextlink["href"]
            else:
                done = True

    def forfattningssamlingar(self):
        return ["kvfs", "kvvfs"]

class LMFS(MyndFskrBase):
    alias = "lmfs"
    start_url = "http://www.lantmateriet.se/Om-Lantmateriet/Rattsinformation/Foreskrifter/"
    basefile_regex = re.compile('(?P<basefile>LMV?FS \d{4}:\d{1,3})')

    def forfattningssamlingar(self):
        return ["lmfs", "lmvfs"]

    def fwdtests(self):
        t = super(LMFS, self).fwdtests()
        # it's hard to match "...föreskriver X följande" if X contains
        # spaces ("följande" can be pretty much anything else)
        t["rpubl:beslutadAv"].insert(0, '(?:meddelar|föreskriver)\s(Statens\s+lantmäteriverk)')
        return t

class LIFS(MyndFskrBase):
    alias = "lifs"
    start_url = "http://www.lotteriinspektionen.se/sv/Lagar-och-villkor/Foreskrifter/"
    basefile_regex = re.compile('(?P<basefile>LIFS \d{4}:\d{1,3})')


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
    start_url = "https://www.migrationsverket.se/Om-Migrationsverket/Styrning-och-uppfoljning/Lagar-och-regler/Foreskrifter.html"
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
    basefile_regex = "^(?P<basefile>(MPRTFS|MRTVFS|RTVFS) \d+:\d+)$"
    document_url_regex = None
    def forfattningssamlingar(self):
        return ["mprtfs", "mrtvfs", "rtvfs"]


class MSBFS(MyndFskrBase):
    alias = "msbfs"
    start_url = "https://www.msb.se/sv/Om-MSB/Lag-och-ratt/"
    # FIXME: start_url now requres a POST but with a bunch of
    # viewstate crap to yield a full list
    download_iterlinks = False # download_get_basefiles will be called
                               # with start_url text, not result from
                               # .iterlinks()

    basefile_regex = "^(?P<basefile>(MSBFS|SRVFS|KBMFS|SÄIFS) \d+:\d+)"

    def forfattningssamlingar(self):
        return ["msbfs", "srvfs", "kbmfs", "säifs"]

    # this repo has basefiles eg "säifs/2000:6" but the uri will be on
    # the form "../saeifs/2000:6" so we do a special-case transform
    def basefile_from_uri(self, uri):
        uri = uri.replace("/saeifs/", "/säifs/")
        return super(MyndFskrBase, self).basefile_from_uri(uri)

    def download_get_basefiles(self, source):
        doc = lxml.html.fromstring(source)
        doc.make_links_absolute(self.start_url)
        form = doc.forms[0]
        data=dict(form.fields)
        data['ctl00$ContentArea$MainContentArea$ctl02$ctl00$ctl06$SearchFormBox$ctl00$cboValidDate'] = ''
        data['ctl00$SiteTop$SiteQuickSearch$txtSearch'] = ''
        data['ctl00$ContentArea$MainContentArea$ctl02$ctl00$ctl04$ctl00$txtSearch'] = ''
        # simulate a click on the lower search button
        data['ctl00$ContentArea$MainContentArea$ctl02$ctl00$ctl06$SearchFormBox$ctl00$ctl00'] = "Sök"
        resp = self.session.post(form.action, data=data)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        for link in soup.find_all("a", text=re.compile(self.basefile_regex),
                                  href=re.compile("\.pdf$")):
            basefile = re.match(self.basefile_regex, link.get_text()).group("basefile")
            yield self.sanitize_basefile(basefile), urljoin(self.start_url, link["href"])

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
    basefile_regex = "^(?P<basefile>S?NFS \d+:\d+)$"
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
        soup = BeautifulSoup(self.session.get(url).text, "lxml")
        basehead = soup.find("h3", text=re.compile("Grundföreskrift$"))
        if not basehead:
            realbasefile = basefile
        else:
            m = re.match("(S?NFS)\s+(\d+:\d+)", basehead.get_text())
            realbasefile = m.group(1).lower() + "/" + m.group(2)
        self.log.info(
            "%s: Downloaded index %s, real basefile was %s" %
            (basefile, url, realbasefile))
        basefile = realbasefile
        descpath = self.store.downloaded_path(basefile,
                                              attachment="description.html")
        self.download_if_needed(url, basefile, filename=descpath)
        soup = BeautifulSoup(util.readfile(descpath), "lxml")
        seen_consolidated = False
        # find all pdf links, identify consolidated version if present
        # [1:] in order to skip header
        for tr in soup.find("table", "regulations-table").find_all("tr")[1:]:
            head = tr.find("h3")
            link = tr.find("a", href=re.compile("\.pdf$", re.I))
            if not link:
                continue
            if "Konsoliderad" in head.get_text() or "-k" in link.get("href"):
                assert not seen_consolidated
                conspath = self.store.downloaded_path(basefile,
                                                      attachment="consolidated.pdf")
                consurl = urljoin(url, link.get("href"))
                self.log.info(
                    "%s: Downloading consolidated version from %s" %
                    (basefile, consurl))
                self.download_if_needed(consurl, basefile, filename=conspath)
                seen_consolidated = True
            else:
                m = re.match("(S?NFS)\s+(\d+:\d+)", head.get_text())
                subbasefile = m.group(1).lower() + "/" + m.group(2)
                suburl = urljoin(url, link.get("href"))
                entrypath = self.store.documententry_path(subbasefile)
                DocumentEntry.updateentry(self.download_single, "download", entrypath, subbasefile, suburl)
                # self.download_single(subbasefile, suburl)

    def fwdtests(self):
        t = super(NFS, self).fwdtests()
        # it's hard to match "...föreskriver X följande" if X contains spaces ("följande" can be pretty much anything else)
        t["rpubl:beslutadAv"].insert(0, '(?:meddelar|föreskriver)\s(Statens\s*naturvårdsverk)')
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
        for item in soup.find_all("td", text=re.compile("^\d{4}:\d+$")):
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


class SJVFS(MyndFskrBase):
    alias = "sjvfs"
    start_url = "http://www.jordbruksverket.se/forfattningar/forfattningssamling.4.5aec661121e2613852800012537.html"
    download_iterlinks = False

    def forfattningssamlingar(self):
        return ["sjvfs", "dfs"]

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

    start_url = "https://www4.skatteverket.se/rattsligvagledning/115.html"
    # also consolidated versions
    # http://www.skatteverket.se/rattsinformation/lagrummet/foreskrifterkonsoliderade/aldrear.4.19b9f599116a9e8ef3680004242.html
    def forfattningssamlingar(self):
        return ["skvfs", "rsfs"]

    # URL's are highly unpredictable. We must find the URL for every
    # resource we want to download, we cannot transform the resource
    # id into a URL
    @decorators.recordlastdownload
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
    def textreader_from_basefile(self, basefile):
        outfile = self.store.path(basefile, 'intermediate', '.txt')
        # prefer the PDF attachment to the html page
        infile = self.store.downloaded_path(basefile, attachment="index.pdf")
        if os.path.exists(infile):
            tmpfile = self.store.intermediate_path(basefile, attachment="index.pdf")
            return self.textreader_from_basefile_pdftotext(infile, tmpfile, outfile, basefile)
        else:
            infile = self.store.downloaded_path(basefile)
            soup = BeautifulSoup(util.readfile(infile), "lxml")
        h = soup.find("h1", id="pageheader")
        body = soup.find("div", "body")
        if body:
            maintext = body.get_text("\n\n", strip=True)
            maintext = h.get_text().strip() + "\n\n" + maintext
            outfile = self.store.path(basefile, 'intermediate', '.txt')
            util.writefile(outfile, maintext)
            return TextReader(string=maintext)
        else:
            raise ParseError("%s: Didn't find a text body element" % basefile)


class SOSFS(MyndFskrBase):
    alias = "sosfs"
    start_url = "http://www.socialstyrelsen.se/sosfs"
    storage_policy = "dir"  # must be able to handle attachments
    download_iterlinks = False

    def forfattningssamlingar(self):
        return ["hslffs", "sosfs"]
    

    def _basefile_from_text(self, linktext):
        if linktext:
            m = re.search("((SOSFS|HSLF-FS)\s+\d+:\d+)", linktext)
            if m:
                return self.sanitize_basefile(m.group(1))

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
            # If a base act has no changes, only type 1 links will be
            # on the front page. If it has any changes, only a type 2
            # link will be on the front page, but type 1 links will be
            # on that subsequent page.
            if txt.startswith("Grundförfattning"):
                # 1) links to HTML pages describing (and linking to) a
                # base act, eg for SOSFS 2014:10
                # http://www.socialstyrelsen.se/publikationer2014/2014-10-12
                yield(basefile, link)
            elif txt.startswith("Konsoliderad"):
                # 2) links to HTML pages containing a consolidated act
                # (with links to type 1 base and change acts), eg for
                # SOSFS 2011:13
                # http://www.socialstyrelsen.se/sosfs/2011-13 - fetch
                # page, yield all type 1 links, also find basefile form
                # element.text
                konsfile = self.store.downloaded_path(
                    basefile, attachment="konsolidering.html")
                if (self.config.refresh or (not os.path.exists(konsfile))):
                    soup = BeautifulSoup(self.session.get(link).text, "lxml")
                    self.log.debug("%s: Has had changes -- downloading base act and all changes" %
                                   basefile)

                    linkhead = soup.find(text=re.compile(
                        "(Ladda ner eller beställ|Beställ eller ladda ner)"))
                    if linkhead:
                        for link_el in linkhead.find_parent("div").find_all("a"):
                            if '/publikationer' in link_el.get("href"):
                                subbasefile = self._basefile_from_text(link_el.get_text())
                                if subbasefile:
                                    yield(subbasefile,
                                          urljoin(link, link_el.get("href")))
                    else:
                        self.log.warning("%s: Can't find links to base/change"
                                         " acts" % basefile)
                    # then save page itself as grundforf/konsoldering.html
                    self.log.debug("%s: Downloading consolidated version" %
                                   basefile)
                    self.download_if_needed(link, basefile, filename=konsfile)
            elif txt.startswith("Ändringsförfattning"):
                if (self.config.refresh or (
                        not os.path.exists(self.store.downloaded_path(basefile)))):
                    self.log.debug(
                        "%s: Downloading updated consolidated version of base" %
                        basefile)
                    self.log.debug("%s:    first getting %s" % (basefile, link))
                    soup = BeautifulSoup(self.session.get(link).text, "lxml")
                    konsbasefileregex = re.compile(
                        "Senaste version av SOSFS (?P<basefile>\d+:\d+)")
                    konslinkel = soup.find("a", text=konsbasefileregex)
                    if konslinkel:
                        konsbasefile = self.sanitize_basefile(
                            konsbasefileregex.search(
                                konslinkel.text).group("basefile"))
                        konsfile = self.store.downloaded_path(
                            konsbasefile,
                            attachment="konsolidering.html")
                        konslink = urljoin(link, konslinkel.get("href"))
                        self.log.debug(
                            "%s:    now downloading consolidated %s" %
                            (konsbasefile, konslink))
                        self.download_if_needed(konslink, basefile, filename=konsfile)
                    else:
                        self.log.warning(
                            "%s:    Couldn't find link to consolidated version" %
                            basefile)
                yield(basefile, link)

    def download_single(self, basefile, url):
        # the url will be to a HTML landing page. We extract the link
        # to the actual PDF file and then call default impl of
        # download_single in order to update documententry. This'll
        # mean that the orig_url is set to the PDF link, not this HTML
        # landing page.
        soup = BeautifulSoup(self.session.get(url).text, "lxml")
        link_el = soup.find("a", text=re.compile("^\s*Ladda ner\s*$"))
        if link_el:
            link = urljoin(url, link_el.get("href"))
            return super(SOSFS, self).download_single(basefile, link)
        else:
            self.log.warning("%s: No link to PDF file found at %s" % (basefile, url))
            return False

    def fwdtests(self):
        t = super(SOSFS, self).fwdtests()
        t["dcterms:identifier"] = ['^([A-ZÅÄÖ-]+FS\s\s?\d{4}:\d+)']
        return t

    def parse_metadata_from_textreader(self, reader, doc):
        # cue past the first cover pages until we find the first real page
        page = 1
        try:
            while ("Ansvarig utgivare" not in reader.peekchunk('\f') and
                   "Utgivare" not in reader.peekchunk('\f')):
                self.log.debug("%s: Skipping cover page %s" %
                               (doc.basefile, page))
                reader.readpage()
                page += 1
        except IOError:   # read past end of file
            util.robust_remove(self.store.path(doc.basefile,
                                               'intermediate', '.txt'))
            raise RequiredTextMissing("%s: Could not find proper first page" %
                                      doc.basefile)
        return super(SOSFS, self).parse_metadata_from_textreader(reader, doc)


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
#     basefile_regex = "^STAFS (?P<basefile>\d{4}:\d+)$"
#     storage_policy = "dir"
#     re_identifier = re.compile('STAFS[ _]+(\d{4}[:/_-]\d+)')


class STFS(MyndFskrBase):
    # (id vs länk)
    alias = "stfs"
    start_url = "https://www.sametinget.se/dokument?cat_id=52"
    download_iterlinks = False
    
    @decorators.downloadmax
    def download_get_basefiles(self, source):
        done = False
        soup = BeautifulSoup(source, "lxml")
        while not done:
            for item in soup.find_all("div", "item"):
                basefile = item.h3.text.strip()
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
    basefile_regex = "^SvKFS (?P<basefile>\d{4}:\d{1,3})"
