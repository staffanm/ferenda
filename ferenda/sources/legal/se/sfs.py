# -*- coding: utf-8 -*-
from __future__ import unicode_literals, print_function

# system libraries
from collections import defaultdict, OrderedDict
from datetime import datetime, date
from time import time
import codecs
import logging
import os
import re
import sys

from html.parser import HTMLParser
from urllib.parse import quote, unquote

# 3rdparty libs
from rdflib import URIRef, Literal, RDF, Graph, BNode
from rdflib.namespace import DCTERMS, SKOS, RDFS
from lxml import etree
from bs4 import BeautifulSoup
import requests
import requests.exceptions
from layeredconfig import LayeredConfig
from cached_property import cached_property

# my own libraries
from ferenda import DocumentEntry, TripleStore
from ferenda import TextReader, Facet
from ferenda.sources.legal.se import legaluri
from ferenda import util
from ferenda.elements.html import UL, LI, Body
from ferenda.errors import FerendaException, DocumentRemovedError, ParseError
from .legalref import LegalRef, LinkSubject
from . import Trips, SwedishCitationParser, RPUBL, SwedishLegalStore, RINFOEX
from .elements import *


class UpphavdForfattning(DocumentRemovedError):
    """Raised when an act that is parsed is determined to be expired. The
    setting config.keepexpired controls whether these exceptions are
    thrown.

    """
    # FIXME: Those checks occur in several places:
    # extract_metadata_header, extract_metadata_register and
    # download_to_intermediate, with varying amounts of completeness
    # and error handling


class InteUppdateradSFS(FerendaException):
    """Raised whenever SFSR indicates that a base SFS has been updated,
    but SFST doesn't reflect this.

    """
    pass


class InteExisterandeSFS(DocumentRemovedError):
    """Raised when a HTML page that should contain the text of an statute
    instead contains an error message saying that no such document
    exists. This happens because the search results occasionally
    contain such links. A common case seem to be a search result
    appearing to be a base SFS, but the SFS number really refers to a
    change SFS of some other base SFS.

    """
    # FIXME: This is raised in extract_head and download_base_sfs
    # (only called when doing updating download, not full refresh). It
    # should probably be raised in download_single as well (and
    # possibly not in extract_head)
    
class SFSDocumentStore(SwedishLegalStore):

    # some extra methods for SFSR pages and semi-hidden metadata pages. 
    # FIXME: These should probably be handled as attachments instead of custom methods, even if that 
    # means we need to set storage_policy = "dir"
    def register_path(self, basefile):
        return self.path(basefile, "register", ".html")

    def open_register(self, basefile, mode="r"):
        filename = self.register_path(basefile)
        return self._open(filename, mode)

    def metadata_path(self, basefile):
        return self.path(basefile, "metadata", ".html")

    # Override to ensure that intermediate files use .txt suffix (not .xml)
    def intermediate_path(self, basefile, version=None, attachment=None):
        return self.path(basefile, "intermediate", ".txt")


class SFS(Trips):

    """Handles consolidated (codified) versions of statutes from SFS
    (Svensk f\xf6rfattningssamling).
    """

    # A note about logging:
    # 
    # There are four additional loggers available ('paragraf', 'tabell',
    # 'numlist' and 'rubrik'). By default, manager.py turns them off
    # unless config.trace.[logname] is set. Do something like
    #
    # ./ferenda-build.py sfs parse 2009:924 --force --sfs-trace-rubrik
    # 
    # (sets the sfs.rubrik logger level to DEBUG) or
    #
    # ./ferenda-build.py sfs parse 2009:924 --force --sfs-trace-tabell=INFO

    alias = "sfs"
    rdf_type = RPUBL.KonsolideradGrundforfattning
    parse_types = LegalRef.LAGRUM, LegalRef.EULAGSTIFTNING
    parse_allow_relative = True
    app = "sfst"  # dir, prop, sfst
    base = "SFSR"  # DIR, THWALLPROP, SFSR
    # This must be pretty lax, basefile is sanitized later
    basefile_regex = "(?P<basefile>\d{4}:(bih. ?|)\d+( ?s\. ?\d+| \d|))$"
    # start_url = "http://rkrattsbaser.gov.se/sfsr/adv?sort=asc"
    start_url = "http://rkrattsbaser.gov.se/sfsr/adv?upph=false&sort=asc"
    document_url_template = "http://rkrattsbaser.gov.se/sfst?bet=%(basefile)s"
    document_sfsr_url_template = "http://rkrattsbaser.gov.se/sfsr?bet=%(basefile)s"
    document_sfsr_change_url_template = "http://rkrattsbaser.gov.se/sfsr?%%C3%%A4bet=%(basefile)s"
    xslt_template = "xsl/sfs.xsl"
    max_resources = 2500  # SFS 2010:110 currently has 2063 legitimate subresources
    documentstore_class = SFSDocumentStore

    def __init__(self, config=None, **kwargs):
        super(SFS, self).__init__(config, **kwargs)
        self.current_section = '0'
        self.current_headline_level = 0  # 0 = unknown, 1 = normal, 2 = sub

        from ferenda.manager import loglevels
        self.trace = {}
        for logname in ('paragraf', 'tabell', 'numlist', 'rubrik'):
            self.trace[logname] = logging.getLogger('%s.%s' %
                                                    (self.alias, logname))
            if 'trace' in self.config:
                if logname in self.config.trace:
                    loglevel = getattr(self.config.trace, logname)
                    if loglevel is True:
                        loglevel = logging.DEBUG
                    else:
                        loglevel = loglevels[loglevel]
                    self.trace[logname].setLevel(loglevel)
            else:
                # shut up logger
                self.trace[logname].propagate = False

    @cached_property
    def lagrum_parser(self):
        return SwedishCitationParser(LegalRef(LegalRef.LAGRUM,
                                              LegalRef.EULAGSTIFTNING),
                                     self.minter,
                                     self.commondata,
                                     allow_relative=True)

    @cached_property
    def forarbete_parser(self):
        return SwedishCitationParser(LegalRef(LegalRef.FORARBETEN),
                                     self.minter,
                                     self.commondata)

    @classmethod
    def get_default_options(cls):
        opts = super(SFS, cls).get_default_options()
        opts['keepexpired'] = False
        opts['revisit'] = list
        opts['next_sfsnr'] = str
        opts['shortdesclen'] = 200  # how many (markup) characters of Författningskommentar to include
        if 'cssfiles' not in opts:
            opts['cssfiles'] = []
        opts['cssfiles'].append('css/sfs.css')
        return opts

    def download(self, basefile=None):
        if basefile:
            ret = self.download_single(basefile)
        # following is copied from supers' download
        elif self.config.refresh or ('next_sfsnr' not in self.config):
            ret = super(SFS, self).download(basefile)
            self._set_last_sfsnr()
        else:
            # in this case, super().download is never called so we'll
            # have to make sure this runs anyway:
            if self.config.ipbasedurls:
                self._make_ipbasedurls()
            ret = self.download_new()
        return ret

    def _set_last_sfsnr(self, last_sfsnr=None):
        maxyear = datetime.today().year
        if not last_sfsnr:
            self.log.info("Setting last SFS-nr")
            last_sfsnr = "1600:1"
            # for f in util.list_dirs("%s/sfst" % self.download_dir, ".html"):
            for basefile in self.store.list_basefiles_for("parse"):
                f = self.store.downloaded_path(basefile)
                tmp = self._find_uppdaterad_tom(basefile, f)
                tmpyear = int(tmp.split(":")[0])
                if tmpyear > maxyear:
                    self.log.warning('%s is probably not correct, '
                                     'ignoring (%s)' % (tmp, basefile))
                    continue
                if util.numcmp(tmp, last_sfsnr) > 0:
                    self.log.info('%s > %s (%s)' % (tmp, last_sfsnr, basefile))
                    last_sfsnr = tmp
        self.config.next_sfsnr = last_sfsnr
        LayeredConfig.write(self.config)

    def download_new(self):
        if 'next_sfsnr' not in self.config:
            self._set_last_sfsnr()
        (year, nr) = [int(
            x) for x in self.config.next_sfsnr.split(":")]
        done = False
        revisit = []
        if 'revisit' in self.config and self.config.revisit:
            last_revisit = self.config.revisit
            for wanted_sfs_nr in last_revisit:
                self.log.info('Revisiting %s' % wanted_sfs_nr)
                try:
                    self.download_base_sfs(wanted_sfs_nr)
                except InteUppdateradSFS:
                    revisit.append(wanted_sfs_nr)

        peek = False
        last_sfsnr = self.config.next_sfsnr
        while not done:
            # first do all of last_revisit, then check the rest...
            wanted_sfs_nr = '%s:%s' % (year, nr)
            try:
                self.download_base_sfs(wanted_sfs_nr)
                last_sfsnr = wanted_sfs_nr
            except InteUppdateradSFS:
                revisit.append(wanted_sfs_nr)
            except (InteExisterandeSFS, requests.exceptions.HTTPError):
                # try peeking at next number, or maybe next year, and
                # if none are there, we're done
                if not peek:
                    peek = True
                    self.log.debug(
                        'Peeking forward for %s:%s' %
                        (year, nr + 1))  # increments below
                elif datetime.today().year > year:
                    peek = False
                    year = datetime.today().year
                    nr = 0  # increments below, actual downloading occurs next loop
                else:
                    done = True
            nr = nr + 1

        self._set_last_sfsnr(last_sfsnr)
        self.config.revisit = revisit
        LayeredConfig.write(self.config)

    def download_base_sfs(self, wanted_sfs_nr):
        self.log.debug('Looking for %s' % wanted_sfs_nr)
        (year, nr) = [int(x) for x in wanted_sfs_nr.split(":", 1)]
        base_sfsnr_list = self._check_for_sfs(year, nr)
        if base_sfsnr_list:
            # usually only a 1-elem list
            for base_sfsnr in base_sfsnr_list:
                self.download_single(base_sfsnr)
                # get hold of uppdaterad_tom from the
                # just-downloaded doc
                filename = self.store.downloaded_path(base_sfsnr)
                uppdaterad_tom = self._find_uppdaterad_tom(base_sfsnr,
                                                           filename)
                if base_sfsnr_list[0] == wanted_sfs_nr:
                    # initial grundf\xf6rfattning - varken
                    # "Uppdaterad T.O.M. eller "Upph\xe4vd av" ska
                    # vara satt
                    pass
                elif util.numcmp(uppdaterad_tom, wanted_sfs_nr) < 0:
                    # the "Uppdaterad T.O.M." field is outdated --
                    # this is OK only if the act is revoked (upphavd)
                    if self._find_upphavts_genom(filename):
                        self.log.debug("    Text only updated to %s, "
                                       "but slated for revocation by %s" %
                                       (uppdaterad_tom,
                                        self._find_upphavts_genom(filename)))
                    else:
                        self.log.warning("    Text in %s updated to %s, not %s" %
                                         (base_sfsnr, uppdaterad_tom, wanted_sfs_nr))
                        raise InteUppdateradSFS(wanted_sfs_nr)
        else:
            raise InteExisterandeSFS(wanted_sfs_nr)

    def _check_for_sfs(self, year, nr):
        """Givet ett SFS-nummer, returnera en lista med alla
        SFS-numret f\xf6r dess grundf\xf6rfattningar. Normalt sett har en
        \xe4ndringsf\xf6rfattning bara en grundf\xf6rfattning, men f\xf6r vissa
        (exv 2008:605) finns flera. Om SFS-numret inte finns alls,
        returnera en tom lista."""
        # Titta f\xf6rst efter grundf\xf6rfattning
        self.log.debug('    Looking for base act')
        grundforf = []
        basefile = "%s:%s" % (year, nr)
        url = self.document_sfsr_url_template % {'basefile': basefile}
        text = requests.get(url).text
        # FIXME: If the result page contains "Totalt \d tr\xe4ffar", we
        # should parse it to find the correct URL (will have a post_id
        # parameter)
        if ("<div>Inga tr\xe4ffar</div>" not in text and
            not re.search("Totalt <strong>\d+</strong> tr\xe4ffar", text)):
            grundforf.append("%s:%s" % (year, nr))
            return grundforf

        # Sen efter \xe4ndringsf\xf6rfattning
        self.log.debug('    Looking for change act')
        url = self.document_sfsr_change_url_template % {'basefile': basefile}
        text = requests.get(url).text
        # NB: Right now a search for \xe4bet=2016:1 will return all base
        # acts changed by any act *starting* with 2016:1. This means
        # this search will never work right with one-or two digit
        # ordinals. Bug filed with RK.
        if ("<div>Inga tr\xe4ffar</div>" in text or
            re.search("Totalt <strong>\d+</strong> tr\xe4ffar", text)):
            self.log.debug('    Found no change act')
            return grundforf

        m = re.search('<a href="/sfst?bet=([^"]+)"', text)
        if m:
            grundforf.append(m.groups(1))
            self.log.debug('    Found change act (to %s)' %
                           m.groups(1))
            return grundforf
        else:
            # If a single change act changed multiple base acts. This
            # is very rare and we don't handle this at all now.
            raise InteExisterandeSFS("%s should contain a single base act, "
                                     "but doesn't" % url)

    def download_single(self, basefile, url=None):
        """Laddar ner senaste konsoliderade versionen av
        grundf\xf6rfattningen med angivet SFS-nr. Om en tidigare version
        finns p\xe5 disk, arkiveras den. Returnerar det SFS-nummer till
        vilket f\xf6rfattningen uppdaterats."""
        if not url:
            url = self.remote_url(basefile)
        sfsr_url = url.replace("sfst?", "sfsr?")

        # FIXME: a lot of code duplication compared to
        # DocumentRepository.download_single. Maybe particularly the
        # DocumentEntry juggling should go into download_if_needed()?
        downloaded_path = self.store.downloaded_path(basefile)
        created = not os.path.exists(downloaded_path)
        updated = False
        if self.download_if_needed(url, basefile):
            if created:
                text = util.readfile(downloaded_path, encoding=self.source_encoding)
                if "<div>Inga tr\xe4ffar</div>" in text:
                    self.log.warning("%s: Is not really an base SFS, search results must have contained an invalid entry" % basefile)
                    util.robust_remove(downloaded_path)
                    return False
                self.log.info("%s: downloaded from %s" % (basefile, url))
            else:
                self.log.info(
                    "%s: downloaded new version from %s" % (basefile, url))
            updated = True
        # using the attachment functionality makes some sense, but
        # requires that self.store.storage_policy = "dir"
        # regfilename= self.store.downloaded_path(basefile,attachment="register")
        # The method used by download_new does not allow us to
        # discover the magic URL to the database view containing
        # metadata
        if url:
            metadatafilename = self.store.metadata_path(basefile)
            self.download_if_needed(url, basefile, archive=False, filename=metadatafilename)
        regfilename = self.store.register_path(basefile)
        self.download_if_needed(sfsr_url, basefile, archive=False, filename=regfilename)
        entry = DocumentEntry(self.store.documententry_path(basefile))
        now = datetime.now()
        entry.orig_url = url
        if created:
            entry.orig_created = now
        if updated:
            entry.orig_updated = now
        checked = True
        if checked:
            entry.orig_checked = now
        entry.save()

        return updated

    # FIXME: This doesn't work at all
    def get_archive_version_nonworking(self, basefile, sfst_tempfile):
        sfst_file = self.store.downloaded_path(basefile)
        # FIXME: Implement get_archive_version
        if os.path.exists(sfst_file):
            old_checksum = self._checksum(sfst_file)
            new_checksum = self._checksum(sfst_tempfile)
            upphavd_genom = self._find_upphavts_genom(sfst_tempfile)
            uppdaterad_tom = self._find_uppdaterad_tom(basefile, sfst_tempfile)
            if (old_checksum != new_checksum):
                old_uppdaterad_tom = self._find_uppdaterad_tom(
                    basefile, sfst_file)
                uppdaterad_tom = self._find_uppdaterad_tom(
                    basefile, sfst_tempfile)
                if uppdaterad_tom != old_uppdaterad_tom:
                    self.log.info('        %s har \xe4ndrats (%s -> %s)' % (
                        basefile, old_uppdaterad_tom, uppdaterad_tom))
                    self._archive(sfst_file, basefile, old_uppdaterad_tom)
                else:
                    self.log.info('        %s har \xe4ndrats (gammal '
                                  'checksum %s)' % (basefile, old_checksum))
                    self._archive(sfst_file,
                                  basefile, old_uppdaterad_tom, old_checksum)

                # replace the current file, regardless of wheter
                # we've updated it or not
                util.robust_rename(sfst_tempfile, sfst_file)
            elif upphavd_genom:
                self.log.info('        %s har upph\xe4vts' % (basefile))

            else:
                self.log.debug('        %s har inte \xe4ndrats (gammal '
                               'checksum %s)' % (basefile, old_checksum))
        else:
            util.robust_rename(sfst_tempfile, sfst_file)

        # FIXME: since basefile might be slightly modified from the
        # actual URL to be used ("bet=1878:bih. 56 s. 1" vs
        # "1878:bih.56_s.1") it's not really safe to use this template
        sfsr_url = self.document_sfsr_url_template % {'basefile':
                                                      basefile.replace(" ", "%20")}
        sfsr_file = self.store.register_path(basefile)
        if (old_uppdaterad_tom and
                old_uppdaterad_tom != uppdaterad_tom):
            self._archive(sfsr_file, basefile, old_uppdaterad_tom)

        self.download_if_needed(sfsr_url, basefile, filename=sfsr_file)

        if upphavd_genom:
            self.log.info(
                '        %s \xe4r upph\xe4vd genom %s' % (basefile, upphavd_genom))
            return upphavd_genom
        elif uppdaterad_tom:
            self.log.info(
                '        %s \xe4r uppdaterad tom %s' % (basefile, uppdaterad_tom))
            return uppdaterad_tom
        else:
            self.log.info(
                '        %s \xe4r varken uppdaterad eller upph\xe4vd' % (basefile))
            return None

    def _find_uppdaterad_tom(self, sfsnr, filename=None, reader=None):
        if not reader:
            reader = TextReader(filename, encoding=self.source_encoding)
        try:
            reader.cue("\xc4ndring inf\xf6rd:</span> t.o.m. SFS")
            l = reader.readline()
            m = re.search('(\d+:\s?\d+)', l)
            if m:
                return m.group(1)
            else:
                # if m is None, the SFS id is using a non-standard
                # formatting (eg 1996/613-first-version) -- interpret
                # it as if it didn't exist
                return sfsnr
        except IOError:
            return sfsnr  # the base SFS nr

    def _find_upphavts_genom(self, filename):
        return None # this info is not available in the SFST document
                    # anymore (but sort of through the SFSR docs,
                    # although date seems to be missing).

    def _checksum(self, filename):
        """MD5-checksumman f\xf6r den angivna filen"""
        import hashlib
        c = hashlib.md5()
        try:
            c.update(util.readfile(filename, encoding=self.source_encoding))
        except:
            self.log.warning("Could not extract plaintext from %s" % filename)
        return c.hexdigest()

    def make_document(self, basefile=None):
        doc = super(SFS, self).make_document(basefile)
        if basefile:   # toc_generate_page calls this w/o basefile
            # We need to get the uppdaterad_tom field to create a proper
            # URI.  First create a throwaway reader and make sure we have
            # the intermediate file at ready
            # FIXME: this is broken
            fp = self.downloaded_to_intermediate(basefile)
            textheader = fp.read(2048)
            t = TextReader(string=textheader.decode(self.source_encoding, errors="ignore"))
            fp.close()
            uppdaterad_tom = self._find_uppdaterad_tom(basefile, reader=t)
            doc.uri = self.canonical_uri(basefile, uppdaterad_tom)
        return doc

    def canonical_uri(self, basefile, konsolidering=False):
        basefile = self.sanitize_basefile(basefile)
        attributes = self.metadata_from_basefile(basefile)

        parts = basefile.split(":", 1)
        # add some extra attributes that will enable
        # attributes_to_resource to create a graph that is partly
        # wrong, but will yield the correct URI.
        attributes.update({"rpubl:arsutgava": parts[0],
                           "rpubl:lopnummer": parts[1],
                           "rpubl:forfattningssamling":
                           URIRef(self.lookup_resource("SFS",
                                                       SKOS.altLabel))})
        if konsolidering:
            if konsolidering is not True:
                # eg konsolidering = "2013-05-30" or "2013:460"
                konsolidering = konsolidering.replace(" ", "_")
            attributes["dcterms:issued"] = konsolidering
        resource = self.attributes_to_resource(attributes)
        uri = self.minter.space.coin_uri(resource)
        # create eg "https://lagen.nu/sfs/2013:460/konsolidering" if
        # konsolidering = True instead of a issued date.
        # FIXME: This should be done in CoIN entirely
        if konsolidering is True:
            uri = uri.rsplit("/", 1)[0]
        computed_basefile = self.basefile_from_uri(uri)
        assert basefile == computed_basefile, "%s -> %s -> %s" % (basefile, uri, computed_basefile)
        # end temporary code
        return uri

    def basefile_from_uri(self, uri):
        basefile = super(SFS, self).basefile_from_uri(uri)
        if not basefile:
            return
        # remove any possible "/konsolidering/2015:123" trailing info
        basefile = basefile.split("/")[0]
        if "#" in basefile:
            basefile = basefile.split("#", 1)[0]
        # "1874:26 s.11" -> <https://lagen.nu/sfs/1874:26_s.11> -> "1874:26 s.11"
        # NOTE: This is unneccesary now that the URISpace defines spaceReplacement
        # basefile = basefile.replace("s.", " s.")
        return basefile

    def metadata_from_basefile(self, basefile):
        """Construct the basic attributes, in dict form, for a given
        consolidated SFS.

        """
        attribs = super(SFS, self).metadata_from_basefile(basefile)
        del attribs["rpubl:arsutgava"]
        del attribs["rpubl:lopnummer"]
        attribs["dcterms:publisher"] = "Regeringskansliet"
        return attribs
    
    def downloaded_to_intermediate(self, basefile):
        filename = self.store.downloaded_path(basefile)
        if not os.path.exists(filename):
            self.log.warning("%s: Fulltext is missing" % basefile)
            # FIXME: This code (which only runs when fulltext is
            # missong) needs to be rewritten
            baseuri = self.canonical_uri(basefile)
            if baseuri in registry:
                title = registry[baseuri].value(URIRef(baseuri),
                                                self.ns['dcterms'].title)
                desc.value(self.ns['dcterms'].title, title)
            desc.rel(self.ns['dcterms'].publisher,
                     self.lookup_resource("Regeringskansliet"))
            desc.value(self.ns['dcterms'].identifier, "SFS " + basefile)
            doc.body = Forfattning([Stycke(['Lagtext saknas'],
                                           id='S1')])
        rawtext = util.readfile(filename, encoding=self.source_encoding)
        if not self.config.keepexpired:
            needles = ('<span class="bold">Upph\xe4vd:</span> ',
                       '<span class="bold">Övrigt:</span> Utgår genom SFS')
            for needle in needles:
                idx = rawtext.find(needle, 0, 10000)
                if idx != -1:
                    datestr = rawtext[idx+len(needle):idx+len(needle)+10]
                    if (not re.match("\d+-\d+-\d+$", datestr) or
                        (datetime.strptime(datestr, '%Y-%m-%d') < datetime.today())):
                        self.log.debug('%s: Expired' % basefile)
                        raise UpphavdForfattning("%s is an expired SFS" % basefile,
                                                 dummyfile=self.store.parsed_path(basefile))
        return self._extract_text(basefile)

#  I think maybe SwedishLegalSource.patch_if_needed does all this now?
#
#    def patch_if_needed(self, fp, basefile):
#        fp = super(SFS, self).patch_if_needed(fp, basefile)
#        # find out if patching occurred and record the patch description
#        # (maybe this should only be done in the lagen.nu.SFS subclass?
#        # the canonical SFS repo should maybe not have patches?)
#        if None and patchdesc:
#            desc.value(self.ns['rinfoex'].patchdescription,
#                       patchdesc)
#        return fp

    def extract_head(self, fp, basefile):
        """Parsear ut det SFSR-registret som inneh\xe5ller alla \xe4ndringar
        i lagtexten fr\xe5n HTML-filer"""

        # NB: We should really call self.store.register_path, but that
        # custom func isn't mocked by ferenda.testutil.RepoTester,
        # and downloaded_path is. So we call that one and munge it.
        filename = self.store.downloaded_path(basefile).replace(
            "/downloaded/", "/register/")
        with codecs.open(filename, encoding=self.source_encoding) as rfp:
            soup = BeautifulSoup(rfp.read(), "lxml")
        # do we really have a registry?
        notfound = soup.find(text="S\xf6kningen gav ingen tr\xe4ff!")
        if notfound:
            raise InteExisterandeSFS(str(notfound))
        textheader = fp.read(2048)
        if not isinstance(textheader, str):
            # Depending on whether the fp is opened through standard
            # open() or bz2.BZ2File() in self.parse_open(), it might
            # return bytes or unicode strings. This seem to be a
            # problem in BZ2File (or how we use it). Just roll with it.
            textheader = textheader.decode(self.source_encoding, errors="ignore")

        idx = textheader.index("-"*64)
        header = textheader[:idx]
        fp.seek(len(header.encode("utf-8")) + 66)
        return soup, header

    def extract_metadata(self, datatuple, basefile):
        soup, reader = datatuple
        d = self.metadata_from_basefile(basefile)
        d.update(self.extract_metadata_register(soup, basefile))
        d.update(self.extract_metadata_header(reader, basefile))
        return d

    def extract_metadata_register(self, soup, basefile):
        # any change metadata (found below) should result in triples
        # like <.../1977:672> rpubl:ersatter <.../1915:218#P27>
        # ie. the object should be a URI based on the base act, not
        # the change act itself
        self.lagrum_parser._currenturl = self.canonical_uri(basefile)
        d = {}
        content = soup.find('div', 'search-results-content')
        innerboxes = content.findAll('div', 'result-inner-box')
        d = OrderedDict()
        d['SFS-nummer'] = util.normalize_space(innerboxes[0].text.split(u"\xb7")[1])
        d['Rubrik'] = util.normalize_space(innerboxes[1].text)
        for innerbox in innerboxes[2:]:
            key, val = innerbox.text.split(":", 1)
            d[key.strip()] = val.strip()
        changes = [d]
        for c in content.findAll('div', 'result-inner-sub-box-container'):
            d = OrderedDict()
            d[u'SFS-nummer'] = c.find('div',
                                      'result-inner-sub-box-header').text.split("SFS ")[1].strip()
            for row in c.findAll('div', 'result-inner-sub-box'):
                key, val = row.text.split(":", 1)
                d[key.strip()] = util.normalize_space(val)
            changes.append(d)
        g = self.make_graph()  # used for qname lookup only
        for rowdict in changes:
            docuri = self.canonical_uri(rowdict['SFS-nummer'])
            parts = rowdict['SFS-nummer'].split(":")
            d[docuri] = {
                "dcterms:publisher": "Regeringskansliet",
                "rpubl:arsutgava": parts[0],
                "rpubl:beslutadAv": "Regeringskansliet",
                "rpubl:forfattningssamling": "SFS",
                "rpubl:lopnummer": parts[1]
            }
            for key, val in list(rowdict.items()):
                if key == 'SFS-nummer':
                    (arsutgava, lopnummer) = val.split(":")
                    d[docuri]["dcterms:identifier"] = "SFS " + val
                    d[docuri]["rpubl:arsutgava"] = arsutgava
                    d[docuri]["rpubl:lopnummer"] = lopnummer

                elif key == 'Departement':
                    d[docuri]["rpubl:departement"] = val
                    # FIXME: Sanitize this in
                    # sanitize_metadata->sanitize_department, lookup
                    # resource in polish_metadata
                elif key == 'Rubrik':
                    # Change acts to some special laws never contain the SFS no
                    # of the law
                    special = ("1949:381", "1958:637", "1987:230", "1970:994",
                               "1998:808", "1962:700", "1942:740", "1981:774",
                               "2010:110", "1949:105", "1810:0926", "1974:152",
                               "2014:801", "1991:1469")
                    if basefile.replace("_", " ") not in val and not basefile in special:
                        self.log.warning(
                            "%s: Base SFS %s not in title %r" % (basefile,
                                                                 basefile,
                                                                 val))
                    d[docuri]["dcterms:title"] = util.normalize_space(val)
                    d[docuri]["rdf:type"] = self._forfattningstyp(val)
                elif key == 'Observera':
                    d[docuri]["rdfs:comment"] = val
                elif key == 'Upphävd':
                    # val is normally "YYYY-MM-DD" but may contain trailing info (1973:638)
                    dateval = datetime.strptime(val[:10], '%Y-%m-%d')
                    if dateval < datetime.today():
                        raise UpphavdForfattning("%s is an expired SFS"
                                                 % basefile,
                                                 dummyfile=self.store.parsed_path(basefile))
                    d[docuri]["rpubl:upphavandedatum"] = val
                elif key == 'Ikraft':
                    d[docuri]["rpubl:ikrafttradandedatum"] = val[:10]
                elif key == 'Omfattning':
                    # First, create rdf statements for every
                    # single modified section we can find
                    for changecat in val.split('; '):
                        if (changecat.startswith('\xe4ndr.') or
                            changecat.startswith('\xe4ndr ') or
                                changecat.startswith('\xe4ndring ')):
                            pred = self.ns['rpubl'].ersatter
                        elif (changecat.startswith('upph.') or
                              changecat.startswith('upp.') or
                              changecat.startswith('utg\xe5r')):
                            pred = self.ns['rpubl'].upphaver
                        elif (changecat.startswith('ny') or
                              changecat.startswith('ikrafttr.') or
                              changecat.startswith('ikrafftr.') or
                              changecat.startswith('ikraftr.') or
                              changecat.startswith('ikrafttr\xe4d.') or
                              changecat.startswith('till\xe4gg')):
                            pred = self.ns['rpubl'].inforsI
                        elif (changecat.startswith('nuvarande') or
                              changecat.startswith('rubr. n\xe4rmast') or
                              changecat in ('begr. giltighet', 'Omtryck',
                                            'omtryck', 'forts.giltighet',
                                            'forts. giltighet',
                                            'forts. giltighet av vissa best.')):
                            # some of these changecats are renames, eg
                            # "nuvarande 2, 3, 4, 5 \xa7\xa7 betecknas 10,
                            # 11, 12, 13, 14, 15 \xa7\xa7;" or
                            # "rubr. n\xe4rmast efter 1 \xa7 s\xe4tts n\xe4rmast
                            # f\xf6re 10 \xa7"
                            pred = None
                        else:
                            self.log.warning(
                                "%s: Ok\xe4nd omfattningstyp %r" %
                                (basefile, changecat))
                            pred = None
                        for node in self.lagrum_parser.parse_string(changecat,
                                                                    pred):
                            if hasattr(node, 'predicate'):
                                qname = g.qname(node.predicate)
                                if qname not in d[docuri]:
                                    d[docuri][qname] = []
                                d[docuri][qname].append(node.uri)
                    # Secondly, preserve the entire text
                    d[docuri]["rpubl:andrar"] = val
                elif key == 'F\xf6rarbeten':
                    for node in self.forarbete_parser.parse_string(val,
                                                                   "rpubl:forarbete"):
                        if hasattr(node, 'uri'):
                            if "rpubl:forarbete" not in d[docuri]:
                                d[docuri]["rpubl:forarbete"] = []
                            d[docuri]["rpubl:forarbete"].append(node.uri)
                            d[node.uri] = {"dcterms:identifier": str(node)}
                elif key == 'CELEX-nr':
                    for celex in re.findall('3\d{2,4}[LR]\d{4}', val):
                        b = BNode()
                        cg = Graph()
                        cg.add((b, RPUBL.celexNummer, Literal(celex)))
                        celexuri = self.minter.space.coin_uri(cg.resource(b))
                        if "rpubl:genomforDirektiv" not in d[docuri]:
                            d[docuri]["rpubl:genomforDirektiv"] = []
                        d[docuri]["rpubl:genomforDirektiv"].append(celexuri)
                        d[celexuri] = {"rpubl:celexNummer": celex}
                elif key == 'Tidsbegr\xe4nsad':
                    d["rinfoex:tidsbegransad"] = val[:10]
                    expdate = datetime.strptime(val[:10], '%Y-%m-%d')
                    if expdate < datetime.today():
                        if not self.config.keepexpired:
                            raise UpphavdForfattning(
                                "%s is expired (time-limited) SFS" % basefile,
                                dummyfile=self.store.parsed_path(basefile))
                else:
                    if not (key.startswith("http://") or key.startswith("https://")):
                        self.log.warning(
                            '%s: Obekant nyckel [\'%s\']' % (basefile, key))
            utfardandedatum = self._find_utfardandedatum(rowdict['SFS-nummer'])
            if utfardandedatum:
                d[docuri]["rpubl:utfardandedatum"] = utfardandedatum
        return d

    def extract_metadata_header(self, headertext, basefile):
        re_sfs = re.compile(r'(\d{4}:\d+)\s*$').search
        lines = headertext.strip().split("\n\n")
        # first few lines contains data without a key, and we already
        # have that data from other sources, so just skip it
        skip = True
        d = {}
        identifier = "SFS " + lines[0].split('\xb7')[1].strip()
        d["dcterms:title"] = util.normalize_space(lines[1])
        for line in lines[2:]:
            if ":" not in line:
                continue
            key, val = [x.strip() for x in line.split(":", 1)]
            
            # Simple string literals
            if key == '\xd6vrigt':
                d["rdfs:comment"] = val
            # date literals
            elif key == 'Utf\xe4rdad':
                d["rpubl:utfardandedatum"] = val[:10]
            elif key == 'Tidsbegr\xe4nsad':
                # FIXME: Should be done by lagen.nu.SFS
                d["rinfoex:tidsbegransad"] = val[:10]
            elif key == 'Upph\xe4vd':
                dat = datetime.strptime(val[:10], '%Y-%m-%d')
                d["rpubl:upphavandedatum"] = val[:10]
                if not self.config.keepexpired and dat < datetime.today():
                    raise UpphavdForfattning("%s is an expired SFS" % basefile,
                                             dummyfile=self.store.parsed_path(basefile))

            elif key == 'Departement':
                # the split is only needed because of SFS 1942:724,
                # which has "F\xf6rsvarsdepartementet,
                # Socialdepartementet"...
                if "departementet, " in val:
                    val = val.split(", ")[0]
                d["dcterms:creator"] = val
            elif (key == '\xc4ndring inf\xf6rd' and re_sfs(val)):
                uppdaterad = re_sfs(val).group(1)
                # not sure we need to add this, since parse_metadata
                # catches the same
                d["rpubl:konsolideringsunderlag"] = [URIRef(self.canonical_uri(uppdaterad))]
                if identifier and identifier != "SFS " + uppdaterad:
                    identifier += " i lydelse enligt SFS " + uppdaterad
                d["dcterms:issued"] = uppdaterad

            elif (key == 'Omtryck' and re_sfs(val)):
                d["rinfoex:omtryck"] = self.canonical_uri(re_sfs(val).group(1))
            elif (key == 'F\xf6rfattningen har upph\xe4vts genom' and
                  re_sfs(val)):
                s = re_sfs(val).group(1)
                d["rinfoex:upphavdAv"] = self.canonical_uri(s)
            elif key == 'Ikraft':
                d["rpubl:ikrafttradandedatum"] = val[:10]
            else:
                self.log.warning(
                    '%s: Obekant nyckel [\'%s\']' % (basefile, key))
        # FIXME: This is a misuse of the dcterms:issued prop in order
        # to mint the correct URI. We need to remove this somehow afterwards.
        if "dcterms:issued" not in d:
            d["dcterms:issued"] = basefile
        d["dcterms:identifier"] = identifier
        return d

    def sanitize_basefile(self, basefile):
        year, no = basefile.split(":")
        no = no.replace("_", " ") # make this function repeatably callable
        assert len(year) == 4 and year.isdigit(), "%s does not contain a valid year" % basefile
        # normalize the "number" (which might be 'bih.40s.1' or '60 s. 1')
        no = no.replace("bih. ", "bih.").replace(" s.", "s.").replace("s.", " s.").replace("s. ", "s.")
        # we used to do this in swedishlegalsource.space.ttl by
        # setting coin:spaceReplacement to "_" but that messed up
        # fragment identifiers ("#P1_a" instead of "#P1a")
        no = no.replace(" ", "_")
        return "%s:%s" % (year, no)

    def sanitize_metadata(self, attribs, basefile):
        attribs = super(SFS, self).sanitize_metadata(attribs, basefile)
        for k in attribs:
            if isinstance(attribs[k], dict):
                attribs[k] = self.sanitize_metadata(attribs[k], basefile)
            elif k in ("dcterms:creator", "rpubl:departement"):
                attribs[k] = self.sanitize_departement(attribs[k])
        return attribs

    def sanitize_departement(self, val):
        # to avoid "Assuming that" warnings, autoremove sub-org ids,
        # ie "Finansdepartementet S3" -> "Finansdepartementet"
        # loop until done to handle "Justitiedepartementet DOM, L5 och \xc5"

        cleaned = None
        while True:
            cleaned = re.sub(",? (och|[A-Z\xc5\xc4\xd6\d]{1,5})$", "", val)
            if val == cleaned:
                break
            val = cleaned
        return cleaned

    def polish_metadata(self, attributes):
        # attributes will be a nested dict with some values being
        # dicts themselves. Convert the subdicts to rdflib.Resource
        # objects.
        post_count = 0
        r = None
        for k in sorted(list(attributes.keys()), key=util.split_numalpha):
            if isinstance(attributes[k], dict):
                if len(attributes[k]) > 1:
                    # get a rdflib.Resource with a coined URI
                    r = super(SFS, self).polish_metadata(attributes[k])
                    if k != str(r.identifier):
                        # This happens when lopnummer cointains spaces
                        # because the URISpace defintion removes
                        # spaces while we (in this particular case)
                        # want them replaced with "_"). So just rebase
                        # the graph
                        for p, o in r.graph.predicate_objects(r.identifier):
                            r.graph.remove((r.identifier, p, o))
                            r.graph.add((URIRef(k), p, o))
                    if "rpubl:konsoliderar" not in attributes:
                        attributes["rpubl:konsoliderar"] = URIRef(k)
                    baseuri = k
                    del attributes[k]
                    attributes[URIRef(k)] = r
                    if "rpubl:konsolideringsunderlag" not in attributes:
                        attributes["rpubl:konsolideringsunderlag"] = []
                    attributes["rpubl:konsolideringsunderlag"].append(r.identifier)
                    post_count += 1
                else: 
                    # get a anonymous (BNode) rdflib.Resource
                    ar = self.attributes_to_resource(attributes[k])
                    # Add a rdf:type to this BNode if we can determine
                    # it.  FIXME: we should be able to get this
                    # information from forarbete_parser, since it has
                    # already gleaned it. Also, this general class
                    # shouldn't deal with RINFOEX classes (this should
                    # be something for lagen.nu.SFS)
                    if "/prop/" in k:
                        ar.add(RDF.type, RPUBL.Proposition)
                    elif "/bet/" in k:
                        ar.add(RDF.type, RINFOEX.Utskottsbetankande)
                    elif "/rskr/" in k:
                        ar.add(RDF.type, RINFOEX.Riksdagsskrivelse)
                    del attributes[k]
                    attributes[URIRef(k)] = ar
        resource = super(SFS, self).polish_metadata(attributes,
                                                    infer_nodes=False)
        # Finally: the dcterms:issued property for this
        # rpubl:KonsolideradGrundforfattning isn't readily
        # available. The true value is only found by parsing PDF files
        # in another docrepo. There are two ways of finding
        # it out.
        issued = None
        # 1. if registry contains a single value (ie a
        # Grundforfattning that hasn't been amended yet), we can
        # assume that dcterms:issued == rpubl:utfardandedatum
        if post_count == 1 and resource.value(RPUBL.utfardandedatum):
            issued = resource.value(RPUBL.utfardandedatum)
        elif r:
            # 2. if the last post in registry contains a
            # rpubl:utfardandedatum, assume that this version of the
            # rpubl:KonsolideradGrundforfattning has the same
            # dcterms:issued date (Note that r is automatically set to
            # the last post due to the above loop)
            utfardad = r.value(RPUBL.utfardandedatum)
            if utfardad:
                issued = utfardad
        if issued:
            resource.graph.add((resource.identifier, DCTERMS.issued, issued))
        else:
            # create a totally incorrect value, otherwise
            # lagen.nu.SFS.infer_triples wont be able to generate a
            # owl:sameAs uri
            resource.graph.add((resource.identifier, DCTERMS.issued, Literal(datetime.today())))
        return resource


    re_missing_newline = re.compile("(\.)\n([IV]+  )", flags=re.MULTILINE)
    def sanitize_body(self, textreader):
        # add missing newlines where we can detect them missing. We
        # could do this with patchfiles, but some errors seem
        # systematic.

        # missing extra newline before underavdelning (identified by
        # roman numeral followed by double space) occurs multiple
        # times in 2010:110. Check for end of sentence followed by
        # single newline followed by roman numeral.
        if self.re_missing_newline.search(textreader.data):
            textreader.data = self.re_missing_newline.sub("\\1\n\n\\2", textreader.data)
            textreader.maxpos = len(textreader.data)
        return textreader

    def postprocess_doc(self, doc):
        # finally, combine data from the registry with any possible
        # overgangsbestammelser, and append them at the end of the
        # document.
        obs = {}
        obsidx = None
        for idx, p in enumerate(doc.body):
            if isinstance(p, Overgangsbestammelser):
                for ob in p:
                    assert isinstance(ob, Overgangsbestammelse)
                    obs[self.canonical_uri(ob.sfsnr)] = ob
                    obsidx = idx
                break
        if obs:
            del doc.body[obsidx]
            reg = Register(rubrik='\xc4ndringar och \xf6verg\xe5ngsbest\xe4mmelser')
        else:
            reg = Register(rubrik='\xc4ndringar')

        # remove the bogus dcterms:issued thing that we only added to
        # aid URI generation
        for o in doc.meta.objects(URIRef(doc.uri), DCTERMS.issued):
            if not o.datatype:
                doc.meta.remove((URIRef(doc.uri), DCTERMS.issued, o))

        # move some data from the big document graph to a series of
        # small graphs, one for each change act.
        trash = set()
        for res in sorted(doc.meta.resource(doc.uri).objects(RPUBL.konsolideringsunderlag), key=lambda uri:util.split_numalpha(str(uri))):
            if not res.value(RDF.type):
                continue
            identifier = res.value(DCTERMS.identifier).replace("SFS ", "L")
            graph = self.make_graph()
            for s, p, o in res:
                if not isinstance(o, Literal):
                    o = o.identifier
                triple = (s.identifier, p.identifier, o)
                graph.add(triple)
                doc.meta.remove(triple)
                if p.identifier == RPUBL.forarbete:
                    triple = (o, DCTERMS.identifier,
                              doc.meta.value(o, DCTERMS.identifier))
                    graph.add(triple)
                    trash.add(triple)
                    triple = (o, RDF.type,
                              doc.meta.value(o, RDF.type))
                    graph.add(triple)
                    trash.add(triple)
                elif p.identifier == RPUBL.genomforDirektiv:
                    triple = (o, RPUBL.celexNummer,
                              doc.meta.value(o, RPUBL.celexNummer))
                    graph.add(triple)
                    trash.add(triple)
            uri = str(res.identifier)
            rp = Registerpost(uri=uri, meta=graph, id=identifier)
            reg.append(rp)
            if uri in obs:
                rp.append(obs[uri])
        for triple in trash:
            doc.meta.remove(triple)
        doc.body.append(reg)

        # finally, set the uri of the main body object to a better value
        doc.body.uri = str(doc.meta.value(URIRef(doc.uri), RPUBL.konsoliderar))

    def _forfattningstyp(self, forfattningsrubrik):
        forfattningsrubrik = util.normalize_space(
            # we omit the last char of the regex, as this is the
            # end-of-line matcher ($) wich we don't want in this case.
            re.sub(self.basefile_regex[:-1], "", forfattningsrubrik).replace("()", ""))
        if (forfattningsrubrik.startswith('Lag ') or
            (forfattningsrubrik.endswith('lag') and
             not forfattningsrubrik.startswith('F\xf6rordning')) or
            forfattningsrubrik.endswith('balk')):
            return self.ns['rpubl'].Lag
        else:
            return self.ns['rpubl'].Forordning

    def _find_utfardandedatum(self, sfsnr):
        # FIXME: Code to instantiate a SFSTryck object and muck about goes here
        fake = {'1915:218': date(1915, 12, 31),  # we really don't know
                '1987:329': date(1987, 12, 31),  #        -""-
                '1994:1513': date(1994, 12, 31), #        -""-
                '1994:1809':date(1994, 12, 31),  #        -""-
                '2013:363': date(2013, 5, 23),
                '2008:344': date(2008, 5, 22),
                '2009:1550': date(2009, 12, 17),
                '2013:411': date(2013, 5, 30),
                '2013:647': date(2013, 7, 2),
                '2010:448': date(2010, 6, 8),
                '2010:110': date(2010, 3, 16),
                '2010:343': date(2010, 5, 19),
                }
        return fake.get(sfsnr, None)

    def extract_body(self, fp, basefile):
        bodystring = fp.read()
        # see comment in extract_head for why we must handle both
        # bytes- and str-files
        if not isinstance(bodystring, str):
            bodystring = bodystring.decode(self.source_encoding)
        reader = TextReader(string=bodystring, linesep=TextReader.UNIX)
        reader.autostrip = True
        return reader

    # FIXME: should get hold of a real LNKeyword repo object and call
    # it's canonical_uri()
    def _term_to_subject(self, term):
        capitalized = term[0].upper() + term[1:]
        return 'https://lagen.nu/begrepp/%s' % capitalized.replace(' ', '_')

    # this struct is intended to be overridable
    ordinalpredicates = {
        Kapitel: "rpubl:kapitelnummer",
        Paragraf: "rpubl:paragrafnummer",
    }

    def construct_id(self, node, state):
        def not_in_force(para):
            # in some cases, a para might have a 'upphor' or
            # 'ikrafttrader' attribute that is a string, not a date
            # (typically "den dag regeringen bestämmer")
            return ((hasattr(para, 'upphor') and isinstance(para.upphor, datetime) and datetime.now() > para.upphor) or
                    (hasattr(para, 'ikrafttrader') and isinstance(para.ikrafttrader, datetime) and datetime.now() < para.ikrafttrader))
                
        # copy our state (no need for copy.deepcopy as state shouldn't
        # use nested dicts)
        state = dict(state)
        if isinstance(node, Forfattning):
            attributes = self.metadata_from_basefile(state['basefile'])
            state.update(attributes)
            state["rpubl:arsutgava"], state["rpubl:lopnummer"] = state["basefile"].split(":", 1)
            state["rpubl:forfattningssamling"] = self.lookup_resource("SFS", SKOS.altLabel)
        if self.ordinalpredicates.get(node.__class__):  # could be a qname?
            if hasattr(node, 'ordinal') and node.ordinal:
                ordinal = node.ordinal
            elif hasattr(node, 'sfsnr'):
                ordinal = node.sfsnr
            else:
                # find out which # this is
                ordinal = 0
                for othernode in state['parent']:
                    if type(node) == type(othernode):
                        ordinal += 1
                    if node == othernode:
                        break

            # in the case of Listelement / rinfoex:punktnummer, these
            # can be nested. In order to avoid overwriting a toplevel
            # Listelement with the ordinal from a sub-Listelement, we
            # make up some extra RDF predicates that our URISpace
            # definition knows how to handle. NB: That def doesn't
            # support a nesting of arbitrary depth, but this should
            # not be a problem in practice.
            ordinalpredicate = self.ordinalpredicates.get(node.__class__)
            if ordinalpredicate == "rinfoex:punktnummer":
                while ordinalpredicate in state:
                    ordinalpredicate = ("rinfoex:sub" +
                                        ordinalpredicate.split(":")[1])
            state[ordinalpredicate] = ordinal
            del state['parent']
            for skip, ifpresent in self.skipfragments:
                if skip in state and ifpresent in state:
                    del state[skip]
            res = self.attributes_to_resource(state)
            try:
                uri = self.minter.space.coin_uri(res)
            except Exception:
                self.log.warning("Couldn't mint URI for %s" % type(node))
                uri = None
            if uri:
                # if there's two versions of a para (before and after
                # a change act), only use a URI for the version
                # currently in force to avoid having two nodes with
                # identical @about.
                if uri not in state['uris'] and (not isinstance(node, (Paragraf, Kapitel, Rubrik)) or
                                                 not not_in_force(node)):
                    node.uri = uri
                    state['uris'].add(uri)
                else:
                    # No uri added to this node means we shouldn't add
                    # an id either, and not recurse to it's
                    # children. Returning None instead of current
                    # state will prevent recursive calls on this nodes
                    # childen
                    return None
                    
                # else:
                #     print("Not assigning %s to another node" % uri)
                if "#" in uri:
                    node.id = uri.split("#", 1)[1]
                pass
        state['parent'] = node
        return state

    re_Bullet = re.compile(r'^(\-\-?|\x96) ')
    # NB: these are redefinitions of regex objects in sfs_parser.py
    re_SearchSfsId = re.compile(r'\((\d{4}:\d+)\)').search
    re_DottedNumber = re.compile(r'^(\d+ ?\w?)\. ')
    re_Bokstavslista = re.compile(r'^(\w)\) ')
    re_definitions = re.compile(
        r'^I (lagen|f\xf6rordningen|balken|denna lag|denna f\xf6rordning|denna balk|denna paragraf|detta kapitel) (avses med|betyder|anv\xe4nds f\xf6ljande)').match
    re_brottsdef = re.compile(
        r'\b(d\xf6ms|d\xf6mes)(?: han)?(?:,[\w\xa7 ]+,)? f\xf6r ([\w ]{3,50}) till (b\xf6ter|f\xe4ngelse)', re.UNICODE).search
    re_brottsdef_alt = re.compile(
        r'[Ff]\xf6r ([\w ]{3,50}) (d\xf6ms|d\xf6mas) till (b\xf6ter|f\xe4ngelse)', re.UNICODE).search
    re_parantesdef = re.compile(r'\(([\w ]{3,50})\)\.', re.UNICODE).search
    re_loptextdef = re.compile(
        r'^Med ([\w ]{3,50}) (?:avses|f\xf6rst\xe5s) i denna (f\xf6rordning|lag|balk)', re.UNICODE).search
    def find_definitions(self, element, find_definitions):
        if not isinstance(element, CompoundElement):
            return None
        find_definitions_recursive = find_definitions
        # Hitta begreppsdefinitioner
        if isinstance(element, Paragraf):
            # kolla om f\xf6rsta stycket inneh\xe5ller en text som
            # antyder att definitioner f\xf6ljer
            # self.log.debug("Testing %r against some regexes" % element[0][0])
            if self.re_definitions(element[0][0]):
                find_definitions = "normal"
            if (self.re_brottsdef(element[0][0]) or
                    self.re_brottsdef_alt(element[0][0])):
                find_definitions = "brottsrubricering"
            if self.re_parantesdef(element[0][0]):
                find_definitions = "parantes"
            if self.re_loptextdef(element[0][0]):
                find_definitions = "loptext"

            for p in element:
                if isinstance(p, Stycke):
                    # do an extra check in case "I denna paragraf
                    # avses med" occurs in the 2nd or later
                    # paragrapgh of a section
                    if self.re_definitions(p[0]):
                        find_definitions = "normal"
            find_definitions_recursive = find_definitions

        # Hitta lagrumsh\xe4nvisningar + definitioner
        if isinstance(element, (Stycke, Listelement, Tabellrad)):
            nodes = []
            term = None

            # self.log.debug("handling text %s, find_definitions %s" % (element[0],find_definitions))
            if find_definitions:
                # For Tabellrad, this is a Tabellcell, not a string,
                # but we fix that later
                elementtext = element[0] 
                termdelimiter = ":"

                if isinstance(element, Tabellrad):
                    # only the first cell can be a definition, and
                    # only if it's not the text "Beteckning". So for
                    # the reminder of this func, we switch context to
                    # not the element itself but rather the first
                    # cell.
                    element = elementtext 
                    elementtext = element[0]
                    if elementtext != "Beteckning":
                        term = elementtext
                        self.log.debug(
                            '"%s" \xe4r nog en definition (1)' % term)
                elif isinstance(element, Stycke):

                    # Case 1: "antisladdsystem: ett tekniskt st\xf6dsystem"
                    # Sometimes, : is not the delimiter between
                    # the term and the definition, but even in
                    # those cases, : might figure in the
                    # definition itself, usually as part of the
                    # SFS number. Do some hairy heuristics to find
                    # out what delimiter to use
                    if find_definitions == "normal":
                        if not self.re_definitions(elementtext):
                            if " - " in elementtext:
                                if (":" in elementtext and
                                        (elementtext.index(":") < elementtext.index(" - "))):
                                    termdelimiter = ":"
                                else:
                                    termdelimiter = " - "
                            m = self.re_SearchSfsId(elementtext)

                            if termdelimiter == ":" and m and m.start() < elementtext.index(
                                    ":"):
                                termdelimiter = " "

                            if termdelimiter in elementtext:
                                term = elementtext.split(termdelimiter)[0]
                                self.log.debug('"%s" \xe4r nog en definition (2.1)' % term)

                    # case 2: "Den som ber\xf6var annan livet, d\xf6ms
                    # f\xf6r mord till f\xe4ngelse"
                    m = self.re_brottsdef(elementtext)
                    if m:
                        term = m.group(2)
                        self.log.debug(
                            '"%s" \xe4r nog en definition (2.2)' % term)

                    # case 3: "F\xf6r milj\xf6brott d\xf6ms till b\xf6ter"
                    m = self.re_brottsdef_alt(elementtext)
                    if m:
                        term = m.group(1)
                        self.log.debug(
                            '"%s" \xe4r nog en definition (2.3)' % term)

                    # case 4: "Inteckning f\xe5r p\xe5 ans\xf6kan av
                    # fastighets\xe4garen d\xf6das (d\xf6dning)."
                    m = self.re_parantesdef(elementtext)
                    if m:
                        term = m.group(1)
                        # print("%s: %s" %  (basefile, elementtext))
                        self.log.debug(
                            '"%s" \xe4r nog en definition (2.4)' % term)

                    # case 5: "Med detaljhandel avses i denna lag
                    # f\xf6rs\xe4ljning av l\xe4kemedel"
                    m = self.re_loptextdef(elementtext)
                    if m:
                        term = m.group(1)
                        self.log.debug(
                            '"%s" \xe4r nog en definition (2.5)' % term)

                elif isinstance(element, Listelement):
                    for rx in (self.re_Bullet,
                               self.re_DottedNumber,
                               self.re_Bokstavslista):
                        elementtext = rx.sub('', elementtext)
                    term = elementtext.split(termdelimiter)[0]
                    self.log.debug('"%s" \xe4r nog en definition (3)' % term)

                # Longest legitimate term found "Valutav\xe4xling,
                # betalnings\xf6verf\xf6ring och annan finansiell
                # verksamhet"
                if term and len(term) < 68:
                    term = util.normalize_space(term)
                    termnode = LinkSubject(term, uri=self._term_to_subject(
                        term), predicate="dcterms:subject")
                    find_definitions_recursive = False
                else:
                    term = None

            if term:
                idx = None
                for p in element:
                    if isinstance(p, str) and term in p:
                        (head, tail) = p.split(term, 1)
                        nodes = (head, termnode, tail)
                        idx = element.index(p)
                if not idx is None:
                    element[idx:idx + 1] = nodes

        return find_definitions_recursive
        

    def find_references(self, node, state):
        pass

    def _count_elements(self, element):
        counters = defaultdict(int)
        if isinstance(element, CompoundElement):
            for p in element:
                if hasattr(p, 'fragment_label'):
                    counters[p.fragment_label] += 1
                    if hasattr(p, 'ordinal') and p.ordinal:
                        counters[p.fragment_label + p.ordinal] += 1
                    subcounters = self._count_elements(p)
                    for k in subcounters:
                        counters[k] += subcounters[k]
        return counters
 
    def set_skipfragments(self, node, dummystate):
        elements = self._count_elements(node)
        if 'K' in elements and elements['P1'] < 2:
            self.skipfragments = [
                ('rinfoex:avdelningnummer', 'rpubl:kapitelnummer'),
                ('rinfoex:underavdelningnummer', 'rpubl:kapitelnummer'),
                ('rpubl:kapitelnummer', 'rpubl:paragrafnummer')]
        else:
            self.skipfragments = [('rinfoex:avdelningnummer', 'rpubl:kapitelnummer'),
                                  ('rinfoex:underavdelningnummer', 'rpubl:kapitelnummer')
            ]
        return None  # run only on root element

    def get_parser(self, basefile, sanitized):
        # this should work something like offtryck_parser
        from .sfs_parser import make_parser
        return make_parser(sanitized, basefile, self.log, self.trace)

    def visitor_functions(self, basefile):
        return ((self.set_skipfragments, None),
                (self.construct_id, {'basefile': basefile,
                                     'uris': set()}),
                (self.find_definitions, False))

    def parse_entry_id(self, doc):
        # For SFS, the doc.uri can be temporal, ie
        # https://lagen.nu/2015:220/konsolidering/2015:667, but we'd
        # like to use a static value as entry.id, ie
        # https://lagen.nu/2015:220.
        return str(doc.meta.value(URIRef(doc.uri), RPUBL.konsoliderar))

    def parse_entry_title(self, doc):
        # should use eg Lag (2015:667) om ändring i lagen (2015:220) om blahonga
        return super(SFS, self).parse_entry_title(doc)
    
    def parse_entry_summary(self, doc):
        # should use eg. omfattning (if change) + förarbeten
        return "Omfattning: Ändrat 5 §, Ny 6 a §\n\n"
    
    _document_name_cache = {}
    _query_template_cache = {}
    def store_select(self, store, query_template, uri, context=None, extraparams=None):
        params = {'uri': uri,
                  'context': context}
        if extraparams:
            params.update(extraparams)
        if query_template not in self._query_template_cache:
            with self.resourceloader.open(query_template) as fp:
                self._query_template_cache[query_template] = fp.read()
        sq = self._query_template_cache[query_template] % params
        # Only FusekiStore.select supports (or needs) uniongraph
        if self.config.storetype == "FUSEKI":
            if context:
                kwargs = {'uniongraph': False}
            else:
                kwargs = {'uniongraph': True}
        else:
            kwargs = {}
        return store.select(sq, "python", **kwargs)


    # FIXME: Copied verbatim from keyword.py
    def time_store_select(self, store, query_template, basefile,
                          context=None, label="things", extra=None):
        values = {'basefile': basefile,
                  'label': label,
                  'count': None}
        uri = self.canonical_uri(basefile)
        msg = ("%(basefile)s: selected %(count)s %(label)s "
               "(%(elapsed).3f sec)")
        with util.logtime(self.log.debug,
                          msg,
                          values):
            result = self.store_select(store,
                                       query_template,
                                       uri,
                                       context,
                                       extra)
            values['count'] = len(result)
        return result

    def prep_annotation_file(self, basefile):
        sfsdataset = self.dataset_uri()
        assert "sfs" in sfsdataset
        dvdataset = sfsdataset.replace("sfs", "dv")
        wikidataset = sfsdataset.replace("sfs", "mediawiki")

        # this is old legacy code. The new nice way would be to create
        # one giant SPARQL CONSTRUCT query file and just set
        # self.sparql_annotations to that file. But you know, this works.
        uri = self.canonical_uri(basefile)
        baseuri = uri
        store = TripleStore.connect(self.config.storetype,
                                    self.config.storelocation,
                                    self.config.storerepository)
        # Putting togeher a (non-normalized) RDF/XML file, suitable
        # for XSLT inclusion in six easy steps
        stuff = {}
        # 1. all rpubl:Rattsfallsreferat that has baseuri as a
        # rpubl:lagrum, either directly or through a chain of
        # dcterms:isPartOf statements
        rattsfall = self.time_store_select(store,
                                           "sparql/sfs_rattsfallsref.rq",
                                           basefile,
                                           None,  # query uses both dv and sfs datasets
                                           "legal cases")

        stuff[baseuri] = {}
        stuff[baseuri]['rattsfall'] = []

        specifics = {}
        for row in rattsfall:
            if 'lagrum' not in row:
                lagrum = baseuri
            else:
                # truncate 1998:204#P7S2 to just 1998:204#P7
                if "S" in row['lagrum']:
                    lagrum = row['lagrum'][:row['lagrum'].index("S")]
                else:
                    lagrum = row['lagrum']
                specifics[row['id']] = True
            # we COULD use a tricky defaultdict for stuff instead of
            # this initializing code, but defauldicts don't pprint
            # so pretty...
            if not lagrum in stuff:
                stuff[lagrum] = {}
            if not 'rattsfall' in stuff[lagrum]:
                stuff[lagrum]['rattsfall'] = []

            record = {'id': row['id'],
                      'desc': row['desc'],
                      'uri': row['uri']}

            # if one case references two or more paragraphs in a
            # particular section (ie "6 kap 1 \xa7 1 st. och 6 kap 1 \xa7 2
            # st.") we will get duplicates that we can't (easily)
            # filter out in the SPARQL query. Filter them out here
            # instead.
            if not record in stuff[lagrum]['rattsfall']:
                stuff[lagrum]['rattsfall'].append(record)

        # remove cases that refer to the law itself and a specific
        # paragraph (ie only keep cases that only refer to the law
        # itself)
        filtered = []
        for r in stuff[baseuri]['rattsfall']:
            if r['id'] not in specifics:
                filtered.append(r)
        stuff[baseuri]['rattsfall'] = filtered

        # 2. all law sections that has a dcterms:references that matches this
        # (using dcterms:isPartOf).
        inboundlinks = self.time_store_select(store,
                                              "sparql/sfs_inboundlinks.rq",
                                              basefile,
                                              sfsdataset,
                                              "law references")
        stuff[baseuri]['inboundlinks'] = []

        # mapping <http://rinfo.lagrummet.se/publ/sfs/1999:175> =>
        # "R\xe4ttsinformationsf\xf6rordning (1999:175)"
        specifics = {}
        for row in inboundlinks:
            if not (row['uri'].startswith(("http://", "https://"))):
                # we once had a condition where some rows were like 
                # {'lagrum': 'https://lagen.nu/sfs/1998:204#L2015:589', 'uri': 'b0'}
                # so we make "sure" uri is a URI
                continue
            if 'lagrum' not in row:
                lagrum = baseuri
            else:
                # truncate 1998:204#P7S2 to just 1998:204#P7
                if "S" in row['lagrum']:
                    lagrum = row['lagrum'][:row['lagrum'].index("S")]
                else:
                    lagrum = row['lagrum']
                lagrum = row['lagrum']
                specifics[row['uri']] = True
            # we COULD use a tricky defaultdict for stuff instead of
            # this initializing code, but defauldicts don't pprint
            # so pretty...
            if not lagrum in stuff:
                stuff[lagrum] = {}
            if not 'inboundlinks' in stuff[lagrum]:
                stuff[lagrum]['inboundlinks'] = []
            # print "adding %s under %s" % (row['id'],lagrum)
            stuff[lagrum]['inboundlinks'].append({'uri': row['uri']})

        # remove inbound links that refer to the law itself plus at
        # least one specific paragraph (ie only keep cases that only
        # refer to the law itself)
        filtered = []
        for r in stuff[baseuri]['inboundlinks']:
            if r['uri'] not in specifics:
                filtered.append(r)
        stuff[baseuri]['inboundlinks'] = filtered

        # pprint (stuff)
        # 3. all wikientries that dcterms:description this
        wikidesc = self.time_store_select(store,
                                          "sparql/sfs_wikientries.rq",
                                          basefile,
                                          None,  # need both mediawiki and sfs contexts
                                          "wiki comments")

        for row in wikidesc:
            if not 'lagrum' in row:
                lagrum = baseuri
            else:
                lagrum = row['lagrum']

            if not lagrum in stuff:
                stuff[lagrum] = {}
            stuff[lagrum]['desc'] = row['desc']
        # (4. eurlex.nu data (mapping CELEX ids to titles))

        # 5. References to bemyndiganden
        bemyndiganden = self.time_store_select(store,
                                               "sparql/sfs_bemyndiganden.rq",
                                               basefile,
                                               None, # need all possible fs contexts
                                               "bemyndiganden")
        for row in bemyndiganden:
            lagrum = row['bemyndigande']
            if lagrum not in stuff:
                stuff[lagrum] = {}
            if 'bemyndiganden' not in stuff[lagrum]:
                stuff[lagrum]['bemyndiganden'] = []
            stuff[lagrum]['bemyndiganden'].append({'uri': row['fskr'],
                                                   'title': row['fskrtitle'],
                                                   'identifier': row['fskrid']})
        # 6. change entries for each section
        changes = self.time_store_select(store,
                                         "sparql/sfs_changes.rq",
                                         basefile,
                                         None, # need both prop and sfs contexts
                                         "change annotations")
        for row in changes:
            lagrum = row['lagrum']
            if not lagrum in stuff:
                stuff[lagrum] = {}
            if not 'changes' in stuff[lagrum]:
                stuff[lagrum]['changes'] = []
            stuff[lagrum]['changes'].append({'uri':        row['change'],
                                             'id':         row['id'],
                                             'changetype': row['changetype'],
                                             'propid':     row.get('propid'),
                                             'proptitle':  row.get('proptitle')})


        # 7. all forfattnigskommentar
        canonical_uri = self.canonical_uri(basefile)
        g = Graph().parse(self.store.distilled_path(basefile))
        title = str(g.value(URIRef(self.canonical_uri(basefile)), DCTERMS.title))
        tempuri = self.temp_sfs_uri(title)
        extra = {'tempuri': tempuri}
        forf_kommentar = self.time_store_select(store,
                                                "sparql/sfs_forfattningskommentar.rq",
                                                basefile,
                                                None,  # need both prop and sfs contexts
                                                "forfattningskommentarer",
                                                extra)
        seen_comments = {}
        for row in forf_kommentar:
            if row['kommentar'] in seen_comments:
                self.log.warning("Recieved duplicate comment for %s ('%s', previously '%s')" % (
                    row['kommentar'], row['prop'], seen_comments[row['kommentar']]))
                continue
            seen_comments[row['kommentar']] = row['prop']
            if not 'lagrum' in row:
                lagrum = baseuri
            else:
                # create canonical uris now that we know them (FIXME:
                # maybe this could be done with string functions in
                # the sparql query itself)
                if row['lagrum'].startswith(tempuri):
                    lagrum = row['lagrum'].replace(tempuri, canonical_uri)
                else:
                    lagrum = row['lagrum']

            if not lagrum in stuff:
                stuff[lagrum] = {}
            shortdesc = util.normalize_space(row['desc'])
            shortdesclen = self.config.shortdesclen
            if len(shortdesc) > shortdesclen:
                # first split the (markup) string at the best word boundary
                m = re.match('(.{%d,}?\S)\s'%shortdesclen, shortdesc)
                if m:
                    shortdesc = m.group()
                    # then, make sure all tags are ended properly
                    soup = BeautifulSoup(shortdesc, "html.parser")
                    # insert an ellipsis in the right place (the very last NavigableString)
                    navstrings = list(soup.find_all("p"))
                    # get the last non-empty NavigableString
                    navstrings = [x for x in navstrings if "".join(x.strings)]
                    if navstrings:
                        navstrings[-1].replace_with(str(navstrings[-1]) + "...")
                    # then convert back to markup and strip any default namespacing
                    shortdesc = str(soup).replace(' xmlns="http://www.w3.org/1999/xhtml"', '')
            link = '<b><a href="%s">%s</a></b>: ' % (row['kommentar'], row['prop'])
            if 'kommentar' not in stuff[lagrum]:
                stuff[lagrum]['kommentar'] = ""
            stuff[lagrum]['kommentar'] += shortdesc.replace("p>", "p>"+link, 1)

        # then, construct a single de-normalized rdf/xml dump, sorted
        # by root/chapter/section/paragraph URI:s. We do this using
        # raw XML, not RDFlib, to avoid normalizing the graph -- we
        # need repetition in order to make the XSLT processing simple.
        # FIXME: Preferred way would be to serialize the RDF graph as GRIT
        
        start = time()
        # compatibility hack to enable lxml to process qnames for namespaces
        def ns(string):
            if ":" in string:
                prefix, tag = string.split(":", 1)
                return "{%s}%s" % (str(self.ns[prefix]), tag)

        reversename = {'http://rinfo.lagrummet.se/ns/2008/11/rinfo/publ#inforsI': 'rpubl:isEnactedBy',
                       'http://rinfo.lagrummet.se/ns/2008/11/rinfo/publ#ersatter': 'rpubl:isChangedBy',
                       'http://rinfo.lagrummet.se/ns/2008/11/rinfo/publ#upphaver': 'rpubl:isRemovedBy'}

        root_node = etree.Element(ns("rdf:RDF"), nsmap=self.ns)

        for l in sorted(list(stuff.keys()), key=util.split_numalpha):
            lagrum_node = etree.SubElement(root_node, ns("rdf:Description"))
            lagrum_node.set(ns("rdf:about"), l)
            if 'rattsfall' in stuff[l]:
                for r in stuff[l]['rattsfall']:
                    islagrumfor_node = etree.SubElement(
                        lagrum_node, ns("rpubl:isLagrumFor"))
                    rattsfall_node = etree.SubElement(
                        islagrumfor_node, ns("rdf:Description"))
                    rattsfall_node.set(ns("rdf:about"), r['uri'])
                    id_node = etree.SubElement(rattsfall_node, ns("dcterms:identifier"))
                    id_node.text = r['id']
                    desc_node = etree.SubElement(
                        rattsfall_node, ns("dcterms:description"))
                    desc_node.text = r['desc']
            if 'inboundlinks' in stuff[l]:
                inbound = stuff[l]['inboundlinks']
                inboundlen = len(inbound)
                prev_uri = None
                for i in range(inboundlen):
                    if "#" in inbound[i]['uri']:
                        (uri, fragment) = inbound[i]['uri'].split("#")
                    else:
                        (uri, fragment) = (inbound[i]['uri'], None)

                    # 1) if the baseuri differs from the previous one,
                    # create a new dcterms:isReferencedBy node
                    if uri != prev_uri:
                        references_node = etree.Element(ns("dcterms:isReferencedBy"))
                        # 1.1) if the baseuri is the same as the uri
                        # for the law we're generating, place it first
                        if uri == baseuri:
                            # If the uri is the same as baseuri (the law
                            # we're generating), place it first.
                            lagrum_node.insert(0, references_node)
                        else:
                            lagrum_node.append(references_node)
                    # Find out the next uri safely
                    if (i + 1 < inboundlen):
                        next_uri = inbound[i + 1]['uri'].split("#")[0]
                    else:
                        next_uri = None

                    # If uri is the same as the next one OR uri is the
                    # same as baseuri, use relative form for creating
                    # dcterms:identifier
                    # print "uri: %s, next_uri: %s, baseuri: %s" %
                    # (uri[35:],next_uri[35:],baseuri[35:])
                    if (uri == next_uri) or (uri == baseuri):
                        form = "relative"
                    else:
                        form = "absolute"

                    inbound_node = etree.SubElement(
                        references_node, ns("rdf:Description"))
                    inbound_node.set(ns("rdf:about"), inbound[i]['uri'])
                    id_node = etree.SubElement(inbound_node, ns("dcterms:identifier"))
                    id_node.text = self.display_title(inbound[i]['uri'], form)
                    prev_uri = uri

            if 'changes' in stuff[l]:
                for r in stuff[l]['changes']:
                    qname = ns(reversename[r['changetype']])
                    ischanged_node = etree.SubElement(lagrum_node, qname)
                    #rattsfall_node = etree.SubElement(islagrumfor_node, "rdf:Description")
                    # rattsfall_node.set("rdf:about",r['uri'])
                    id_node = etree.SubElement(ischanged_node, ns("rpubl:fsNummer"))
                    id_node.text = r['id'].replace("SFS ", "")
                    if r['propid']:
                        prop_node = etree.SubElement(ischanged_node, ns("rpubl:proposition"))
                        prop_node.text = " (%(proptitle)s)" % r
            if 'desc' in stuff[l]:
                desc_node = etree.SubElement(lagrum_node, ns("dcterms:description"))
                xhtmlstr = "<div xmlns='http://www.w3.org/1999/xhtml'>%s</div>" % stuff[
                    l]['desc']
                desc_node.append(etree.fromstring(xhtmlstr.encode('utf-8')))

            if 'kommentar' in stuff[l]:
                desc_node = etree.SubElement(lagrum_node, ns("rinfoex:forfattningskommentar"))
                xhtmlstr = "<div xmlns='http://www.w3.org/1999/xhtml'>%s</div>" % stuff[
                    l]['kommentar']
                desc_node.append(etree.fromstring(xhtmlstr.encode('utf-8')))

            if 'bemyndiganden' in stuff[l]:
                for myndfs in stuff[l]['bemyndiganden']:
                    bf_node = etree.Element(ns("rpubl:isBemyndigandeFor"))
                    myndfs_node = etree.SubElement(bf_node, ns("rdf:Description"))
                    myndfs_node.set(ns("rdf:about"), myndfs['uri'])
                    myndfstitle_node = etree.SubElement(myndfs_node, ns("dcterms:title"))
                    myndfstitle_node.text = myndfs['title']
                    myndfsid_node = etree.SubElement(myndfs_node, ns("dcterms:identifier"))
                    myndfsid_node.text = myndfs['identifier']
                    lagrum_node.append(bf_node)

        # tree = etree.ElementTree(root_node)
        treestring = etree.tostring(root_node, encoding="utf-8", pretty_print=True)
        with self.store.open_annotation(basefile, mode="wb") as fp:
            fp.write(treestring)
        return self.store.annotation_path(basefile)

    def display_title(self, uri, form="absolute"):
        # "https://lagen.nu/2010:1770#K1P2S1" =>
        #   "Lag (2010:1770) om blahonga, 1 kap. 2 \xa7 1 st."

        # FIXME: legaluri.parse only works with canonical uris (but
        # not even correct canonical uris, rather the canonical base
        # URI, but with old lagen-nu-style fragments). This is a
        # horrible workaround when using localized uris
        canonical_uri = uri.replace("https://lagen.nu/",
                                    "http://rinfo.lagrummet.se/publ/sfs/")
        parts = legaluri.parse(canonical_uri)
        res = ""
        for (field, label) in (('chapter', 'kap.'),
                               ('section', '\xa7'),
                               ('piece', 'st'),
                               ('item', 'p')):
            if field in parts and not (field == 'piece' and
                                       parts[field] == '1' and
                                       'item' not in parts):
                res += "%s %s " % (parts[field], label)

        # Special hack: handle references from ändrings-SFS, eg
        # "http://rinfo.lagrummet.se/publ/sfs/1998:204#L1998:204N3"
        # (legaluri should be able to parse out this information
        if not res and "#L" in uri:
            changesfs = uri.split("#L")[1]
            changeloc = changepara = None
            if "S" in changesfs:
                changesfs, changepara = changesfs.split("S", 1)
            if "N" in changesfs:
                changesfs, changeloc = changesfs.split("N", 1)
            res += "övg. best. SFS %s" % changesfs
            if changepara:
                res += " %s st" % changepara
            if changeloc:
                res += " %s p" % changeloc
                
        if form == "absolute":
            if parts['law'] not in self._document_name_cache:
                if "#" in uri:
                    uri = uri.split("#")[0]
                store = TripleStore.connect(self.config.storetype,
                                            self.config.storelocation,
                                            self.config.storerepository)
                changes = self.store_select(
                    store,
                    "sparql/sfs_title.rq",
                    uri,
                    None # self.dataset_uri()
                )
                if changes:
                    self._document_name_cache[parts[
                        'law']] = changes[0]['title']
                else:
                    self._document_name_cache[parts[
                        'law']] = "SFS %s" % parts['law']
                    # print "Cache miss for %s (%s)" % (parts['law'],
                    #                              self._document_name_cache[parts['law']])

            res += self._document_name_cache[parts['law']]
            return res
        elif form == "relative":
            return res.strip()
        else:
            raise ValueError('unknown form %s' % form)

    def _forfattningskey(self, title):
        # these last examples should probably be handled in the parse step
        title = re.sub("^/r1/ ", "", util.normalize_space(title))
        if title.startswith("/Rubriken"):
            m = re.match(
                "/Rubriken upph\xf6r att g\xe4lla U:([^/]+)/ *([^/]+)/Rubriken tr\xe4der i kraft I:([^/]+)/ *([^/]+)",
                title)
            if m:
                expdate = m.group(1)
                oldtitle = m.group(2)
                newtitle = m.group(4)
                try:
                    expdate = self.parse_iso_date(expdate)
                    if expdate <= date.today():
                        title = newtitle
                    else:
                        title = oldtitle
                except:
                    title = oldtitle

        # these are for better sorting/selecting
        title = re.sub('Kungl\. Maj:ts ', '', title)
        # if newtitle was selected above, it might not contain the SFSid eg "(2016:123)"
        title = re.sub(
            '^(Lag|F\xf6rordning|Tillk\xe4nnagivande|[kK]ung\xf6relse) ?(\([^\)]+\)|) ?(av|om|med|ang\xe5ende) ',
            '',
            title)
        title = re.sub("^\d{4} \xe5rs ", "", title)

        return title

    def facet_query(self, context):
        # override the default impl, which is driven by defined
        # facets, with a hardcoded variant that knows about the
        # relation between a consolidated document and the document
        # its consolidating
        return """PREFIX dcterms: <http://purl.org/dc/terms/>
PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX rpubl: <http://rinfo.lagrummet.se/ns/2008/11/rinfo/publ#>

SELECT DISTINCT ?uri ?rdf_type ?titel ?utgiven ?label ?creator ?issued
FROM <https://lagen.nu/dataset/sfs>
WHERE {
    ?childuri rdf:type rpubl:KonsolideradGrundforfattning .
    ?childuri rpubl:konsoliderar ?uri .
    OPTIONAL { ?uri rdf:type ?rdf_type . }
    OPTIONAL { ?uri dcterms:title ?titel . }
    OPTIONAL { ?uri rpubl:arsutgava ?utgiven . }
    OPTIONAL { ?childuri rdfs:label ?label . }
    OPTIONAL { ?childuri dcterms:creator ?creator . }
    OPTIONAL { ?childuri dcterms:issued ?issued . }

}"""


    def facets(self):
        def forfattningskey(row, binding, resource_graph):
            # "Lag (1994:1920) om allm\xe4n l\xf6neavgift" => "allm\xe4n l\xf6neavgift"
            # "Lag (2012:318) om 1996 \xe5rs Haagkonvention" => "Haagkonvention" (avoid leading year)
            return self._forfattningskey(row[binding]).lower()

        def forfattningsselector(row, binding, resource_graph):
            # "Lag (1994:1920) om allm\xe4n l\xf6neavgift" => "A"
            return forfattningskey(row, binding, resource_graph)[0].upper()

        def typelabel(row, binding, resource_graph):
            return {str(RPUBL.Lag): "lagar",
                    str(RPUBL.Forordning): "förordningar"}[row[binding]]

        return [Facet(RDF.type,
                      pagetitle="Alla %(selected)s",
                      selector=typelabel),
                Facet(RPUBL.arsutgava,
                      use_for_toc=True,
                      label="Ordnade efter utgivnings\xe5r",
                      pagetitle='F\xf6rfattningar utgivna %(selected)s',
                      dimension_label="utgiven",
                      selector_descending=True),
                Facet(DCTERMS.title,
                      label="Ordnade efter titel",
                      pagetitle='F\xf6rfattningar som b\xf6rjar p\xe5 "%(selected)s"',
                      selector=forfattningsselector,
                      identificator=forfattningsselector,
                      key=forfattningskey,
                      dimension_label="titel"),
                ] + self.standardfacets

    def _relate_fulltext_resources(self, body):
        # only return K1, K1P1 or B1, not more fine-grained resources
        # like K1P1S1N1
        return [body] + [r for r in body.findall(".//*[@about]") if re.search("#[KPBS]\d+\w?(P\d+\w?|)$", r.get("about"))]
    
    _relate_fulltext_value_cache = {}
    def _relate_fulltext_value(self, facet, resource, desc):
        def rootlabel(desc):
            return desc.getvalue(DCTERMS.identifier)
        if facet.dimension_label in ("label", "creator", "issued"):
            # "creator" and "issued" should be identical for the root
            # resource and all contained subresources. "label" can
            # change slighly.
            resourceuri = resource.get("about")
            rooturi = resourceuri.split("#")[0]
            if "#" not in resourceuri:
                if desc.getvalues(RPUBL.utfardandedatum):
                    utfardandedatum = desc.getvalue(RPUBL.utfardandedatum)
                else:
                    utfardandedatum = date(int(desc.getvalue(RPUBL.arsutgava)), 12, 31)
                self._relate_fulltext_value_cache[rooturi] = {
                    "creator": desc.getrel(RPUBL.departement),
                    "issued": utfardandedatum
                }
            if facet.dimension_label == "label":
                v = self.display_title(resourceuri)
            else:
                v = self._relate_fulltext_value_cache[rooturi][facet.dimension_label]
            return facet.dimension_label, v
        else:
            return super(SFS, self)._relate_fulltext_value(facet, resource, desc)

    from .sfs_parser import re_SectionId
    def _extract_plaintext(self, node, resources):
        plaintext = super(SFS, self)._extract_plaintext(node, resources)
        # remove leading "3 § " so that autocomplete returns more useful text objects.
        return self.re_SectionId.sub('', plaintext)

    def toc_item(self, binding, row):
        """Returns a formatted version of row, using Element objects"""
        if 'titel' not in row:
            self.log.warning("%s: titel missing" % row['uri'])
            row['titel'] = "(Titel saknas)"
        title = self._forfattningskey(row['titel'])
        res = []
        if title in row['titel']:
            idx = row['titel'].index(title)
            if idx:
                res.append(row['titel'][:idx])
        res.append(Link(title, uri=row['uri']))
        return LI(res)

    def toc_generate_page_body(self, documentlist, nav):
        # SFS, unlike most other documents, should not be presented in
        # a dl list <dt> = identifier and <dd> = title. Instead we use
        # a straight ul list
        return Body([nav,
                     UL(documentlist, **{'class': 'dl-horizontal',
                                         'role':'main'})
        ])


    news_feedsets_main_label = "Samtliga författningar"


