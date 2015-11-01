# -*- coding: utf-8 -*-
from __future__ import unicode_literals, print_function

# system libraries (+ six)
from collections import defaultdict
from datetime import datetime, date
from time import time
import codecs
import difflib
import logging
import os
import re
import sys

from six.moves import html_parser
from six.moves.urllib_parse import quote, unquote
from six import text_type as str
from ferenda.compat import OrderedDict

# 3rdparty libs
from rdflib import URIRef, Literal, RDF, Graph, BNode
from rdflib.namespace import DCTERMS, SKOS
from lxml import etree
import bs4
import requests
import requests.exceptions
from layeredconfig import LayeredConfig

# my own libraries
from ferenda import DocumentEntry, DocumentStore, TripleStore
from ferenda import TextReader, Describer, Facet
from ferenda import decorators
from ferenda.sources.legal.se import legaluri
from ferenda import util
from ferenda.errors import FerendaException, DocumentRemovedError, ParseError
from .legalref import LegalRef, LinkSubject
from . import Trips, SwedishCitationParser, RPUBL, SwedishLegalStore
from .elements import *


class IckeSFS(ParseError):
    """Raised when an act that has been published in SFS, but is not a
    proper SFS (eg N1992:31), is encountered.

    """
    # NB: This is only raised in download_to_intermediate. Should
    # perhaps be raised in download_single to avoid storing these at
    # all? There only seems to be SFSR entries for these, no fulltext
    # can be found in SFST.


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

    # FIXME: we might just add the quote call to
    # SwedishLegalSource.basefile_to_pathfrag and remove this override
    def basefile_to_pathfrag(self, basefile):
        return quote(super(SFSDocumentStore, self).basefile_to_pathfrag(basefile))

    # FIXME: ditto
    def pathfrag_to_basefile(self, pathfrag):
        return unquote(super(SFSDocumentStore, self).pathfrag_to_basefile(pathfrag))

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
    (Svensk författningssamling).
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
    basefile_regex = "(?P<basefile>\d{4}:[\d s\.]+)$"
    start_url = ("http://rkrattsbaser.gov.se/cgi-bin/thw?${HTML}=%(app)s_lst"
                 "&${OOHTML}=%(app)s_dok&${SNHTML}=%(app)s_err"
                 "&${MAXPAGE}=%(maxpage)d&${BASE}=%(base)s"
                 "&${FORD}=FIND&%%C5R=FR%%C5N+%(start)s&%%C5R=TILL+%(end)s")
    download_params = [
        {'maxpage': 101,
         'app': app,
         'base': base,
         'start': '1600',
         'end': '2008'},
        {'maxpage': 101,
         'app': app,
         'base': base,
         'start': '2009',
         'end': str(datetime.today().year)}
    ]
    # download_params is split into a list of two since the UI has a bug in that it only 
    # returns the first 10 000 hits (or so). When doing a full refresh, the 10 000:th document 
    # occurs somewhere around 2009 
 
    document_url_template = (
        "http://rkrattsbaser.gov.se/cgi-bin/thw?${OOHTML}=sfst_dok&"
        "${HTML}=sfst_lst&${SNHTML}=sfst_err&${BASE}=SFST&"
        "${TRIPSHOW}=format=THW&BET=%(basefile)s")

    document_sfsr_url_template = (
        "http://rkrattsbaser.gov.se/cgi-bin/thw?${OOHTML}=sfsr_dok&"
        "${HTML}=sfst_lst&${SNHTML}=sfsr_err&${BASE}=SFSR&"
        "${TRIPSHOW}=format=THW&BET=%(basefile)s")

    document_sfsr_change_url_template = (
        "http://rkrattsbaser.gov.se/cgi-bin/thw?${OOHTML}=sfsr_dok&"
        "${HTML}=sfst_lst&${SNHTML}=sfsr_err&${BASE}=SFSR&"
        "${TRIPSHOW}=format=THW&%%C4BET=%(basefile)s")

    xslt_template = "res/xsl/sfs.xsl"

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

    # make sure our EBNF-based parsers (which are expensive to create)
    # only gets created if they are demanded.
    @property
    def lagrum_parser(self):
        if not hasattr(self, '_lagrum_parser'):
            self._lagrum_parser = SwedishCitationParser(LegalRef(LegalRef.LAGRUM,
                                                                 LegalRef.EULAGSTIFTNING),
                                                        self.minter,
                                                        self.commondata,
                                                        allow_relative=True)
        return self._lagrum_parser

    @property
    def forarbete_parser(self):
        if not hasattr(self, '_forarbete_parser'):
            self._forarbete_parser = SwedishCitationParser(LegalRef(LegalRef.FORARBETEN),
                                                           self.minter,
                                                           self.commondata)
        return self._forarbete_parser

    @classmethod
    def get_default_options(cls):
        opts = super(SFS, cls).get_default_options()
        opts['keepexpired'] = False
        opts['revisit'] = list
        opts['next_sfsnr'] = str
        return opts

    def basefile_from_uri(self, uri):
        prefix = self.config.url + self.config.urlpath
        # tell the difference btw "1998:204/konsolidering/2010:323"
        # and "dom/nja/2008s42"
        if uri.startswith(prefix) and uri[len(prefix)].isdigit():
            rest = uri[len(prefix):].replace("_", " ")
            return rest.split("/")[0]

    def download(self, basefile=None):
        if 'skipdownload' in self.config:
            return
        if basefile:
            ret = self.download_single(basefile)
        # following is copied from supers' download
        elif self.config.refresh or ('next_sfsnr' not in self.config):
            ret = super(SFS, self).download(basefile)
            self._set_last_sfsnr()
        else:
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
                    self.log.info(
                        'Peeking for SFS %s:%s' %
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
        self.log.info('Looking for %s' % wanted_sfs_nr)
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
                    # initial grundförfattning - varken
                    # "Uppdaterad T.O.M. eller "Upphävd av" ska
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
                        self.log.warning("    Text updated to %s, not %s" %
                                         (uppdaterad_tom, wanted_sfs_nr))
                        raise InteUppdateradSFS(wanted_sfs_nr)
        else:
            raise InteExisterandeSFS(wanted_sfs_nr)

    def _check_for_sfs(self, year, nr):
        """Givet ett SFS-nummer, returnera en lista med alla
        SFS-numret för dess grundförfattningar. Normalt sett har en
        ändringsförfattning bara en grundförfattning, men för vissa
        (exv 2008:605) finns flera. Om SFS-numret inte finns alls,
        returnera en tom lista."""
        # Titta först efter grundförfattning
        self.log.debug('    Looking for base act')
        grundforf = []
        basefile = "%s:%s" % (year, nr)
        url = self.document_sfsr_url_template % {'basefile': basefile}
        t = TextReader(string=requests.get(url).text)
        try:
            t.cue("<p>Sökningen gav ingen träff!</p>")
        except IOError:  # hurra!
            grundforf.append("%s:%s" % (year, nr))
            return grundforf

        # Sen efter ändringsförfattning
        self.log.debug('    Looking for change act')
        url = self.document_sfsr_change_url_template % {'basefile': basefile}
        t = TextReader(string=requests.get(url).text)
        try:
            t.cue("<p>Sökningen gav ingen träff!</p>")
            self.log.debug('    Found no change act')
            return grundforf
        except IOError:
            t.seek(0)
            try:
                t.cuepast('<input type="hidden" name="BET" value="')
                grundforf.append(t.readto("$"))
                self.log.debug('    Found change act (to %s)' %
                               grundforf[-1])
                return grundforf
            except IOError:
                t.seek(0)
                page = t.read(sys.maxsize)
                for m in re.finditer('>(\d+:[\d\w\. ]+)</a>', page):
                    grundforf.append(m.group(1))
                    self.log.debug('    Found change act (to %s)'
                                   % grundforf[-1])
                return grundforf

    def download_single(self, basefile, url=None):
        """Laddar ner senaste konsoliderade versionen av
        grundförfattningen med angivet SFS-nr. Om en tidigare version
        finns på disk, arkiveras den. Returnerar det SFS-nummer till
        vilket författningen uppdaterats."""
        self.log.debug('Attempting to download %s' % (basefile))

        sfst_url = self.document_url_template % {'basefile': basefile.replace(" ", "+")}
        sfsr_url = self.document_sfsr_url_template % {
            'basefile': basefile.replace(
                " ",
                "+")}
        # FIXME: a lot of code duplication compared to
        # DocumentRepository.download_single. Maybe particularly the
        # DocumentEntry juggling should go into download_if_needed()?
        created = not os.path.exists(self.store.downloaded_path(basefile))
        updated = False
        if self.download_if_needed(sfst_url, basefile):
            if created:
                self.log.info("%s: downloaded from %s" % (basefile, sfst_url))
            else:
                self.log.info(
                    "%s: downloaded new version from %s" % (basefile, sfst_url))
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
        entry.orig_url = sfst_url
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
                    self.log.info('        %s har ändrats (%s -> %s)' % (
                        basefile, old_uppdaterad_tom, uppdaterad_tom))
                    self._archive(sfst_file, basefile, old_uppdaterad_tom)
                else:
                    self.log.info('        %s har ändrats (gammal '
                                  'checksum %s)' % (basefile, old_checksum))
                    self._archive(sfst_file,
                                  basefile, old_uppdaterad_tom, old_checksum)

                # replace the current file, regardless of wheter
                # we've updated it or not
                util.robust_rename(sfst_tempfile, sfst_file)
            elif upphavd_genom:
                self.log.info('        %s har upphävts' % (basefile))

            else:
                self.log.debug('        %s har inte ändrats (gammal '
                               'checksum %s)' % (basefile, old_checksum))
        else:
            util.robust_rename(sfst_tempfile, sfst_file)

        sfsr_url = ("http://62.95.69.15/cgi-bin/thw?${OOHTML}=sfsr_dok&"
                    "${HTML}=sfst_lst&${SNHTML}=sfsr_err&${BASE}=SFSR&"
                    "${TRIPSHOW}=format=THW&BET=%s" % basefile.replace(" ", "+"))
        sfsr_file = self.store.register_path(basefile)
        if (old_uppdaterad_tom and
                old_uppdaterad_tom != uppdaterad_tom):
            self._archive(sfsr_file, basefile, old_uppdaterad_tom)

        self.download_if_needed(sfsr_url, basefile, filename=sfsr_file)

        if upphavd_genom:
            self.log.info(
                '        %s är upphävd genom %s' % (basefile, upphavd_genom))
            return upphavd_genom
        elif uppdaterad_tom:
            self.log.info(
                '        %s är uppdaterad tom %s' % (basefile, uppdaterad_tom))
            return uppdaterad_tom
        else:
            self.log.info(
                '        %s är varken uppdaterad eller upphävd' % (basefile))
            return None

    def _find_uppdaterad_tom(self, sfsnr, filename=None, reader=None):
        if not reader:
            reader = TextReader(filename, encoding='iso-8859-1')
        try:
            reader.cue("&Auml;ndring inf&ouml;rd:<b> t.o.m. SFS")
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
        reader = TextReader(filename, encoding='iso-8859-1')
        try:
            reader.cue("upph&auml;vts genom:<b> SFS")
            l = reader.readline()
            m = re.search('(\d+:\s?\d+)', l)
            if m:
                return m.group(1)
            else:
                return None
        except IOError:
            return None

    def _checksum(self, filename):
        """MD5-checksumman för den angivna filen"""
        import hashlib
        c = hashlib.md5()
        try:
            c.update(util.readfile(filename, encoding='iso-8859-1'))
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
            t = TextReader(string=fp.read(2048))
            fp.close()
            uppdaterad_tom = self._find_uppdaterad_tom(basefile, reader=t)
            doc.uri = self.canonical_uri(basefile, uppdaterad_tom)
        return doc

    def canonical_uri(self, basefile, konsolidering=False):
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
        res = self.minter.space.coin_uri(resource)
        # create eg "https://lagen.nu/sfs/2013:460/konsolidering" if
        # konsolidering = True instead of a issued date.
        # FIXME: This should be done in CoIN entirely
        if konsolidering is True:
            res = res.rsplit("/", 1)[0]
        return res

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
        # Check to see if this might not be a proper SFS at all
        # (from time to time, other agencies publish their stuff
        # in SFS - this seems to be handled by giving those
        # documents a SFS nummer on the form "N1992:31". Filter
        # these out.
        if basefile.startswith('N'):
            raise IckeSFS("%s is not a regular SFS" % basefile)
        filename = self.store.downloaded_path(basefile)
        try:
            # t = TextReader(filename, encoding="iso-8859-1")
            t = TextReader(filename, encoding="iso-8859-1")
        except IOError:
            self.log.warning("%s: Fulltext is missing" % basefile)
            # FIXME: This code needs to be rewritten
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
        # Check to see if the Författning has been revoked (using
        # plain fast string searching, no fancy HTML parsing and
        # traversing)
        if not self.config.keepexpired:
            try:
                t.cuepast('<i>Författningen är upphävd/skall upphävas: ')
                datestr = t.readto('</i></b>')
                if datetime.strptime(datestr, '%Y-%m-%d') < datetime.today():
                    self.log.debug('%s: Expired' % basefile)
                    raise UpphavdForfattning("%s is an expired SFS" % basefile)
                t.seek(0)
            except IOError:
                t.seek(0)
        t.cuepast('<pre>')
        # remove &auml; et al
        hp = html_parser.HTMLParser()
        txt = hp.unescape(t.readto('</pre>'))
        if '\r\n' not in txt:
            txt = txt.replace('\n', '\r\n')
        re_tags = re.compile("</?\w{1,3}>")
        txt = re_tags.sub('', txt)
        # add ending CRLF aids with producing better diffs
        txt += "\r\n"
        util.writefile(self.store.intermediate_path(basefile), txt,
                       encoding="iso-8859-1")
        return codecs.open(self.store.intermediate_path(basefile),
                           encoding="iso-8859-1")

    def patch_if_needed(self, fp, basefile):
        fp = super(SFS, self).patch_if_needed(fp, basefile)
        # find out if patching occurred and record the patch description
        # (maybe this should only be done in the lagen.nu.SFS subclass?
        # the canonical SFS repo should maybe not have patches?)
        if None and patchdesc:
            desc.value(self.ns['rinfoex'].patchdescription,
                       patchdesc)
        return fp

    def extract_head(self, fp, basefile):
        """Parsear ut det SFSR-registret som innehåller alla ändringar
        i lagtexten från HTML-filer"""

        # NB: We should really call self.store.register_path, but that
        # custom func isn't mocked by ferenda.testutil.RepoTester,
        # and downloaded_path is. So we call that one and munge it.
        filename = self.store.downloaded_path(basefile).replace(
            "/downloaded/", "/register/")
        with codecs.open(filename, encoding="iso-8859-1") as rfp:
            soup = bs4.BeautifulSoup(rfp.read(), "lxml")
        # do we really have a registry?
        notfound = soup.find(text="Sökningen gav ingen träff!")
        if notfound:
            raise InteExisterandeSFS(str(notfound))
        textheader = fp.read(2048)
        if not isinstance(textheader, str):
            # Depending on whether the fp is opened through standard
            # open() or bz2.BZ2File() in self.parse_open(), it might
            # return bytes or unicode strings. This seem to be a
            # problem in BZ2File (or how we use it). Just roll with it.
            textheader = textheader.decode("iso-8859-1")
        idx = textheader.index(b"\r\n" * 4)
        fp.seek(idx + 8)
        reader = TextReader(string=textheader,
                            linesep=TextReader.DOS)
        subreader = reader.getreader(
            reader.readchunk, reader.linesep * 4)
        return soup, subreader.getiterator(subreader.readparagraph)

    def extract_metadata(self, datatuple, basefile):
        soup, reader = datatuple
        d = self.metadata_from_basefile(basefile)
        d.update(self.extract_metadata_register(soup, basefile))
        d.update(self.extract_metadata_header(reader, basefile))
        return d

    def extract_metadata_register(self, soup, basefile):
        d = {}
        rubrik = util.normalize_space(soup.body('table')[2].text)
        changes = soup.body('table')[3:-2]
        for table in changes:
            sfsnr = table.find(text="SFS-nummer:").find_parent(
                "td").find_next_sibling("td").text.strip()
            docuri = self.canonical_uri(sfsnr)
            rowdict = {}
            parts = sfsnr.split(":")
            d[docuri] = {
                "dcterms:publisher": "Regeringskansliet",
                "rpubl:arsutgava": parts[0],
                "rpubl:beslutadAv": "Regeringskansliet",
                "rpubl:forfattningssamling": "SFS",
                "rpubl:lopnummer": parts[1]
            }
                         # URIRef(self.lookup_resource("SFS", SKOS.altLabel))}
            g = self.make_graph()  # used for qname lookup only
            for row in table('tr'):
                key = row.td.text.strip()
                if key.endswith(":"):
                    key = key[:-1]  # trim ending ":"
                elif key == '':
                    continue
                # FIXME: the \xa0 (&nbsp;) to space conversion should
                # maye be part of normalize_space?
                val = util.normalize_space(row('td')[1].text)
                if val == "":
                    continue
                rowdict[key] = val
            # first change does not contain a "Rubrik" key. Fake it.
            if 'Rubrik' not in rowdict and rubrik:
                rowdict['Rubrik'] = rubrik
                rubrik = None
            for key, val in rowdict.items():
                if key == 'SFS-nummer':
                    (arsutgava, lopnummer) = val.split(":")
                    d[docuri]["dcterms:identifier"] = "SFS " + val
                    d[docuri]["rpubl:arsutgava"] = arsutgava
                    d[docuri]["rpubl:lopnummer"] = lopnummer

                elif key == 'Ansvarig myndighet':
                    d[docuri]["rpubl:departement"] = val
                    # FIXME: Sanitize this in
                    # sanitize_metadata->sanitize_department, lookup
                    # resource in polish_metadata
                elif key == 'Rubrik':
                    # Change acts to Balkar never contain the SFS no
                    # of the Balk.
                    if basefile not in val and not val.endswith("balken"):
                        self.log.warning(
                            "%s: Base SFS %s not in title %r" % (basefile,
                                                                 basefile,
                                                                 val))
                    d[docuri]["dcterms:title"] = val
                    d[docuri]["rdf:type"] = self._forfattningstyp(val)
                elif key == 'Observera':
                    if not self.config.keepexpired:
                        if 'Författningen är upphävd/skall upphävas: ' in val:
                            dateval = datetime.strptime(val[41:51], '%Y-%m-%d')
                            if dateval < datetime.today():
                                raise UpphavdForfattning("%s is an expired SFS"
                                                         % basefile)
                    d[docuri]["rdfs:comment"] = val
                elif key == 'Ikraft':
                    d[docuri]["rpubl:ikrafttradandedatum"] = val[:10]
                elif key == 'Omfattning':
                    # First, create rdf statements for every
                    # single modified section we can find
                    for changecat in val.split('; '):
                        if (changecat.startswith('ändr.') or
                            changecat.startswith('ändr ') or
                                changecat.startswith('ändring ')):
                            pred = self.ns['rpubl'].ersatter
                        elif (changecat.startswith('upph.') or
                              changecat.startswith('upp.') or
                              changecat.startswith('utgår')):
                            pred = self.ns['rpubl'].upphaver
                        elif (changecat.startswith('ny') or
                              changecat.startswith('ikrafttr.') or
                              changecat.startswith('ikrafftr.') or
                              changecat.startswith('ikraftr.') or
                              changecat.startswith('ikraftträd.') or
                              changecat.startswith('tillägg')):
                            pred = self.ns['rpubl'].inforsI
                        elif (changecat.startswith('nuvarande') or
                              changecat.startswith('rubr. närmast') or
                              changecat in ('begr. giltighet', 'Omtryck',
                                            'omtryck', 'forts.giltighet',
                                            'forts. giltighet',
                                            'forts. giltighet av vissa best.')):
                            # some of these changecats are renames, eg
                            # "nuvarande 2, 3, 4, 5 §§ betecknas 10,
                            # 11, 12, 13, 14, 15 §§;" or
                            # "rubr. närmast efter 1 § sätts närmast
                            # före 10 §"
                            pred = None
                        else:
                            self.log.warning(
                                "%s: Okänd omfattningstyp %r" %
                                (basefile, changecat))
                            pred = None
                        old_currenturl = self.lagrum_parser._currenturl
                        self.lagrum_parser._currenturl = docuri
                        for node in self.lagrum_parser.parse_string(changecat,
                                                                    pred):
                            if hasattr(node, 'predicate'):
                                d[docuri][g.qname(node.predicate)] = node.uri
                        self.lagrum_parser._currenturl = old_currenturl
                    # Secondly, preserve the entire text
                    d[docuri]["rpubl:andrar"] = val
                elif key == 'Förarbeten':
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
                        g = Graph()
                        g.add((b, RPUBL.celexNummer, Literal(celex)))
                        celexuri = self.minter.space.coin_uri(g.resource(b))
                        if "rpubl:genomforDirektiv" not in d[docuri]:
                            d[docuri]["rpubl:genomforDirektiv"] = []
                        d[docuri]["rpubl:genomforDirektiv"].append(celexuri)
                        d[celexuri] = {"rpubl:celexNummer": celex}
                elif key == 'Tidsbegränsad':
                    d["rinfoex:tidsbegransad"] = val[:10]
                    expdate = datetime.strptime(val[:10], '%Y-%m-%d')
                    if expdate < datetime.today():
                        if not self.config.keepexpired:
                            raise UpphavdForfattning(
                                "%s is expired (time-limited) SFS" % basefile)
                else:
                    self.log.warning(
                        '%s: Obekant nyckel [\'%s\']' % basefile, key)
            utfardandedatum = self._find_utfardandedatum(sfsnr)
            if utfardandedatum:
                d[docuri]["rpubl:utfardandedatum"] = utfardandedatum
        return d

    def extract_metadata_header(self, reader, basefile):
        re_sfs = re.compile(r'(\d{4}:\d+)\s*$').search
        d = {}
        for line in reader:
            if ":" in line:
                (key, val) = [util.normalize_space(x)
                              for x in line.split(":", 1)]
            # Simple string literals
            if key == 'Rubrik':
                d["dcterms:title"] = val
            elif key == 'Övrigt':
                d["rdfs:comment"] = val
            elif key == 'SFS nr':
                identifier = "SFS " + val
                # delay actual writing to graph, since we may need to
                # amend this

            # date literals
            elif key == 'Utfärdad':
                d["rpubl:utfardandedatum"] = val[:10]
            elif key == 'Tidsbegränsad':
                # FIXME: Should be done by lagen.nu.SFS
                d["rinfoex:tidsbegransad"] = val[:10]
            elif key == 'Upphävd':
                d = datetime.strptime(val[:10], '%Y-%m-%d')
                d["rpubl:upphavandedatum"] = val[:10]
                if not self.config.keepexpired and d < datetime.today():
                    raise UpphavdForfattning("%s is an expired SFS" % basefile)

            # urirefs
            elif key == 'Departement/ myndighet':
                # this is only needed because of SFS 1942:724, which
                # has "Försvarsdepartementet, Socialdepartementet"...
                if "departementet, " in val:
                    val = val.split(", ")[0]
                d["dcterms:creator"] = val
            elif (key == 'Ändring införd' and re_sfs(val)):
                uppdaterad = re_sfs(val).group(1)
                # not sure we need to add this, since parse_metadata
                # catches the same
                d["rpubl:konsolideringsunderlag"] = [URIRef(self.canonical_uri(uppdaterad))]
                if identifier and identifier != "SFS " + uppdaterad:
                    identifier += " i lydelse enligt SFS " + uppdaterad
                d["dcterms:issued"] = uppdaterad

            elif (key == 'Omtryck' and re_sfs(val)):
                d["rinfoex:omtryck"] = self.canonical_uri(re_sfs(val).group(1))
            elif (key == 'Författningen har upphävts genom' and
                  re_sfs(val)):
                s = re_sfs(val).group(1)
                d["rinfoex:upphavdAv"] = self.canonical_uri(s)
            else:
                self.log.warning(
                    '%s: Obekant nyckel [\'%s\']' % (basefile, key))

        d["dcterms:identifier"] = identifier

        # FIXME: This is a misuse of the dcterms:issued prop in order
        # to mint the correct URI. We need to remove this somehow afterwards.
        if "dcterms:issued" not in d:
            d["dcterms:issued"] = basefile

        if "dcterms:title" not in d:
            self.log.warning("%s: Rubrik saknas" % basefile)
        return d

    def sanitize_metadata(self, attribs, basefile):
        attribs = super(SFS, self).sanitize_metadata(attribs, basefile)
        # FIXME: Needs to be recursive
        for k in "dcterms:creator", "rpubl:departement":
            if k in attribs:
                attribs[k] = self.sanitize_departement(attribs[k])
        return attribs

    def sanitize_departement(self, val):
        # to avoid "Assuming that" warnings, autoremove sub-org ids,
        # ie "Finansdepartementet S3" -> "Finansdepartementet"
        # loop until done to handle "Justitiedepartementet DOM, L5 och Å"

        cleaned = None
        while True:
            cleaned = re.sub(",? (och|[A-ZÅÄÖ\d]{1,5})$", "", val)
            if val == cleaned:
                break
            val = cleaned
        return cleaned

    def polish_metadata(self, attributes):
        # attributes will be a nested dict with some values being
        # dicts themselves. Convert the subdicts to rdflib.Resource
        # objects.
        post_count = 0
        for k in sorted(list(attributes.keys()), key=util.split_numalpha):
            if isinstance(attributes[k], dict):
                if len(attributes[k]) > 1:
                    # get a rdflib.Resource with a coined URI
                    r = super(SFS, self).polish_metadata(attributes[k])
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
        else:
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
            # lagen.nu.SFS.infer_Triples wont be able to generate a
            # owl:sameAs uri
            resource.graph.add((resource.identifier, DCTERMS.issued, Literal(datetime.today())))
        return resource


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
            reg = Register(rubrik='Ändringar och övergångsbestämmelser')
        else:
            reg = Register(rubrik='Ändringar')

        # remove the bogus dcterms:issued thing that we only added to
        # aid URI generation
        for o in doc.meta.objects(URIRef(doc.uri), DCTERMS.issued):
            if not o.datatype:
                doc.meta.remove((URIRef(doc.uri), DCTERMS.issued, o))

            # from pudb import set_trace; set_trace()
        for res in sorted(doc.meta.resource(doc.uri).objects(RPUBL.konsolideringsunderlag)):
            identifier = res.value(DCTERMS.identifier).replace("SFS ", "L")
            graph = self.make_graph()
            for s, p, o in res:
                if not isinstance(o, Literal):
                    o = o.identifier
                triple = (s.identifier, p.identifier, o)
                graph.add(triple)
                doc.meta.remove(triple)
                if p.identifier in (RPUBL.forarbete, RPUBL.genomforDirektiv):
                    if p.identifier == RPUBL.forarbete:
                        triple = (o, DCTERMS.identifier,
                                  doc.meta.value(o, DCTERMS.identifier))
                    elif p.identifier == RPUBL.genomforDirektiv:
                        triple = (o, RPUBL.celexNummer,
                                  doc.meta.value(o, RPUBL.celexNummer))
                    graph.add(triple)
                    doc.meta.remove(triple)
            rp = Registerpost(uri=res.identifier, meta=graph, id=identifier)
            reg.append(rp)
            if res.identifier in obs:
                rp.append(obs[uri])
        doc.body.append(reg)

        # finally, set the uri of the main body object to a better value
        doc.body.uri = str(doc.meta.value(URIRef(doc.uri), RPUBL.konsoliderar))

    def _forfattningstyp(self, forfattningsrubrik):
        forfattningsrubrik = re.sub(" *\(\d{4}:\d+\)", "", forfattningsrubrik)
        if (forfattningsrubrik.startswith('Lag ') or
            (forfattningsrubrik.endswith('lag') and
             not forfattningsrubrik.startswith('Förordning')) or
            forfattningsrubrik.endswith('balk')):
            return self.ns['rpubl'].Lag
        else:
            return self.ns['rpubl'].Forordning

    def _find_utfardandedatum(self, sfsnr):
        # FIXME: Code to instantiate a SFSTryck object and muck about goes here
        fake = {'2013:363': date(2013, 5, 23),
                '2008:344': date(2008, 5, 22),
                '2009:1550': date(2009, 12, 17),
                '2013:411': date(2013, 5, 30),
                }
        return fake.get(sfsnr, None)

    def extract_body(self, fp, basefile):
        bodystring = fp.read()
        # see comment in extract_head for why we must handle both
        # bytes- and str-files
        if not isinstance(bodystring, str):
            bodystring = bodystring.decode("iso-8859-1")
        # replace bogus emdash contained in some text files before
        # loading into TextReader (our need to do this before creating
        # the TextReader is why we can't do it in sanitize_body
        reader = TextReader(string=bodystring.replace("\u2013", "-"),
                            linesep=TextReader.DOS)
        reader.autostrip = True
        return reader

    # FIXME: should get hold of a real LNKeyword repo object and call
    # it's canonical_uri()
    def _term_to_subject(self, term):
        capitalized = term[0].upper() + term[1:]
        return 'https://lagen.nu/concept/%s' % capitalized.replace(' ', '_')

    # this struct is intended to be overridable
    ordinalpredicates = {
        Kapitel: "rpubl:kapitelnummer",
        Paragraf: "rpubl:paragrafnummer",
    }

    def construct_id(self, node, state):
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
                node.uri = uri
                if "#" in uri:
                    node.id = uri.split("#", 1)[1]
                pass
        state['parent'] = node
        return state

    re_Bullet = re.compile(r'^(\-\-?|\x96) ')
    # NB: these are redefinitions of regex objects in sfs_parser.py
    re_DottedNumber = re.compile(r'^(\d+ ?\w?)\. ')
    re_Bokstavslista = re.compile(r'^(\w)\) ')
    re_definitions = re.compile(
        r'^I (lagen|förordningen|balken|denna lag|denna förordning|denna balk|denna paragraf|detta kapitel) (avses med|betyder|används följande)').match
    re_brottsdef = re.compile(
        r'\b(döms|dömes)(?: han)?(?:,[\w\xa7 ]+,)? för ([\w ]{3,50}) till (böter|fängelse)', re.UNICODE).search
    re_brottsdef_alt = re.compile(
        r'[Ff]ör ([\w ]{3,50}) (döms|dömas) till (böter|fängelse)', re.UNICODE).search
    re_parantesdef = re.compile(r'\(([\w ]{3,50})\)\.', re.UNICODE).search
    re_loptextdef = re.compile(
        r'^Med ([\w ]{3,50}) (?:avses|förstås) i denna (förordning|lag|balk)', re.UNICODE).search
    def find_definitions(self, element, find_definitions):
        if not isinstance(element, CompoundElement):
            return None
        find_definitions_recursive = find_definitions
        # Hitta begreppsdefinitioner
        if isinstance(element, Paragraf):
            # kolla om första stycket innehåller en text som
            # antyder att definitioner följer
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

        # Hitta lagrumshänvisningar + definitioner
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
                            '"%s" är nog en definition (1)' % term)
                elif isinstance(element, Stycke):

                    # Case 1: "antisladdsystem: ett tekniskt stödsystem"
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
                                self.log.debug('"%s" är nog en definition (2.1)' % term)

                    # case 2: "Den som berövar annan livet, döms
                    # för mord till fängelse"
                    m = self.re_brottsdef(elementtext)
                    if m:
                        term = m.group(2)
                        self.log.debug(
                            '"%s" är nog en definition (2.2)' % term)

                    # case 3: "För miljöbrott döms till böter"
                    m = self.re_brottsdef_alt(elementtext)
                    if m:
                        term = m.group(1)
                        self.log.debug(
                            '"%s" är nog en definition (2.3)' % term)

                    # case 4: "Inteckning får på ansökan av
                    # fastighetsägaren dödas (dödning)."
                    m = self.re_parantesdef(elementtext)
                    if m:
                        term = m.group(1)
                        # print("%s: %s" %  (basefile, elementtext))
                        self.log.debug(
                            '"%s" är nog en definition (2.4)' % term)

                    # case 5: "Med detaljhandel avses i denna lag
                    # försäljning av läkemedel"
                    m = self.re_loptextdef(elementtext)
                    if m:
                        term = m.group(1)
                        self.log.debug(
                            '"%s" är nog en definition (2.5)' % term)

                elif isinstance(element, Listelement):
                    # remove
                    for rx in (self.re_Bullet,
                               self.re_DottedNumber,
                               self.re_Bokstavslista):
                        elementtext = rx.sub('', elementtext)
                    term = elementtext.split(termdelimiter)[0]
                    self.log.debug('"%s" är nog en definition (3)' % term)

                # Longest legitimate term found "Valutaväxling,
                # betalningsöverföring och annan finansiell
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
                ('rpubl:kapitelnummer', 'rpubl:paragrafnummer')]
        else:
            self.skipfragments = [('rinfoex:avdelningnummer',
                                   'rpubl:kapitelnummer')]
        return False  # run only on root element

    def get_parser(self, basefile, sanitized):
        # this should work something like offtryck_parser
        from .sfs_parser import make_parser
        return make_parser(sanitized, self.log, self.trace)

    def visitor_functions(self, basefile):
        return ((self.set_skipfragments, None),
                (self.construct_id, {'basefile': basefile}),
                (self.find_definitions, True))


    _document_name_cache = {}

    def store_select(self, store, query_template, uri, context=None):
        params = {'uri': uri,
                  'context': context}
        with self.resourceloader(query_template):
            sq = fp.read() % params
        # FIXME: Only FusekiStore.select supports (or needs) uniongraph
        if context:
            uniongraph = False
        else:
            uniongraph = True
        return store.select(sq, "python", uniongraph=uniongraph)

    # FIXME: Copied verbatim from keyword.py
    def time_store_select(self, store, query_template, basefile,
                          context=None, label="things"):
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
                                       context)
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
                                           "res/sparql/sfs_rattsfallsref.rq",
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
                                              "res/sparql/sfs_inboundlinks.rq",
                                              basefile,
                                              sfsdataset,
                                              "law references")
        stuff[baseuri]['inboundlinks'] = []

        # mapping <http://rinfo.lagrummet.se/publ/sfs/1999:175> =>
        # "Rättsinformationsförordning (1999:175)"
        specifics = {}
        for row in inboundlinks:
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
                                          "res/sparql/sfs_wikientries.rq",
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

        # pprint(wikidesc)
        # (4. eurlex.nu data (mapping CELEX ids to titles))
        # (5. Propositionstitlar)
        # 6. change entries for each section
        # NOTE: The SFS RDF data does not yet contain change entries, this query
        # always returns 0 rows
        changes = self.time_store_select(store,
                                         "res/sparql/sfs_changes.rq",
                                         basefile,
                                         sfsdataset,
                                         "change annotations")

        for row in changes:
            lagrum = row['lagrum']
            if not lagrum in stuff:
                stuff[lagrum] = {}
            if not 'changes' in stuff[lagrum]:
                stuff[lagrum]['changes'] = []
            stuff[lagrum]['changes'].append({'uri': row['change'],
                                             'id': row['id']})

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
                    # create a new dcterms:references node
                    if uri != prev_uri:
                        references_node = etree.Element(ns("dcterms:references"))
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
                    ischanged_node = etree.SubElement(
                        lagrum_node, ns("rpubl:isChangedBy"))
                    #rattsfall_node = etree.SubElement(islagrumfor_node, "rdf:Description")
                    # rattsfall_node.set("rdf:about",r['uri'])
                    id_node = etree.SubElement(ischanged_node, ns("rpubl:fsNummer"))
                    id_node.text = r['id']
            if 'desc' in stuff[l]:
                desc_node = etree.SubElement(lagrum_node, ns("dcterms:description"))
                xhtmlstr = "<div xmlns='http://www.w3.org/1999/xhtml'>%s</div>" % stuff[
                    l]['desc']
                desc_node.append(etree.fromstring(xhtmlstr.encode('utf-8')))

        # tree = etree.ElementTree(root_node)
        treestring = etree.tostring(root_node, encoding="utf-8", pretty_print=True)
        with self.store.open_annotation(basefile, mode="wb") as fp:
            fp.write(treestring)
        return self.store.annotation_path(basefile)

    def display_title(self, uri, form="absolute"):
        # "https://lagen.nu/2010:1770#K1P2S1" => "Lag (2010:1770) om blahonga, 1 kap. 2 § 1 st."
        parts = legaluri.parse(uri)
        res = ""
        for (field, label) in (('chapter', 'kap.'),
                               ('section', '\xa7'),
                               ('piece', 'st'),
                               ('item', 'p')):
            if field in parts and not (field == 'piece' and
                                       parts[field] == '1' and
                                       'item' not in parts):
                res += "%s %s " % (parts[field], label)

        if form == "absolute":
            if parts['law'] not in self._document_name_cache:
                if "#" in uri:
                    uri = uri.split("#")[0]
                store = TripleStore.connect(self.config.storetype,
                                            self.config.storelocation,
                                            self.config.storerepository)

                changes = self.store_select(
                    store,
                    "res/sparql/sfs_title.rq",
                    uri,
                    self.dataset_uri())
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
        title = re.sub("^/r1/ ", "", title)
        if title.startswith("/Rubriken"):
            m = re.match(
                "/Rubriken upphör att gälla U:([^/]+)/ *([^/]+)/Rubriken träder i kraft I:([^/]+)/ *([^/]+)",
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
        title = re.sub(
            '^(Lag|Förordning|Tillkännagivande|[kK]ungörelse) ?\([^\)]+\) ?(av|om|med|angående) ',
            '',
            title)
        title = re.sub("^\d{4} års ", "", title)

        return title

    def facets(self):
        def forfattningskey(row, binding, resource_graph):
            # "Lag (1994:1920) om allmän löneavgift" => "allmän löneavgift"
            # "Lag (2012:318) om 1996 års Haagkonvention" => "Haagkonvention" (avoid leading year)
            return self._forfattningskey(row[binding]).lower()

        def forfattningsselector(row, binding, resource_graph):
            # "Lag (1994:1920) om allmän löneavgift" => "a"
            return forfattningskey(row, binding, resource_graph)[0]

        return [Facet(RDF.type),
                # for newsfeeds, do a facet that checks the type of
                # the rpubl:konsoliderar object of this one
                Facet(DCTERMS.title,
                      label="Ordnade efter titel",
                      pagetitle='Författningar som börjar på "%(selected)s"',
                      selector=forfattningsselector,
                      identificator=forfattningsselector,
                      key=forfattningskey,
                      dimension_label="titel"),
                Facet(DCTERMS.issued,
                      label="Ordnade efter utgivningsår",
                      pagetitle='Författningar utgivna %(selected)s',
                      key=forfattningskey,
                      dimension_label="utgiven")
                ]

    def toc_item(self, binding, row):
        """Returns a formatted version of row, using Element objects"""
        title = self._forfattningskey(row['titel'])
        res = []
        if title in row['titel']:
            idx = row['titel'].index(title)
            if idx:
                res.append(row['titel'][:idx])
        res.append(Link(title, uri=row['uri']))
        return res


