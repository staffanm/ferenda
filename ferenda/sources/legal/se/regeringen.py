# -*- coding: utf-8 -*-
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
from builtins import *

# A abstract base class for fetching and parsing documents
# (particularly preparatory works) from regeringen.se
import os
from time import sleep
import re
import codecs
import json
from datetime import datetime
from urllib.parse import urljoin, urlencode

import requests
import requests.exceptions
import lxml.html
from bs4 import BeautifulSoup
from rdflib import URIRef
from rdflib.namespace import SKOS
from cached_property import cached_property

from ferenda import Describer, DocumentEntry, PDFAnalyzer
from ferenda import util
from ferenda.decorators import recordlastdownload, downloadmax
from ferenda.elements import Section, Link, Body, CompoundElement
from ferenda.pdfreader import PDFReader, Textbox, Textelement, Page, BaseTextDecoder
from ferenda.errors import DocumentRemovedError, DownloadError, DocumentSkippedError
from . import SwedishLegalSource, SwedishLegalStore, Offtryck, RPUBL
from .legalref import LegalRef
from .elements import PreambleSection, UnorderedSection, Forfattningskommentar, Sidbrytning, VerbatimSection
from .decoders import OffsetDecoder1d, OffsetDecoder20, DetectingDecoder


class RegeringenStore(SwedishLegalStore):
    # override to make sure pdf attachments are returned in logical order
    def list_attachments(self, basefile, action, version=None):
        if action == "downloaded":
            repo = Regeringen()
            repo.store.datadir=self.datadir
            for filename, label in repo.sourcefiles(basefile):
                attachment = os.path.basename(filename) + ".pdf"
                filename = self.downloaded_path(basefile, attachment=attachment)
                if os.path.exists(filename):
                    yield attachment
                # else:
                #     self.log.warning("%s: Attachment %s doesn't exist!")
        else:
            for attachment in super(RegeringenStore, self).list_attachments(basefile, action, version):
                yield attachment


class Regeringen(Offtryck):
    RAPPORT = 1341
    DS = 1325
    FORORDNINGSMOTIV = 1326
    KOMMITTEDIREKTIV = 1327
    LAGRADSREMISS = 2085
    PROPOSITION = 1329
    SKRIVELSE = 1330
    SOU = 1331
    SO = 1332

    documentstore_class = RegeringenStore
    document_type = None  # subclasses must override
    start_url = "http://www.regeringen.se/Filter/RssFeed"
    downloaded_suffix = ".html"  # override PDFDocumentRepository
    storage_policy = "dir"
    alias = "regeringen"
    xslt_template = "xsl/forarbete.xsl"
    download_accept_404 = True
    session = None

    @cached_property
    def urlmap(self):
        urlmap_path = self.store.path("urls", "downloaded", ".map", storage_policy="file")
        urlmap = {}
        if os.path.exists(urlmap_path):
            with codecs.open(urlmap_path, encoding="utf-8") as fp:
                for line in fp:
                    if "\t" not in line:
                        continue
                    url, identifier = line.split("\t")
                    urlmap[url] = identifier
        return urlmap


    @recordlastdownload
    def download(self, basefile=None, url=None):
        if basefile:
            if not url:
                entry = DocumentEntry(self.store.documententry_path(basefile))
                url = entry.orig_url
            if url:
                return self.download_single(basefile, url)
            else:
                raise DownloadError("%s doesn't support downloading single basefiles w/o page URL" %
                                    self.__class__.__name__)
        params = {'filterType': 'Taxonomy',
                  'filterByType': 'FilterablePageBase',
                  'preFilteredCategories': '1324',
                  'rootPageReference': '0',
                  'filteredContentCategories': self.document_type
                  }
        if 'lastdownload' in self.config and not self.config.refresh:
            params['fromDate'] = self.config.lastdownload.strftime("%Y-%m-%d")
        # temporary test -- useful when troubleshooting behaviour related to malformed entries in the search result list
        # params['fromDate'] = "2009-05-13"
        # params['toDate']   = "2009-05-20"
        
        self.log.debug("Loading documents starting from %s" %
                       params.get('fromDate', "the beginning"))
        try: 
            for basefile, url in self.download_get_basefiles(params):
                try:
                    # sleep(0.5)  # regeringen.se has a tendency to throw 400 errors, maybe because we're too quick?
                    self.download_single(basefile, url)
                except requests.exceptions.HTTPError as e:
                    if self.download_accept_404 and e.response.status_code == 404:
                        self.log.error("%s: %s %s" % (basefile, url, e))
                        ret = False
                    else:
                        raise e
        finally:
            urlmap_path = self.store.path("urls", "downloaded", ".map", storage_policy="file")
            util.ensure_dir(urlmap_path)
            with codecs.open(urlmap_path, "w", encoding="utf-8") as fp:
                for url, identifier in self.urlmap.items():
                    fp.write("%s\t%s\n" % (url, identifier))


    misleading_urls = set([
        "http://www.regeringen.se/rattsdokument/departementsserien-och-promemorior/2015/06/ds-2015342/",  # Ds 2015:34
        "http://www.regeringen.se/rattsdokument/departementsserien-och-promemorior/2014/05/ds-2014111/",  # Ds 2014:11
        "http://www.regeringen.se/rattsdokument/departementsserien-och-promemorior/2010/02/ds-2009631/",  # Ds 2009:63 -- but in english!
        "http://www.regeringen.se/rattsdokument/departementsserien-och-promemorior/2005/12/ds-2005551/",  # Ds 2005:55 -- but in english!
        "http://www.regeringen.se/rattsdokument/departementsserien-och-promemorior/2005/06/ds-20040551-/",# Ds 2004:51, mislabeled, in easy-reading version
        "http://www.regeringen.se/rattsdokument/departementsserien-och-promemorior/2004/01/ds-2004171/",  # Ds 2004:17
        "http://www.regeringen.se/rattsdokument/departementsserien-och-promemorior/2001/01/ds-2001711/",  # Ds 2001:71
        "http://www.regeringen.se/rattsdokument/departementsserien-och-promemorior/2000/01/ds-2000681/",  # Ds 2000:68
        "http://www.regeringen.se/rattsdokument/departementsserien-och-promemorior/1999/01/ds-1999241/",  # Ds 1999:24 -- in english
        "http://www.regeringen.se/rattsdokument/departementsserien-och-promemorior/1998/01/ds-1998141/",  # Ds 1998:14
        "http://www.regeringen.se/rattsdokument/departementsserien-och-promemorior/2015/12/"
        "andringar-i-rennaringsforordningen-1993384/",  # mistaken for a DS when it's really a unpublished PM
        "http://www.regeringen.se/rattsdokument/departementsserien-och-promemorior/2015/12/"
        "andring-av-bestammelserna-om-ratt-till-bistand-i-lagen-1994137-om-mottagande-av-asylsokande-m.fl/", # same
        "http://www.regeringen.se/rattsdokument/proposition/2018/01/sou-2071883/", # looks like 2071/88:3, but should be 2017/18:83 (and also not SOU!)
        "http://www.regeringen.se/rattsdokument/statens-offentliga-utredningar/1921/04/overenskommelse-med-danmark-angaende-ordnandet-av-post--befordringen-mellan-malmo-och-kopenhamn4/", # SÖ, not SOU
        "http://www.regeringen.se/rattsdokument/statens-offentliga-utredningar/1921/04/overenskommelse-med-danmark-angaende-ordnandet-av-post--befordringen-mellan-malmo-och-kopenhamn3/", # SÖ, not SOU
        "http://www.regeringen.se/rattsdokument/statens-offentliga-utredningar/1921/04/overenskommelse-med-danmark-angaende-ordnandet-av-post--befordringen-mellan-malmo-och-kopenhamn2/", # SÖ, not SOU
        "http://www.regeringen.se/rattsdokument/statens-offentliga-utredningar/1921/04/overenskommelse-med-danmark-angaende-ordnandet-av-post--befordringen-mellan-malmo-och-kopenhamn1/", # SÖ, not SOU
        "http://www.regeringen.se/rattsdokument/statens-offentliga-utredningar/1921/04/overenskommelse-med-danmark-angaende-ordnandet-av-post--befordringen-mellan-malmo-och-kopenhamn/", # SÖ, not SOU
        "http://www.regeringen.se/rattsdokument/statens-offentliga-utredningar/1921/05/skiljedomskonvention-med-brasiliens-forenta-stater/", # SÖ, not SOU
        "http://www.regeringen.se/rattsdokument/statens-offentliga-utredningar/1921/07/internationell-konvention-rorande-upprattandet-i-paris-av-ett-internationellt-frysinstitut/", # SÖ, not SOU
        "http://www.regeringen.se/rattsdokument/statens-offentliga-utredningar/1921/10/noter-med-egypten-angaende-forlangning-av-de-blandade-domstolarnas-verksamhet-m.-m/", # SÖ, not SOU
        "http://www.regeringen.se/rattsdokument/statens-offentliga-utredningar/1921/07/ministeriella-noter-vaxlade-med-italien-angaende-omsesidighet-rorande-ersattning-for-olycksfall-i-arbete/", # SÖ, not SOU
        "http://www.regeringen.se/rattsdokument/statens-offentliga-utredningar/1921/10/konvention-angaende-faststallande-av-minimialder-for-barns-anvandande-i-arbete-till-sjoss/", # SÖ, not SOU
        "https://www.regeringen.se/rattsliga-dokument/proposition/2018/01/sou-2071883", # missing a 1, leading to the interpretation prop. 2071/88:3 instead of 2017/18:83
        "https://www.regeringen.se/rattsliga-dokument/kommittedirektiv/2017/04/dir.-201645/", # is Dir. 2017:45, not 2016:45
        "https://www.regeringen.se/rattsliga-dokument/departementsserien-och-promemorior/2015/11/andring-av-en-avvisningsbestammelse-i-utlanningslagen-2005716/", # no ds, gets incorrect id from a SFS quoted in the title
        "http://www.regeringen.se/rattsliga-dokument/proposition/2018/01/sou-2071883/" # is 2017/18, not 2017/72
     ])
                    
    def attribs_from_url(self, url):
        # The RSS feeds from regeringen.se does not contain textual
        # information about the identifier (eg. "Prop. 2015/16:64") of
        # each document. However, the URL
        # (eg. http://www.regeringen.se/rattsdokument/proposition/2015/12/prop.-20151664/
        # often contains the same information. But not always...
        year = ordinal = None
        m = self.re_urlbasefile_strict.search(url)
        if m and (1900 < int(m.group(1)[:4]) < 2100):
            (year, ordinal) = m.groups()
        else:
            m = self.re_urlbasefile_lax.search(url)
            if m: 
                (year, ordinal) = m.groups()
                year = year.replace("_", "")
        if year and ordinal and url not in self.misleading_urls and int(year[:4]) <= datetime.now().year:  # make sure a misleading url doesn't result in eg year 2071
            return {'rdf:type': self.urispace_segment.split("/")[-1],
                    'rpubl:arsutgava': year,
                    'rpubl:lopnummer': ordinal}
        elif url in self.urlmap:
            identifier = [self.urlmap[url]]
            doclabels = []
        else:
            self.log.warning("Can't find out doc attribs from url %s itself, downloading it..." % url)
            soup = BeautifulSoup(self.session.get(url).text, "lxml")
            identifier = []
            identifier_node = soup.find("span", "h1-vignette")
            if identifier_node:
                identifier = [identifier_node.text]
                self.urlmap[url] = identifier_node.text
            else:
                self.urlmap[url] = None
            doclabels = [x[2] for x in self.find_doc_links(soup, None)]
        for candidate in identifier + doclabels:
            m = self.re_basefile_strict.search(candidate)
            if m: 
                (year, ordinal) = m.group(1).split(":")
            else:
                m = self.re_basefile_lax.search(candidate)
                if m:
                    (year, ordinal) = m.group(1).split(":")
            if year and ordinal:
                return {'rdf:type': self.urispace_segment.split("/")[-1],
                        'rpubl:arsutgava': year,
                        'rpubl:lopnummer': ordinal}
        raise ValueError("Can't find doc attribs from either url %s or the page at that url" % url)

    @downloadmax
    def download_get_basefiles(self, params):
        done = False
        yielded = set()
        while not done:
            qsparams = urlencode(params)
            searchurl = self.start_url + "?" + qsparams
            self.log.debug("Loading page #%s" % params.get('page', 1))
            try:
                resp = util.robust_fetch(self.session.get, searchurl, self.log)
            except requests.exceptions.HTTPError as e:
                assert e.response.status_code == 404
                done = True
                continue
            tree = lxml.etree.fromstring(resp.text)
            done = True
            for item in tree.findall(".//item"):
                done = False
                url = item.find("link").text
                if item.find("title") is not None and item.find("title").text.endswith(("(lättläst)",
                                                                            "(engelsk sammanfattning)")):
                    self.log.debug("%s ('%s') is probably not the real doc" % (url, item.find("title").text))
                try:
                    attribs = self.attribs_from_url(url)
                    basefile = "%s:%s" % (attribs['rpubl:arsutgava'], attribs['rpubl:lopnummer'])
                    try:
                        basefile = self.sanitize_basefile(basefile)
                    except AssertionError: # if the basefile is just plain wrong
                        continue
                    self.log.debug("%s: <- %s" % (basefile, url))
                    if basefile not in yielded: # just in case two or
                                                # more URLs resolve to
                                                # the same basefile
                                                # (like the original
                                                # and an english
                                                # translation), go
                                                # with the first one.
                        if self.get_parse_options(basefile) != "skip":
                            yield basefile, url
                        else:
                            self.log.debug("%s: Marked as 'skip' in options.py" % basefile)
                            
                        yielded.add(basefile)
                except ValueError as e:
                    self.log.error(e)
            params['page'] = params.get('page', 1) + 1


    def download_single(self, basefile, url=None):
        if self.get_parse_options(basefile) == "skip":
            raise DocumentSkippedError("%s should not be downloaded according to options.py" % basefile)
        if not url:
            url = self.remote_url(basefile)
            if not url:  # remote_url failed
                return
        filename = self.store.downloaded_path(basefile)  # just the html page
        updated = filesupdated = False
        created = not os.path.exists(filename)
        if (not os.path.exists(filename) or self.config.refresh):
            existed = os.path.exists(filename)
            try:
                updated = self.download_if_needed(url, basefile, filename=filename)
            except requests.exceptions.HTTPError as e:
                if e.response.status_code == 400:
                    # regeringen.se seems to have a problem with the
                    # first req after a search -- unless slowed down,
                    # raises a 400 error. Sleep on it, and try once more
                    sleep(5)
                    updated = self.download_if_needed(url, basefile, filename=filename)
                else:
                    raise
            docid = url.split("/")[-1]
            if existed:
                if updated:
                    self.log.info("%s: updated from %s" % (basefile, url))
                else:
                    self.log.debug("%s: %s is unchanged, checking PDF files" %
                                   (basefile, filename))
            else:
                self.log.info("%s: download OK from %s" % (basefile, url))

            if self.get_parse_options(basefile) == "metadataonly":
                self.log.debug("%s: Marked as 'metadataonly', not downloading actual PDF file" % basefile)
            else:
                soup = BeautifulSoup(codecs.open(filename, encoding=self.source_encoding), "lxml")
                cnt = 0
                selected_files = self.find_doc_links(soup, basefile)
                if selected_files:
                    for (filename, filetype,label) in selected_files:
                        fileurl = urljoin(url, filename)
                        basepath = filename.split("/")[-1]
                        filename = self.store.downloaded_path(basefile, attachment=basepath)
                        if not filename.lower().endswith(".pdf"):
                            filename += ".%s" % filetype
                        if self.download_if_needed(fileurl, basefile, filename=filename):
                            filesupdated = True
                            self.log.debug(
                                "    %s is new or updated" % filename)
                        else:
                            self.log.debug("    %s is unchanged" % filename)
                else:
                    self.log.warning(
                        "%s (%s) has no downloadable files" % (basefile, url))
            if updated or filesupdated:
                pass
            else:
                self.log.debug("%s and all files are unchanged" % filename)
        else:
            self.log.debug("%s: %s already exists" % (basefile, filename))

        entry = DocumentEntry(self.store.documententry_path(basefile))
        now = datetime.now()
        entry.orig_url = url
        if created:
            entry.orig_created = now
        if updated or filesupdated:
            entry.orig_updated = now
        entry.orig_checked = now
        entry.save()

        return updated or filesupdated

    def sanitize_metadata(self, a, basefile):
        # trim space
        for k in ("dcterms:title", "dcterms:abstract"):
            if k in a:
                a[k] = util.normalize_space(a[k])
        # trim identifier
        try:
            # The identifier displayed on the HTML page is not always
            # correct -- it might be missing digits (eg "SOU 207:111"
            # instead of "SOU 2017:111"). Try to sanitize it, but if
            # we fail, infer it from our basefile instead.
            a["dcterms:identifier"] = self.sanitize_identifier(
                a["dcterms:identifier"].replace("ID-nummer: ", ""))
        except ValueError as e:
            inferred_identifier = str(self.infer_identifier(basefile))
            self.log.warning("%s: Irregular identifier %s, using inferred identifier %s instead" % (basefile, a["dcterms:identifier"], inferred_identifier))
            a["dcterms:identifier"] = inferred_identifier
        # save for later
        self._identifier = a["dcterms:identifier"]
        # it's rare, but in some cases a document can be published by
        # two different departments (eg dir. 2011:80). Convert string
        # to a list in these cases (SwedishLegalSource.polish_metadata
        # will handle that)
        if "rpubl:departement" in a and ", " in a["rpubl:departement"]:
            a["rpubl:departement"] = a["rpubl:departement"].split(", ")
        # remove empty utgarFran list
        if a["rpubl:utgarFran"]:
            a["rpubl:utgarFran"] = [URIRef(x) for x in a["rpubl:utgarFran"]]
        else:
            del a["rpubl:utgarFran"]

        # FIXME: possibly derive utrSerie from self.document_type?
        if self.rdf_type == RPUBL.Utredningsbetankande:
            altlabel = "SOU" if self.document_type == Regeringen.SOU else "Ds"
            a["rpubl:utrSerie"] = self.lookup_resource(altlabel, SKOS.altLabel)
        return a


    def extract_head(self, fp, basefile):
        soup = BeautifulSoup(fp.read(), "lxml")
        self._rawbody = soup.body
        return self._rawbody.find(id="content")

    def extract_metadata(self, rawhead, basefile):
        content = rawhead
        title = list(content.find("h1").children)[0].string.strip()
        # in some cases, the <h1> is not a real title but rather an
        # identifier. Do a simple sanity check for this
        # the longest possible id, "Prop. 1999/2000:123", is 19 chars
        if len(title) < 20 and title.endswith(basefile):
            identifier = title
            title = ""
            # maybe the real title is hiding in the ingress of the page?
            alttitle = content.find("p", "ingress")
            if alttitle:
                alttitle = alttitle.text.strip()
                # some basic heuristics to determine if this is likely to be a title
                if alttitle.startswith("Tilläggsdirektiv") or len(alttitle) > 120:
                    title = alttitle
        else:
            identifier_node = content.find("span", "h1-vignette")
            if identifier_node:
                identifier = identifier_node.text.strip()
            else:
                identifier = ""  # infer_metadata calls
                                 # infer_identifier if this is falsy,
                                 # which will be good enough. No need
                                 # to warn.
                
        # the <time> element has a datetime attrib with
        # machine-readable timestamp, but this has unwarranted high
        # precision. We'd like to capture just a xsd:YearMonth if
        # "Publicerad: April 1994", not a datetime(1994, 4, 1, 0, 0,
        # 0). So we just grab text and use parse_swedish_date later
        # on.
        utgiven = content.find("span", "published").time.text
        
        # find ansvarig departement
        try:
            ansvarig = content.find("p", "media--publikations__sender").find("a", text=re.compile("departement", re.I)).text
        except AttributeError:
            if self.rdf_type != RPUBL.Kommittedirektiv:  # postprocess_doc has us covered
                self.log.warning("%s: No ansvarig departement found" % basefile)
            ansvarig = None
        s = content.find(("div","p"), "has-wordExplanation")
        for a in s.find_all("a"):  # links in summary are extra tacked-on bogus
            a.decompose()
        sammanfattning = " ".join(s.strings)

        # look for related items. We need to import some subclasses of
        # this class in order to do that, which we need to do inline.
        from .direktiv import DirRegeringen
        from .ds import Ds
        from .sou import SOURegeringen
        linkfrags = {self.KOMMITTEDIREKTIV: [],
                     self.DS: [("/komittedirektiv/", DirRegeringen)],
                     self.PROPOSITION: [("/kommittedirektiv/", DirRegeringen),
                                        ("/departementsserien-och-promemorior/", Ds),
                                        ("/statens-offentliga-utredningar", SOURegeringen)],
                     self.SOU: [("/kommittedirektiv/", DirRegeringen)]
        }[self.document_type]
        utgarFran = []
        island = content.find("div", "island")
        for linkfrag, cls in linkfrags:
            inst = cls(datadir=self.config.datadir)
            regex = re.compile(".*"+linkfrag)
            for link in island.find_all("a", href=regex):
                # from a relative link on the form
                # "/rattsdokument/kommittedirektiv/2012/01/dir.-20123/",
                # extract
                # {'rdf:type': RPUBL.Kommittedirektiv,
                #  'rpubl:arsutgava': 2012,
                #  'rpubl:lopnummer} -> attributes_to_resource -> coin_uri
                try:
                    attribs = inst.attribs_from_url(urljoin(inst.start_url, link["href"]))
                except ValueError:
                    self.log.warning("%s: Can't find out properties for linked resource %s" % (basefile, link["href"]))
                    continue
                if attribs["rdf:type"] == "dir":
                    attribs["rdf:type"] = RPUBL.Kommittedirektiv
                else:
                    # lookup on skos:altLabel, but with "Ds" and "SOU"
                    # as keys (not "ds" and "sou")
                    altlabel = attribs["rdf:type"].upper() if attribs["rdf:type"] == "sou" else attribs["rdf:type"].capitalize()
                    attribs["rpubl:utrSerie"] = self.lookup_resource(altlabel, SKOS.altLabel)
                    attribs["rdf:type"] = RPUBL.Utredningsbetankande
                uri = self.minter.space.coin_uri(self.attributes_to_resource(attribs))
                utgarFran.append(uri)
        a = self.metadata_from_basefile(basefile)
        a.update({'dcterms:title': title,
                  'dcterms:identifier': identifier,
                  'dcterms:issued': utgiven,
                  'dcterms:abstract': sammanfattning,
                  'rpubl:utgarFran': utgarFran,
        })
        if ansvarig:
            a['rpubl:departement'] = ansvarig
        return a


    def polish_metadata(self, sane_attribs, basefile, infer_nodes=True):
        resource = super(Regeringen, self).polish_metadata(sane_attribs, basefile, infer_nodes)
        # FIXME: This is hackish -- need to rethink which parts of the
        # parse steps needs access to which data
        self._resource = resource
        return resource


    def sanitize_body(self, rawbody):
        sanitized = super(Regeringen, self).sanitize_body(rawbody)
        # Sanitize particular files with known issues
        if hasattr(rawbody, 'filename'):
            if rawbody.filename.endswith("2015-16/83/151608300webb.pdf"):
                for page in rawbody:
                    # remove incorrectly placed "Bilaga 1" markers from p 4 - 11
                    if 3 < page.number < 12:
                        for box in page:
                            if str(box).strip() == "Bilaga 1":
                                page.remove(box)
                                break
            elif rawbody.filename.endswith("2015-16/78/ett-sarskilt-straffansvar-for-resor-i-terrorismsyfte-prop.-20151678.pdf"):
                # page 74 is some sort of placeholder before an
                # external appendix, and it causes off-by-one
                # pagination in the rest of the appendicies. removing
                # it fixes pagination and left/right pages. we should
                # check the printed version, really
                del rawbody[73]
                for page in rawbody[73:]:
                    page.number -= 1
            elif rawbody.filename.endswith("2015-16/195/nytt-regelverk-om-upphandling-del-1-av-4-kapitel-1-21-prop.-201516195.pdf"):
                for page in rawbody:
                    # also, we need to transpose each textbox so that
                    # margins line up with the first PDF
                    if "del-2-av-4" in page.src or "del-3-av-4" in page.src:
                        # rawbody is constructed from 4 physical PDFs,
                        # some of which are cropped differently from
                        # the first. Unify.
                        page.width = 892
                        page.height = 1263
                        for textbox in page:
                            # 79 should be 172: +93
                            # 79 + 450 should be 172 + 450: +93
                            # 185 should be 278: +93
                            textbox.left += 93
                            textbox.right += 93
        try:
            sanitized.analyzer = self.get_pdf_analyzer(sanitized)
        except AttributeError:
            if isinstance(sanitized, list):
                pass  # means that we use a placeholder text instead
                      # of a real document
            else:
                raise
        return sanitized


    placeholders = set([(PROPOSITION, "2015/16:47"),
                        (PROPOSITION, "2015/16:99")])

    def extract_body(self, fp, basefile):
        if ((self.document_type == self.PROPOSITION and
             basefile.split(":")[1] in ("1", "100")) or
            (self.document_type, basefile) in self.placeholders):
            self.log.warning("%s: Will only process metadata, creating"
                             " placeholder for body text" % basefile)
            # means vår/höstbudget. Create minimal placeholder text
            return ["Dokumentttext saknas (se originaldokument)"]
        # reset global state
        PreambleSection.counter = 0
        UnorderedSection.counter = 0
        
        filenames = [f + ("" if f.lower().endswith(".pdf") else "."+t) for f,t,l in self.find_doc_links(self._rawbody, basefile)]
        if not filenames:
            self.log.error(
                "%s: No PDF documents found, can't parse anything (returning placeholder)" % basefile)
            return ["[Dokumenttext saknas]"]
        
        # read a list of pdf files and return a contatenated PDFReader
        # object (where do we put metrics? On the PDFReader itself?
        return self.read_pdfs(basefile, filenames, self._identifier)


    def sourcefiles(self, basefile, resource=None):
        with self.store.open_downloaded(basefile, "rb") as fp:
            soup = BeautifulSoup(fp.read(), "lxml")
        # FIXME: We might want to trim the labels here, eg to shorten
        # "En digital agenda, SOU 2014:13 (del 2 av 2) (pdf 1,4 MB)"
        # to something more display friendly.
        return [(f, l) for (f, t, l) in self.find_doc_links(soup, basefile)]

    def source_url(self, basefile):
        # this source does not have any predictable URLs, so we try to
        # find if we made a note on the URL when we ran download()
        entry = DocumentEntry(self.store.documententry_path(basefile))
        return entry.orig_url

    def find_doc_links(self, soup, basefile):
        files = []
        docsection = soup.find('ul', 'list--Block--icons')
        filelink = re.compile("/(contentassets|globalassets)/")
        if docsection:
            for link in docsection.find_all("a", href=filelink):
                files.append((link["href"], link.string))
        selected = self.select_files(files)
        self.log.debug(
            "selected %s out of %d files" %
            (", ".join([x[0] for x in selected]), len(files)))
        return selected

    def select_files(self, files):
        """Given a list of (fileurl, linktext) tuples, return only the
        file/those files that make up the main document that make we
        need to parse (by filtering out duplicates, files not part of
        the main document etc).

        The files are returned as a list of (fileurl, filetype, label)
        tuples.

        """
        def guess_type(label):
            return "doc" if "(doc " in label else "pdf"
            
        # NOTE: Any change to this logic should add a new testcase to
        # test/integrationRegeringen.SelectFiles
        cleanfiles = []

        # FIXME: For some documents, the split into different document
        # parts is just too difficult to determine automatically. Eg
        # SOU 2016:32, which has 3 docs but only the first one (which
        # contains the second one in it's entirety, even though you
        # can't tell) should be selected...
        # 1. Simplest case: One file obviously contains all of the text
        for filename, linktext in files:
            if "hela dokumentet" in linktext or "hela betänkandet" in linktext:
                # we're immediately done
                return [(filename, guess_type(linktext), linktext)]


        # 2. Filter out obviously extraneous files
        for filename, linktext in files:
            if (linktext.startswith(("Sammanfattning ", "Remisslista", "Remissammanställning",
                                     "Sammanställning över remiss",
                                     "Utredningens pressmeddelande", "Rättelseblad")) or
                "emissinstanser" in linktext or
                "lättläst version" in linktext):
                pass  # don't add to cleanfiles
            else:
                cleanfiles.append((filename, linktext))

        # 3. Attempt to see if we have one complete file + several
        # files with split-up content
        linktexts = [x[1] for x in cleanfiles]
        commonprefix = os.path.commonprefix(linktexts)
        if commonprefix == "" and len(cleanfiles) > 2:
            # try again without the last file
            commonprefix = os.path.commonprefix(linktexts[:-1])
            if commonprefix:
                # last file is probably safe to skip
                linktexts = linktexts[:-1]
                cleanfiles = cleanfiles[:-1]
        if commonprefix:
            for filename, linktext in cleanfiles:
                # strip away the last filetype + size paranthesis
                linktext = re.sub(" \((pdf|doc) [\d\,]+ [kM]B\)", "", linktext)
                # and if we remove the commonprefix, do we end up with
                # nothing (or something identifier-like)?
                remainder = linktext.replace(commonprefix, "")
                if remainder == "" or re.match(r"(SOU|Ds|Prop\.?) \d+(|/\d+):\d+$", remainder):
                    # then this is probably a complete file
                    return [(filename, guess_type(linktext), linktext)]

        if commonprefix.endswith(" del "):
            parts = set()
            cleanerfiles = []
            # only return unique parts (ie only the first "del 1", not any other
            # special versions of "del 1"
            for filename, linktext in cleanfiles:
                remainder = linktext.replace(commonprefix, "")
                part = remainder[0]  # assume max 9 parts
                if part in parts:
                    continue
                cleanerfiles.append((filename, linktext))
                parts.add(part)
            cleanfiles = cleanerfiles

        # add filetype information based on labels (basically, assume
        # pdf if not obviously doc)
        cleanfiles = [(f, guess_type(l), l) for (f, l) in cleanfiles]
        return cleanfiles


    # returns a concatenated PDF reader containing all sub-PDF readers.
    def read_pdfs(self, basefile, files, identifier=None):
        reader = None
        mapping = {}
        for filename in files:
            basepath = filename.split("/")[-1]
            pdf_path = self.store.downloaded_path(basefile,
                                                  attachment=basepath)
            intermed_path = self.store.intermediate_path(basefile,
                                                         attachment=basepath)
            intermediate_dir = os.path.dirname(intermed_path)
            subreader = self.parse_pdf(pdf_path, intermediate_dir, basefile)
            if not reader:
                reader = subreader
            else:
                reader += subreader
        return reader

    def parse_pdf(self, filename, intermediatedir, basefile):
        # By default, don't create and manage PDF backgrounds files
        # (takes forever, we don't use them yet)
        if self.config.compress == "bz2":
            keep_xml = "bz2"
        else:
            keep_xml = True
        tup = (self.document_type, basefile)
        default_decoder = (DetectingDecoder, None)
        # This just just a list of known different encoding
        # schemes. FIXME: try to find out whether all Ds documents should
        # use the (non-decoding) BaseTextDecoder
        alternate_decoders = {(self.PROPOSITION, "1997/98:44"): (OffsetDecoder20, "Datalagskommittén"),
                              (self.DS, "2004:46"): (BaseTextDecoder, None)}

        decoding_class, decoder_arg = alternate_decoders.get(tup, default_decoder)
        convert_to_pdf = not filename.lower().endswith(".pdf")
        pdf = PDFReader(filename=filename,
                        workdir=intermediatedir,
                        images=self.config.pdfimages,
                        convert_to_pdf=convert_to_pdf,
                        keep_xml=keep_xml,
                        textdecoder=decoding_class(decoder_arg))
        if pdf.is_empty():
            self.log.warning("PDF file %s had no textcontent, trying OCR" % filename)
            pdf = PDFReader(filename=filename,
                            workdir=intermediatedir,
                            images=self.config.pdfimages,
                            keep_xml=keep_xml,
                            ocr_lang="swe")
        identifier = self.canonical_uri(basefile)
        for page in pdf:
            page.src = filename
        return pdf

    def toc(self, otherrepos=None):
        self.log.debug(
            "Not generating TOC (let ferenda.sources.legal.se.Forarbeten do that instead")
        return

    def tabs(self):
        if self.config.tabs:
            label = {self.DS: "Ds:ar",
                     self.KOMMITTEDIREKTIV: "Kommittédirektiv",
                     self.PROPOSITION: "Propositioner",
                     self.SOU: "SOU:er"}.get(self.document_type, "Förarbete")
            return [(label, self.dataset_uri())]
        else:
            return []
