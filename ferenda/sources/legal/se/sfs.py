# -*- coding: utf-8 -*-
from __future__ import unicode_literals, print_function

"""Hanterar (konsoliderade) författningar i SFS från Regeringskansliet
rättsdatabaser.
"""

# system libraries (+ six)
from collections import defaultdict
from datetime import datetime, date
from tempfile import mktemp
from time import time, sleep
import cgi
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
from rdflib import Graph, Namespace, URIRef, RDF, Literal
from lxml import etree
from lxml.builder import ElementMaker
import bs4
import requests

# my own libraries
from . import Trips, RPUBL
from ferenda import DocumentEntry, DocumentStore
from ferenda import TextReader, Describer
from ferenda import decorators
from ferenda.sources.legal.se import legaluri
from ferenda import util, LayeredConfig
from ferenda.elements import CompoundElement
from ferenda.elements import OrdinalElement
from ferenda.elements import TemporalElement
from ferenda.elements import UnicodeElement
from ferenda.errors import DocumentRemovedError, ParseError
from ferenda.sources.legal.se.legalref import LegalRef, LinkSubject

E = ElementMaker(namespace="http://www.w3.org/1999/xhtml")
# Objektmodellen för en författning är uppbyggd av massa byggstenar
# (kapitel, paragrafen, stycken m.m.) där de allra flesta är någon
# form av lista. Även stycken är listor, dels då de kan innehålla
# lagrumshänvisningar i den löpande texten, som uttrycks som
# Link-objekt mellan de vanliga unicodetextobjekten, dels då de kan
# innehålla en punkt- eller nummerlista.
#
# Alla klasser ärver från antingen CompoundElement (som är en list med
# lite extraegenskaper) eller UnicodeElement (som är en unicode med
# lite extraegenskaper)
#
# De kan även ärva från TemporalElement om det är ett objekt som kan
# upphävas eller träda ikraft (exv paragrafer och rubriker, men inte
# enskilda stycken) och/eller OrdinalElement om det är ett objekt
# som har nån sorts löpnummer, dvs kan sorteras på ett meningsfullt
# sätt (exv kapitel och paragrafer, men inte rubriker).


class Forfattning(CompoundElement, TemporalElement):

    """Grundklass för en konsoliderad författningstext."""
    tagname = "body"
    classname = "konsolideradforfattning"

# Rubrike är en av de få byggstenarna som faktiskt inte kan innehålla
# något annat (det förekommer "aldrig" en hänvisning i en
# rubriktext). Den ärver alltså från UnicodeElement, inte
# CompoundElement.


class Rubrik(UnicodeElement, TemporalElement):

    """En rubrik av något slag - kan vara en huvud- eller underrubrik
    i löptexten, en kapitelrubrik, eller något annat"""
    fragment_label = "R"

    def _get_tagname(self):
        if hasattr(self, 'type') and self.type == "underrubrik":
            return "h3"
        else:
            return "h2"
    tagname = property(_get_tagname, "Docstring here")

    def __init__(self, *args, **kwargs):
        self.id = kwargs.get("id", None)
        self.uri = kwargs.get("uri", None)
        super(Rubrik, self).__init__(*args, **kwargs)


class Stycke(CompoundElement):
    fragment_label = "S"
    tagname = "p"
    typeof = "rinfoex:Stycke"  # not defined by the rpubl vocab

    def __init__(self, *args, **kwargs):
        self.id = kwargs.get("id", None)
        self.uri = kwargs.get("uri", None)
        super(Stycke, self).__init__(*args, **kwargs)


class Strecksatslista (CompoundElement):
    tagname = "ul"
    classname = "strecksatslista"


class NumreradLista (CompoundElement):
    tagname = "ul"  # These list are not always monotonically
                   # increasing, which a <ol> requrires
    classname = "numreradlista"


class Bokstavslista (CompoundElement):
    tagname = "ul"  # See above
    classname = "bokstavslista"


# class Preformatted(UnicodeElement):
#     pass


class Tabell(CompoundElement):
    tagname = "table"


class Tabellrad(CompoundElement, TemporalElement):
    tagname = "tr"


class Tabellcell(CompoundElement):
    tagname = "td"


class Avdelning(CompoundElement, OrdinalElement):
    tagname = "div"
    fragment_label = "A"

    def __init__(self, *args, **kwargs):
        self.id = kwargs.get("id", None)
        self.uri = kwargs.get("uri", None)
        super(Avdelning, self).__init__(*args, **kwargs)


class UpphavtKapitel(UnicodeElement, OrdinalElement):

    """Ett UpphavtKapitel är annorlunda från ett upphävt Kapitel på så
    sätt att inget av den egentliga lagtexten finns kvar, bara en
    platshållare"""


class Kapitel(CompoundElement, OrdinalElement):
    fragment_label = "K"
    dagname = "div"
    typeof = "rpubl:Kapitel"  # FIXME: This is qname string, not
                             # rdflib.URIRef (which would be better),
                             # since as_xhtml doesn't have access to
                             # a graph with namespace bindings, which
                             # is required to turn a URIRef to a
                             # qname

    def __init__(self, *args, **kwargs):
        self.id = kwargs.get("id", None)
        self.uri = kwargs.get("uri", None)
        super(Kapitel, self).__init__(*args, **kwargs)


class UpphavdParagraf(UnicodeElement, OrdinalElement):
    pass

# en paragraf har inget "eget" värde, bara ett nummer och ett eller
# flera stycken


class Paragraf(CompoundElement, OrdinalElement):
    fragment_label = "P"
    tagname = "div"
    typeof = "rpubl:Paragraf"  # FIXME: see above

    def __init__(self, *args, **kwargs):
        self.id = kwargs.get("id", None)
        self.uri = kwargs.get("uri", None)
        super(Paragraf, self).__init__(*args, **kwargs)

# kan innehålla nästlade numrerade listor


class Listelement(CompoundElement, OrdinalElement):
    fragment_label = "N"
    tagname = "li"

    def __init__(self, *args, **kwargs):
        self.id = kwargs.get("id", None)
        self.uri = kwargs.get("uri", None)
        super(Listelement, self).__init__(*args, **kwargs)


class Overgangsbestammelser(CompoundElement):

    def __init__(self, *args, **kwargs):
        self.rubrik = kwargs.get('rubrik', 'Övergångsbestämmelser')
        super(Overgangsbestammelser, self).__init__(*args, **kwargs)


class Overgangsbestammelse(CompoundElement, OrdinalElement):
    tagname = "div"
    fragment_label = "L"

    def __init__(self, *args, **kwargs):
        self.id = kwargs.get("id", None)
        self.uri = kwargs.get("uri", None)
        super(Overgangsbestammelse, self).__init__(*args, **kwargs)


class Bilaga(CompoundElement):
    fragment_label = "B"

    def __init__(self, *args, **kwargs):
        self.id = kwargs.get("id", None)
        self.uri = kwargs.get("uri", None)
        super(Bilaga, self).__init__(*args, **kwargs)


class Register(CompoundElement):

    """Innehåller lite metadata om en grundförfattning och dess
    efterföljande ändringsförfattningar"""
    tagname = "div"
    classname = "register"

    def __init__(self, *args, **kwargs):
        self.rubrik = kwargs.get('rubrik', None)
        super(Register, self).__init__(*args, **kwargs)

    def as_xhtml(self, uri=None):
        res = super(Register, self).as_xhtml()
        res.insert(0, E('h1', self.rubrik))
        return res


class Registerpost(CompoundElement):

    """Metadata for a particular Grundforfattning or Andringsforfattning in the form of a rdflib graph, optionally with a Overgangsbestammelse."""
    tagname = "div"
    classname = "registerpost"

    def __init__(self, *args, **kwargs):
        self.id = kwargs.get("id", None)
        self.uri = kwargs.get("uri", None)
        super(Registerpost, self).__init__(*args, **kwargs)

    def as_xhtml(self, uri=None):
        # FIXME: Render this better (particularly the rpubl:andring
        # property -- should be parsed and linked)
        return super(Registerpost, self).as_xhtml()


class IckeSFS(ParseError):

    """Slängs när en författning som inte är en egentlig
    SFS-författning parsas"""


class UpphavdForfattning(DocumentRemovedError):
    pass


class IdNotFound(ParseError):
    pass

DCT = Namespace(util.ns['dct'])
XSD = Namespace(util.ns['xsd'])
RINFOEX = Namespace("http://lagen.nu/terms#")


class SFSDocumentStore(DocumentStore):

    def basefile_to_pathfrag(self, basefile):
        return quote(basefile.replace(":", "/"))

    def pathfrag_to_basefile(self, pathfrag):
        return unquote(pathfrag.replace("\\", "/").replace("/", ":"))

    def register_path(self, basefile):
        return self.path(basefile, "register", ".html")

    def metadata_path(self, basefile):
        return self.path(basefile, "metadata", ".html")


class SFS(Trips):

    """Documentation to come.

    A note about logging:

    There are four additional loggers available ('paragraf', 'tabell',
    'numlist' and 'rubrik'). By default, manager.py turns them off
    unless config.trace.[logname] is set. Do something like

    ./ferenda-build.py sfs parse 2009:924 --force --sfs-trace-rubrik

    (sets the sfs.rubrik logger level to DEBUG) or
    
    ./ferenda-build.py sfs parse 2009:924 --force --sfs-trace-tabell=INFO

    """
    alias = "sfs"

    app = "sfst"  # dir, prop, sfst
    base = "SFSR"  # DIR, THWALLPROP, SFSR

    start_url = ("http://rkrattsbaser.gov.se/cgi-bin/thw?${HTML}=%(app)s_lst"
                 "&${OOHTML}=%(app)s_dok&${SNHTML}=%(app)s_err"
                 "&${MAXPAGE}=%(maxpage)d&${BASE}=%(base)s"
                 "&${FORD}=FIND&%%C5R=FR%%C5N+%(start)s&%%C5R=TILL+%(end)s")

    download_params = [{'maxpage': 101,
                        'app': app,
                        'base': base,
                        'start': '1600',
                        'end': '2008'},
                       {'maxpage': 101,
                        'app': app,
                        'base': base,
                        'start': '2009',
                        'end': str(datetime.today().year)}]

    document_url_template = ("http://rkrattsbaser.gov.se/cgi-bin/thw?${OOHTML}=sfst_dok&"
                             "${HTML}=sfst_lst&${SNHTML}=sfst_err&${BASE}=SFST&"
                             "${TRIPSHOW}=format=THW&BET=%(basefile)s")

    document_sfsr_url_template = ("http://rkrattsbaser.gov.se/cgi-bin/thw?${OOHTML}=sfsr_dok&"
                                  "${HTML}=sfst_lst&${SNHTML}=sfsr_err&${BASE}=SFSR&"
                                  "${TRIPSHOW}=format=THW&BET=%(basefile)s")

    document_sfsr_change_url_template = ("http://rkrattsbaser.gov.se/cgi-bin/thw?${OOHTML}=sfsr_dok&"
                                         "${HTML}=sfst_lst&${SNHTML}=sfsr_err&${BASE}=SFSR&"
                                         "${TRIPSHOW}=format=THW&%%C4BET=%(basefile)s")

    documentstore_class = SFSDocumentStore

    def __init__(self, **kwargs):
        super(SFS, self).__init__(**kwargs)
        self.trace = {}
        self.trace['paragraf'] = logging.getLogger('%s.paragraf' % self.alias)
        self.trace['tabell'] = logging.getLogger('%s.tabell' % self.alias)
        self.trace['numlist'] = logging.getLogger('%s.numlist' % self.alias)
        self.trace['rubrik'] = logging.getLogger('%s.rubrik' % self.alias)
        self.lagrum_parser = LegalRef(LegalRef.LAGRUM,
                                      LegalRef.EGLAGSTIFTNING)
        self.forarbete_parser = LegalRef(LegalRef.FORARBETEN)
        self.current_section = '0'
        self.current_headline_level = 0  # 0 = unknown, 1 = normal, 2 = sub

    def get_default_options(self):
        resource_path = "../../../res/etc/sfs-extra.n3"
        resource_path = os.path.normpath(
            os.path.dirname(__file__) + os.sep + resource_path)
        
        opts = super(SFS, self).get_default_options()
        opts['keepexpired'] = False
        opts['lawabbrevs'] = resource_path
        return opts

    def canonical_uri(self, basefile, konsolidering=False):
        baseuri = "https://lagen.nu/sfs"  # should(?) be hardcoded
        if konsolidering:
            if konsolidering == True:
                return "%s/%s/konsolidering" % (baseuri, basefile)
            else:
                return "%s/%s/konsolidering/%s" % (baseuri, basefile, konsolidering)
        else:
            return "%s/%s" % (baseuri, basefile)

    def download(self, basefile=None):
        if self.config.refresh or (not 'next_sfsnr' in self.config):
            ret = super(SFS,self).download(basefile)
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
        if not 'next_sfsnr' in self.config:
            self._set_last_sfsnr()
        (year, nr) = [int(
            x) for x in self.config.next_sfsnr.split(":")]
        done = False
        real_last_sfs_nr = False
        while not done:
            wanted_sfs_nr = '%s:%s' % (year, nr)
            self.log.info('Looking for %s' % wanted_sfs_nr)
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
                        self.log.warning("    Text updated to and including %s, "
                                         "not %s" %
                                         (uppdaterad_tom, wanted_sfs_nr))
                        if not real_last_sfs_nr:
                            real_last_sfs_nr = wanted_sfs_nr
                nr = nr + 1
            else:
                self.log.info('Peeking for SFS %s:%s' % (year, nr + 1))
                base_sfsnr_list = self._check_for_sfs(year, nr + 1)
                if base_sfsnr_list:
                    if not real_last_sfs_nr:
                        real_last_sfs_nr = wanted_sfs_nr
                    nr = nr + 1  # actual downloading next loop
                elif datetime.today().year > year:
                    self.log.info('    Time to change year?')
                    base_sfsnr_list = self._check_for_sfs(
                        datetime.today().year, 1)
                    if base_sfsnr_list:
                        year = datetime.today().year
                        nr = 1  # actual downloading next loop
                    else:
                        self.log.info("    We're done")
                        done = True
                else:
                    self.log.info("    We're done")
                    done = True
        if real_last_sfs_nr:
            self._set_last_sfsnr(real_last_sfs_nr)
        else:
            self._set_last_sfsnr("%s:%s" % (year, nr))

    def _check_for_sfs(self, year, nr):
        """Givet ett SFS-nummer, returnera en lista med alla
        SFS-numret för dess grundförfattningar. Normalt sett har en
        ändringsförfattning bara en grundförfattning, men för vissa
        (exv 2008:605) finns flera. Om SFS-numret inte finns alls,
        returnera en tom lista."""
        # Titta först efter grundförfattning
        self.log.debug('    Looking for base act')
        grundforf = []
        basefile = "%s:%s" % (year,nr)
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
                for m in re.finditer('>(\d+:\d+)</a>', page):
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
        sfsr_url = self.document_sfsr_url_template % {'basefile': basefile.replace(" ", "+")}
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
    def get_archive_version(self, basefile, sfst_tempfile):
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
            plaintext = self.extract_sfst([filename])
            # for some insane reason, hashlib:s update method can't seem
            # to handle ordinary unicode strings
            c.update(plaintext.encode('iso-8859-1'))
        except:
            self.log.warning("Could not extract plaintext from %s" % filename)
        return c.hexdigest()

    re_SimpleSfsId = re.compile(r'(\d{4}:\d+)\s*$')
    re_SearchSfsId = re.compile(r'\((\d{4}:\d+)\)').search
    re_ChangeNote = re.compile(r'(Lag|Förordning) \(\d{4}:\d+\)\.?$')
    re_ChapterId = re.compile(r'^(\d+( \w|)) [Kk]ap.').match
    re_DivisionId = re.compile(r'^AVD. ([IVX]*)').match
    re_SectionId = re.compile(
        r'^(\d+ ?\w?) \xa7[ \.]')  # used for both match+sub
    re_SectionIdOld = re.compile(
        r'^\xa7 (\d+ ?\w?).')     # as used in eg 1810:0926
    re_DottedNumber = re.compile(r'^(\d+ ?\w?)\. ')
    re_Bullet = re.compile(r'^(\-\-?|\x96) ')
    re_NumberRightPara = re.compile(r'^(\d+)\) ').match
    re_Bokstavslista = re.compile(r'^(\w)\) ')
    re_ElementId = re.compile(
        r'^(\d+) mom\.')        # used for both match+sub
    re_ChapterRevoked = re.compile(
        r'^(\d+( \w|)) [Kk]ap. (upphävd|har upphävts) genom (förordning|lag) \([\d\:\. s]+\)\.?$').match
    re_SectionRevoked = re.compile(
        r'^(\d+ ?\w?) \xa7[ \.]([Hh]ar upphävts|[Nn]y beteckning (\d+ ?\w?) \xa7) genom ([Ff]örordning|[Ll]ag) \([\d\:\. s]+\)\.$').match
    re_RevokeDate = re.compile(
        r'/(?:Rubriken u|U)pphör att gälla U:(\d+)-(\d+)-(\d+)/')
    re_RevokeAuthorization = re.compile(
        r'/Upphör att gälla U:(den dag regeringen bestämmer)/')
    re_EntryIntoForceDate = re.compile(
        r'/(?:Rubriken t|T)räder i kraft I:(\d+)-(\d+)-(\d+)/')
    re_EntryIntoForceAuthorization = re.compile(
        r'/Träder i kraft I:(den dag regeringen bestämmer)/')
    re_dehyphenate = re.compile(r'\b- (?!(och|eller))', re.UNICODE).sub
    re_definitions = re.compile(
        r'^I (lagen|förordningen|balken|denna lag|denna förordning|denna balk|denna paragraf|detta kapitel) (avses med|betyder|används följande)').match
    re_brottsdef = re.compile(
        r'\b(döms|dömes)(?: han)?(?:,[\w\xa7 ]+,)? för ([\w ]{3,50}) till (böter|fängelse)', re.UNICODE).search
    re_brottsdef_alt = re.compile(
        r'[Ff]ör ([\w ]{3,50}) (döms|dömas) till (böter|fängelse)', re.UNICODE).search
    re_parantesdef = re.compile(r'\(([\w ]{3,50})\)\.', re.UNICODE).search
    re_loptextdef = re.compile(
        r'^Med ([\w ]{3,50}) (?:avses|förstås) i denna (förordning|lag|balk)', re.UNICODE).search

    # use this custom matcher to ensure any strings you intend to convert
    # are legal roman numerals (simpler than having from_roman throwing
    # an exception)
    re_roman_numeral_matcher = re.compile(
        '^M?M?M?(CM|CD|D?C?C?C?)(XC|XL|L?X?X?X?)(IX|IV|V?I?I?I?)$').match

    @decorators.action
    @decorators.managedparsing
    def parse(self, doc):
        # 3 ways of getting a proper doc.uri (like
        # https://lagen.nu/sfs/2008:388/konsolidering/2013:411):

        # 1. use self._find_uppdaterad_tom(sfst_file, doc.basefile). Note
        # that this value is often wrong (particularly typos are common).

        # 2. call self.parse_sfsr(sfsr_file) and find the last
        # value. Note that SFSR might be updated before SFST and so
        # the last sfs no might be later than what's really in the SFS file.

        # 3. analyse all text looking for all end-of-section markers
        # like "Lag (2013:411).", then picking the last (sane) one.

        # Ideally, we'd like to have doc.uri available early, since
        # it's input for steps 2 and 3. Therefore we go for method 1,
        # but maybe incorporate warnings (at least later on).
        sfst_file = self.store.downloaded_path(doc.basefile)

        sfsr_file = self.store.register_path(doc.basefile)
        docentry_file = self.store.documententry_path(doc.basefile)
        # workaround to fit into the RepoTester framework
        if not os.path.exists(sfsr_file):
            sfsr_file = sfst_file.replace("/downloaded/", "/register/")
        if not os.path.exists(docentry_file):
            docentry_file = sfst_file.replace(
                "/downloaded/", "/entries/").replace(".html", ".json")

        # legacy code -- try to remove this by providing doc.basefile
        # to all methods that need it
        self.id = doc.basefile

        # Check to see if this might not be a proper SFS at all
        # (from time to time, other agencies publish their stuff
        # in SFS - this seems to be handled by giving those
        # documents a SFS nummer on the form "N1992:31". Filter
        # these out.
        if doc.basefile.startswith('N'):
            raise IckeSFS("%s is not a regular SFS" % doc.basefile)

        # Check to see if the Författning has been revoked (using
        # plain fast string searching, no fancy HTML parsing and
        # traversing)
        t = TextReader(sfst_file, encoding="iso-8859-1")
        if not self.config.keepexpired:
            try:
                t.cuepast('<i>Författningen är upphävd/skall upphävas: ')
                datestr = t.readto('</i></b>')
                if datetime.strptime(datestr, '%Y-%m-%d') < datetime.today():
                    self.log.debug('%s: Expired' % doc.basefile)
                    raise UpphavdForfattning("%s is an expired SFS" % doc.basefile)
            except IOError:
                pass

        # Find out last uppdaterad_tom value
        t.seek(0)
        uppdaterad_tom = self._find_uppdaterad_tom(doc.basefile, reader=t)
        # now we can set doc.uri for reals
        doc.uri = self.canonical_uri(doc.basefile, uppdaterad_tom)
        doc.lang = "sv"
        desc = Describer(doc.meta, doc.uri)

        try:
            registry = self.parse_sfsr(sfsr_file, doc.uri)
        except UpphavdForfattning as e:
            e.dummyfile = self.store.parsed_path(doc.basefile)
            raise e

        # for uri, graph in registry.items():
        #    print("==== %s ====" % uri)
        #    print(graph.serialize(format="turtle").decode("utf-8"))

        try:
            plaintext = self.extract_sfst(sfst_file)
            plaintextfile = self.store.path(doc.basefile, "intermediate", ".txt")
            util.writefile(plaintextfile, plaintext, encoding="iso-8859-1")
            (plaintext, patchdesc) = self.patch_if_needed(doc.basefile, plaintext)
            if patchdesc:
                desc.value(self.ns['rinfoex'].patchdescription,
                           patchdesc)

            self.parse_sfst(plaintext, doc)
        except IOError:
            self.log.warning("%s: Fulltext saknas" % self.id)
            # extractSFST misslyckades, då det fanns någon post i
            # SFST-databasen (det händer alltför ofta att bara
            # SFSR-databasen är uppdaterad).
            desc.value(self.ns['dct'].title,
                       registry.value(URIRef(doc.uri),
                                      self.ns['dct'].title))
            desc.rel(self.ns['dct'].publisher,
                     self.lookup_resource("Regeringskansliet"))

            desc.value(self.ns['dct'].identifier, "SFS " + doc.basefile)

            doc.body = Forfattning([Stycke(['Lagtext saknas'],
                                           id='S1')])

        # At this point, we basic metadata and a almost complete body
        # structure. Enhance the metadata:
        for uri in registry.keys():
            desc.rel(self.ns['rpubl'].konsolideringsunderlag, uri)
        desc.rdftype(self.ns['rpubl'].KonsolideradGrundforfattning)
        # FIXME: make this part of head metadata
        desc.rev(self.ns['owl'].sameAs, self.canonical_uri(doc.basefile, True))
        desc.rel(self.ns['rpubl'].konsoliderar, self.canonical_uri(doc.basefile))
        de = DocumentEntry(docentry_file)
        desc.value(self.ns['rinfoex'].senastHamtad, de.orig_updated)
        desc.value(self.ns['rinfoex'].senastKontrollerad, de.orig_checked)
        # find any established abbreviation -- FIXME: simplifize, most
        # code should be in SwedishLegalSource (c.f. lookup_resource)
        g = Graph()
        g.load(self.config.lawabbrevs, format="n3")
        grf_uri = self.canonical_uri(doc.basefile)
        v = g.value(URIRef(grf_uri), self.ns['dct'].alternate, any=True)
        if v:
            desc.value(self.ns['dct'].alternate, v)

        # Finally: the dct:published property for this
        # rpubl:KonsolideradGrundforfattning isn't readily
        # available. The true value is only found by parsing PDF files
        # in another docrepo. There are three general ways of finding
        # it out.
        published = None
        # 1. if registry contains a single value (ie a
        # Grundforfattning that hasn't been amended yet), we can
        # assume that dct:published == rpubl:utfardandedatum
        if len(registry) == 1:
            published = desc.getvalue(self.ns['rpubl'].utfardandedatum)
        else:
            # 2. if the last post in registry contains a
            # rpubl:utfardandedatum, assume that this version of the
            # rpubl:KonsolideradGrundforfattning has the same dct:published date
            last_post_uri = list(registry.keys())[-1]
            last_post_graph = registry[last_post_uri]
            pub_lit = last_post_graph.value(URIRef(last_post_uri),
                                            self.ns['rpubl'].utfardandedatum)
            if pub_lit:
                published = pub_lit.toPython()
        if not published:
            # 3. general fallback: Use the corresponding orig_updated
            # on the DocumentEntry. This is not correct (as it
            # represents the date we fetched the document, not the
            # date the document was made available), but it's as close
            # as we can get.
            published = de.orig_updated.date()
        assert isinstance(published, date)
        desc.value(self.ns['dct'].published, published)

        rinfo_sameas = "http://rinfo.lagrummet.se/publ/sfs/%s/konsolidering/%s" % (
            doc.basefile, published.strftime("%Y-%m-%d"))
        desc.rel(self.ns['owl'].sameAs, rinfo_sameas)

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

        for uri, graph in registry.items():
            identifier = graph.value(URIRef(uri), self.ns['dct'].identifier)
            identifier = identifier.replace("SFS ", "L")
            rp = Registerpost(uri=uri, meta=graph, id=identifier)
            reg.append(rp)
            if uri in obs:
                rp.append(obs[uri])

        doc.body.append(reg)

    def _forfattningstyp(self, forfattningsrubrik):
        if (forfattningsrubrik.startswith('Lag ') or
            (forfattningsrubrik.endswith('lag') and not forfattningsrubrik.startswith('Förordning')) or
                forfattningsrubrik.endswith('balk')):
            return self.ns['rpubl'].Lag
        else:
            return self.ns['rpubl'].Forordning

    def _dict_to_graph(self, d, graph, uri):
        mapping = {'SFS nr': self.ns['rpubl'].fsNummer,
                   'Rubrik': self.ns['dct'].title,
                   'Senast hämtad': self.ns['rinfoex'].senastHamtad,
                   'Utfärdad': self.ns['rpubl'].utfardandedatum,
                   'Utgivare': self.ns['dct'].publisher,
                   'Departement/ myndighet': self.ns['dct'].creator
                   }
        desc = Describer(graph, uri)
        for (k, v) in d.items():
            if k in mapping:
                if hasattr(v, 'uri'):
                    desc.rel(mapping[k], v.uri)
                else:
                    desc.value(mapping[k], v)

    def parse_sfsr(self, filename, docuri):
        """Parsear ut det SFSR-registret som innehåller alla ändringar
        i lagtexten från HTML-filer"""
        with codecs.open(filename, encoding="iso-8859-1") as fp:
            soup = bs4.BeautifulSoup(fp.read(), "lxml")

        d = OrderedDict()
        rubrik = soup.body('table')[2].text.strip()
        changes = soup.body('table')[3:-2]
        for table in changes:
            sfsnr = table.find(text="SFS-nummer:").find_parent(
                "td").find_next_sibling("td").text.strip()
            # FIXME: canonical uri for this docrepo is consolidated
            # documents. we need the uri for the base document. Either
            # create a helper docrepo (ferenda.legal.se.SFSPrint) or
            # implement a helper method.
            docuri = self.canonical_uri(sfsnr)
            g = self.make_graph()  # to get proper namespace bindings
            d[docuri] = g
            desc = Describer(g, docuri)

            rowdict = {}
            for row in table('tr'):
                key = row.td.text.strip()
                if key.endswith(":"):
                    key = key[:-1]  # trim ending ":"
                elif key == '':
                    continue
                val = row('td')[1].text.replace('\xa0', ' ').strip()
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
                    desc.value(self.ns['dct'].identifier, "SFS " + val)
                    desc.value(self.ns['rpubl'].arsutgava, arsutgava)
                    desc.value(self.ns['rpubl'].lopnummer, lopnummer)
                    # desc.value("rpubl:lopnummer", lopnummer)

                elif key == 'Ansvarig myndighet':
                    try:
                        authrec = self.lookup_resource(val)
                        desc.rel(self.ns['rpubl'].departement, authrec)
                    except Exception:
                        desc.value(self.ns['rpubl'].departement, val)
                elif key == 'Rubrik':
                    if not self.id in val:
                        self.log.warning(
                            "%s: Base SFS %s not found in title %r" % (self.id, self.id, val))
                    desc.value(self.ns['dct'].title, Literal(val, lang="sv"))
                    desc.rdftype(self._forfattningstyp(val))
                elif key == 'Observera':
                    if not self.config.keepexpired:
                        if 'Författningen är upphävd/skall upphävas: ' in val:
                            if datetime.strptime(val[41:51], '%Y-%m-%d') < datetime.today():
                                raise UpphavdForfattning("%s is an expired SFS" % self.id)
                    desc.value(self.ns['rdfs'].comment, val)
                elif key == 'Ikraft':
                    desc.value(self.ns['rpubl'].ikrafttradandedatum,
                               datetime.strptime(val[:10], '%Y-%m-%d').date())
                elif key == 'Omfattning':
                    # First, create rdf statements for every
                    # single modified section we can find
                    for changecat in val.split('; '):
                        if (changecat.startswith('ändr.') or
                            changecat.startswith('ändr ') or
                                changecat.startswith('ändring ')):
                            pred = self.ns['rpubl'].ersatter
                        elif (changecat.startswith('upph.') or
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
                              changecat in ('begr. giltighet', 'Omtryck',
                                            'omtryck', 'forts.giltighet',
                                            'forts. giltighet',
                                            'forts. giltighet av vissa best.')):
                            pred = None
                        else:
                            self.log.warning("%s: Okänd omfattningstyp %r" % (self.id, changecat))
                            pred = None
                            for node in self.lagrum_parser.parse(changecat, docuri, pred):
                                if hasattr(node, 'predicate'):
                                    desc.rel(node.predicate, node.uri)
                    # Secondly, preserve the entire text
                    desc.value(self.ns['rpubl'].andrar, val)
                elif key == 'Förarbeten':
                    for node in self.forarbete_parser.parse(val):
                        if hasattr(node, 'uri'):
                            with desc.rel(self.ns['rpubl'].forarbete,
                                          node.uri):
                                desc.value(self.ns['dct'].identifier,
                                           str(node))
                elif key == 'CELEX-nr':
                    for celex in re.findall('3\d{2,4}[LR]\d{4}', val):
                        celexuri = "http://rinfo.lagrummet.se/ext/eur-lex/%s" % celex
                        with desc.rel(self.ns['rpubl'].genomforDirektiv,
                                      celexuri):
                            desc.value(self.ns['rpubl'].celexNummer, celex)
                elif key == 'Tidsbegränsad':
                    expdate = datetime.strptime(val[:10], '%Y-%m-%d')
                    desc.value(self.ns['rinfoex'].tidsbegransad, expdate)
                    if expdate < datetime.today():
                        if not self.config.keepexpired:
                            raise UpphavdForfattning(
                                "%s is an expired (time-limited) SFS" % filename)
                else:
                    self.log.warning(
                        '%s: Obekant nyckel [\'%s\']' % self.id, key)

            # finally, add some properties not directly found in the
            # registry, but which are always present for SFSes, or deducible
            desc.rel(self.ns['dct'].publisher,
                     self.lookup_resource("Regeringskansliet"))
            desc.rel(self.ns['rpubl'].beslutadAv,
                     self.lookup_resource("Regeringskansliet"))
            desc.rel(self.ns['rpubl'].forfattningssamling,
                     "http://rinfo.lagrummet.se/serie/fs/sfs")
            desc.rel(self.ns['owl'].sameAs,
                     "http://rinfo.lagrummet.se/publ/sfs/" + sfsnr)
            utfardandedatum = self._find_utfardandedatum(sfsnr)
            if utfardandedatum:
                desc.value(self.ns['rpubl'].utfardandedatum, utfardandedatum)

        return d

    def _find_utfardandedatum(self, sfsnr):
        # FIXME: Code to instantiate a SFSTryck object and muck about goes here
        fake = {'2013:363': date(2013, 5, 23),
                '2008:344': date(2008, 5, 22),
                '2009:1550': date(2009, 12, 17),
                '2013:411': date(2013, 5, 30),
                }
        return fake.get(sfsnr, None)

    def extract_sfst(self, filename):
        """Plockar fram plaintextversionen av den konsoliderade
        lagtexten från nedladdade HTML-filer"""
        t = TextReader(filename, encoding="iso-8859-1")
        t.cuepast('<pre>')
        # remove &auml; et al
        hp = html_parser.HTMLParser()
        txt = hp.unescape(t.readto('</pre>'))
        if not '\r\n' in txt:
            txt = txt.replace('\n', '\r\n')
        re_tags = re.compile("</?\w{1,3}>")
        txt = re_tags.sub('', txt)
        return txt

    def _term_to_subject(self, term):
        capitalized = term[0].upper() + term[1:]
        return 'http://lagen.nu/concept/%s' % capitalized.replace(' ', '_')

    # Post-processar dokumentträdet rekursivt och gör tre saker:
    #
    # Hittar begreppsdefinitioner i löptexten
    #
    # Hittar adresserbara enheter (delresurser som ska ha unika URI:s,
    # dvs kapitel, paragrafer, stycken, punkter) och konstruerar id's
    # för dem, exv K1P2S3N4 för 1 kap. 2 \xa7 3 st. 4 p
    #
    # Hittar lagrumshänvisningar i löptexten
    def _construct_ids(self, element, prefix, baseuri, skip_fragments=[], find_definitions=False):
        find_definitions_recursive = find_definitions
        counters = defaultdict(int)
        if isinstance(element, CompoundElement):
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

                find_definitions_recursive = find_definitions

            # Hitta lagrumshänvisningar + definitioner
            if (isinstance(element, Stycke)
                or isinstance(element, Listelement)
                    or isinstance(element, Tabellcell)):
                nodes = []
                term = None

                # self.log.debug("handling text %s, find_definitions %s" % (element[0],find_definitions))
                if find_definitions:
                    elementtext = element[0]
                    termdelimiter = ":"

                    if isinstance(element, Tabellcell):
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

                                if termdelimiter == ":" and m and m.start() < elementtext.index(":"):
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
                        # this results in empty/hidden links -- might
                        # be better to hchange sfs.template.xht2 to
                        # change these to <span rel="" href=""/>
                        # instead. Or use another object than LinkSubject.
                        term = util.normalize_space(term)
                        termnode = LinkSubject(term, uri=self._term_to_subject(
                            term), predicate="dct:subject")
                        find_definitions_recursive = False
                    else:
                        term = None

                for p in element:  # normally only one, but can be more
                                  # if the Stycke has a NumreradLista
                                  # or similar

                    if isinstance(p, str):  # look for stuff
                        # normalize and convert some characters
                        s = " ".join(p.split())
                        s = s.replace("\x96", "-")
                        # Make all links have a dct:references
                        # predicate -- not that meaningful for the
                        # XHTML2 code, but needed to get useful RDF
                        # triples in the RDFa output
                        # print "Parsing %s" % " ".join(p.split())
                        # print "Calling parse w %s" % baseuri+"#"+prefix
                        parsednodes = self.lagrum_parser.parse(s,
                                                               baseuri +
                                                               prefix,
                                                               "dct:references")
                        for n in parsednodes:
                            # py2 compat FIxme
                            if term and isinstance(n, str) and term in n:
                                (head, tail) = n.split(term, 1)
                                nodes.extend((head, termnode, tail))
                            else:
                                nodes.append(n)

                        idx = element.index(p)
                element[idx:idx + 1] = nodes

            # Konstruera IDs
            for p in element:
                counters[type(p)] += 1

                if hasattr(p, 'fragment_label'):
                    elementtype = p.fragment_label
                    if hasattr(p, 'ordinal') and p.ordinal:
                        elementordinal = p.ordinal.replace(" ", "")
                    elif hasattr(p, 'sfsnr'):
                        elementordinal = p.sfsnr
                    else:
                        elementordinal = counters[type(p)]
                    fragment = "%s%s%s" % (prefix, elementtype, elementordinal)
                    p.id = fragment
                    p.uri = baseuri + "#" + fragment
                else:
                    fragment = prefix

                if ((hasattr(p, 'fragment_label') and
                     p.fragment_label in skip_fragments)):
                    self._construct_ids(p, prefix, baseuri, skip_fragments,
                                        find_definitions_recursive)
                else:
                    self._construct_ids(p, fragment, baseuri, skip_fragments,
                                        find_definitions_recursive)

                # Efter att första tabellcellen i en rad hanterats,
                # undvik att leta definitioner i tabellceller 2,3,4...
                if isinstance(element, Tabellrad):
                    # print "släcker definitionsletarflaggan"
                    find_definitions_recursive = False

    def _count_elements(self, element):
        counters = defaultdict(int)
        if isinstance(element, CompoundElement):
            for p in element:
                if hasattr(p, 'fragment_label'):
                    counters[p.fragment_label] += 1
                    if hasattr(p, 'ordinal'):
                        counters[p.fragment_label + p.ordinal] += 1
                    subcounters = self._count_elements(p)
                    for k in subcounters:
                        counters[k] += subcounters[k]
        return counters

    def parse_sfst(self, text, doc):
        # self.reader = TextReader(string=lawtext,linesep=TextReader.UNIX)
        self.reader = TextReader(string=text, linesep=TextReader.DOS)
        self.reader.autostrip = True
        desc = Describer(doc.meta, doc.uri)
        self.make_header(desc)
        doc.body = self.makeForfattning()

        elements = self._count_elements(doc.body)
        if 'K' in elements and elements['P1'] < 2:
            skipfragments = ['A', 'K']
        else:
            skipfragments = ['A']

        self._construct_ids(doc.body, '',
                            self.canonical_uri(doc.basefile),
                            skipfragments)

    #----------------------------------------------------------------
    #
    # SFST-PARSNING
    def make_header(self, desc):
        subreader = self.reader.getreader(
            self.reader.readchunk, self.reader.linesep * 4)
        re_sfs = self.re_SimpleSfsId.search
        for line in subreader.getiterator(subreader.readparagraph):
            if ":" in line:
                (key, val) = [util.normalize_space(x)
                              for x in line.split(":", 1)]
            # Simple string literals
            if key == 'Rubrik':
                desc.value(self.ns['dct'].title, Literal(val, lang="sv"))
            elif key == 'Övrigt':
                desc.value(self.ns['rdfs'].comment, Literal(val, lang="sv"))
            elif key == 'SFS nr':
                identifier = "SFS " + val
                # delay actual writing to graph, since we may need to
                # amend this

            # date literals
            elif key == 'Utfärdad':
                desc.value(self.ns['rpubl'].utfardandedatum,
                           datetime.strptime(val[:10], '%Y-%m-%d').date())
            elif key == 'Tidsbegränsad':
                desc.value(self.ns['rinfoex'].tidsbegransad,
                           datetime.strptime(val[:10], '%Y-%m-%d').date())
            elif key == 'Upphävd':
                d = datetime.strptime(val[:10], '%Y-%m-%d')
                desc.value(self.ns['rpubl'].upphavandedatum, d)
                if not self.config.keepexpired and d < datetime.today():
                    raise UpphavdForfattning("%s is an expired SFS" % self.id)

            # urirefs
            elif key == 'Departement/ myndighet':
                authrec = self.lookup_resource(val)
                desc.rel(self.ns['dct'].creator, authrec)
            elif (key == 'Ändring införd' and re_sfs(val)):
                uppdaterad = re_sfs(val).group(1)
                # not sure we need to add this, since parse_sfsr catches same
                desc.rel(self.ns['rpubl'].konsolideringsunderlag,
                         self.canonical_uri(uppdaterad))
                if identifier and identifier != "SFS " + uppdaterad:
                    identifier += " i lydelse enligt SFS " + uppdaterad

            elif (key == 'Omtryck' and re_sfs(val)):
                desc.rel(self.ns['rinfoex'].omtryck,
                         self.canonical_uri(re_sfs(val).group(1)))
            elif (key == 'Författningen har upphävts genom' and
                  re_sfs(val)):
                desc.rel(self.ns['rinfoex'].upphavdAv,
                         self.canonical_uri(re_sfs(val).group(1)))

            else:
                self.log.warning(
                    '%s: Obekant nyckel [\'%s\']' % (self.id, key))

        desc.value(self.ns['dct'].identifier, identifier)
        desc.rel(self.ns['dct'].publisher,
                 self.lookup_resource("Regeringskansliet"))

        if not desc.getvalue(self.ns['dct'].title):
            self.log.warning("%s: Rubrik saknas" % self.id)

    def makeForfattning(self):
        while self.reader.peekline() == "":
            self.reader.readline()

        self.log.debug('Första raden \'%s\'' % self.reader.peekline())
        (line, upphor, ikrafttrader) = self.andringsDatum(
            self.reader.peekline())
        if ikrafttrader:
            self.log.debug(
                'Författning med ikraftträdandedatum %s' % ikrafttrader)

            b = Forfattning(ikrafttrader=ikrafttrader,
                            uri=self.canonical_uri(self.id))
            self.reader.readline()
        else:
            self.log.debug('Författning utan ikraftträdandedatum')
            b = Forfattning(uri=self.canonical_uri(self.id))

        while not self.reader.eof():
            state_handler = self.guess_state()
            # special case - if a Overgangsbestammelse is encountered
            # without the preceeding headline (which would normally
            # set state_handler to makeOvergangsbestammelser (notice
            # the plural)
            if state_handler == self.makeOvergangsbestammelse:
                res = self.makeOvergangsbestammelser(rubrik_saknas=True)
            else:
                res = state_handler()
            if res is not None:
                b.append(res)
        return b

    def makeAvdelning(self):
        avdelningsnummer = self.idOfAvdelning()
        p = Avdelning(rubrik=self.reader.readline(),
                      ordinal=avdelningsnummer,
                      underrubrik=None)
        if (self.reader.peekline(1) == "" and
            self.reader.peekline(3) == "" and
                not self.isKapitel(self.reader.peekline(2))):
            self.reader.readline()
            p.underrubrik = self.reader.readline()

        self.log.debug("  Ny avdelning: '%s...'" % p.rubrik[:30])

        while not self.reader.eof():
            state_handler = self.guess_state()

            if state_handler in (self.makeAvdelning,  # Strukturer som signalerar att denna avdelning är slut
                                 self.makeOvergangsbestammelser,
                                 self.makeBilaga):
                self.log.debug("  Avdelning %s färdig" % p.ordinal)
                return p
            else:
                res = state_handler()
                if res is not None:
                    p.append(res)
        # if eof is reached
        return p

    def makeUpphavtKapitel(self):
        kapitelnummer = self.idOfKapitel()
        c = UpphavtKapitel(self.reader.readline(),
                           ordinal=kapitelnummer)
        self.log.debug("  Upphävt kapitel: '%s...'" % c[:30])

        return c

    def makeKapitel(self):
        kapitelnummer = self.idOfKapitel()

        para = self.reader.readparagraph()
        (line, upphor, ikrafttrader) = self.andringsDatum(para)

        kwargs = {'rubrik': util.normalize_space(line),
                  'ordinal': kapitelnummer}
        if upphor:
            kwargs['upphor'] = upphor
        if ikrafttrader:
            kwargs['ikrafttrader'] = ikrafttrader
        k = Kapitel(**kwargs)
        self.current_headline_level = 0
        self.current_section = '0'

        self.log.debug("    Nytt kapitel: '%s...'" % line[:30])

        while not self.reader.eof():
            state_handler = self.guess_state()

            if state_handler in (self.makeKapitel,  # Strukturer som signalerar slutet på detta kapitel
                                 self.makeUpphavtKapitel,
                                 self.makeAvdelning,
                                 self.makeOvergangsbestammelser,
                                 self.makeBilaga):
                self.log.debug("    Kapitel %s färdigt" % k.ordinal)
                return (k)
            else:
                res = state_handler()
                if res is not None:
                    k.append(res)
        # if eof is reached
        return k

    def makeRubrik(self):
        para = self.reader.readparagraph()
        (line, upphor, ikrafttrader) = self.andringsDatum(para)
        self.log.debug("      Ny rubrik: '%s...'" % para[:30])

        kwargs = {}
        if upphor:
            kwargs['upphor'] = upphor
        if ikrafttrader:
            kwargs['ikrafttrader'] = ikrafttrader
        if self.current_headline_level == 2:
            kwargs['type'] = 'underrubrik'
        elif self.current_headline_level == 1:
            self.current_headline_level = 2

        h = Rubrik(line, **kwargs)
        return h

    def makeUpphavdParagraf(self):
        paragrafnummer = self.idOfParagraf(self.reader.peekline())
        p = UpphavdParagraf(self.reader.readline(),
                            ordinal=paragrafnummer)
        self.current_section = paragrafnummer
        self.log.debug("      Upphävd paragraf: '%s...'" % p[:30])
        return p

    def makeParagraf(self):
        paragrafnummer = self.idOfParagraf(self.reader.peekline())
        self.current_section = paragrafnummer
        firstline = self.reader.peekline()
        self.log.debug("      Ny paragraf: '%s...'" % firstline[:30])
        # Läs förbi paragrafnumret:
        self.reader.read(len(paragrafnummer) + len(' \xa7 '))

        # some really old laws have sections split up in "elements"
        # (moment), eg '1 \xa7 1 mom.', '1 \xa7 2 mom.' etc
        match = self.re_ElementId.match(firstline)
        if self.re_ElementId.match(firstline):
            momentnummer = match.group(1)
            self.reader.read(len(momentnummer) + len(' mom. '))
        else:
            momentnummer = None

        (fixedline, upphor, ikrafttrader) = self.andringsDatum(firstline)
        # Läs förbi '/Upphör [...]/' och '/Ikraftträder [...]/'-strängarna
        self.reader.read(len(firstline) - len(fixedline))
        kwargs = {'ordinal': paragrafnummer}
        if upphor:
            kwargs['upphor'] = upphor
        if ikrafttrader:
            kwargs['ikrafttrader'] = ikrafttrader

        if momentnummer:
            kwargs['moment'] = momentnummer

        p = Paragraf(**kwargs)

        state_handler = self.makeStycke
        res = self.makeStycke()
        p.append(res)

        while not self.reader.eof():
            state_handler = self.guess_state()
            if state_handler in (self.makeParagraf,
                                 self.makeUpphavdParagraf,
                                 self.makeKapitel,
                                 self.makeUpphavtKapitel,
                                 self.makeAvdelning,
                                 self.makeRubrik,
                                 self.makeOvergangsbestammelser,
                                 self.makeBilaga):
                self.log.debug("      Paragraf %s färdig" % paragrafnummer)
                return p
            elif state_handler == self.blankline:
                state_handler()  # Bara att slänga bort
            elif state_handler == self.makeOvergangsbestammelse:
                self.log.debug("      Paragraf %s färdig" % paragrafnummer)
                self.log.warning(
                    "%s: Avskiljande rubrik saknas mellan författningstext och övergångsbestämmelser" % self.id)
                return p
            else:
                assert state_handler == self.makeStycke, "guess_state returned %s, not makeStycke" % state_handler.__name__
                # if state_handler != self.makeStycke:
                #    self.log.warning("behandlar '%s...' som stycke, inte med %s" % (self.reader.peekline()[:30], state_handler.__name__))
                res = self.makeStycke()
                p.append(res)

        # eof occurred
        return p

    def makeStycke(self):
        self.log.debug(
            "        Nytt stycke: '%s...'" % self.reader.peekline()[:30])
        s = Stycke([util.normalize_space(self.reader.readparagraph())])
        while not self.reader.eof():
            #self.log.debug("            makeStycke: calling guess_state ")
            state_handler = self.guess_state()
            #self.log.debug("            makeStycke: guess_state returned %s " % state_handler.__name__)
            if state_handler in (self.makeNumreradLista,
                                 self.makeBokstavslista,
                                 self.makeStrecksatslista,
                                 self.makeTabell):
                res = state_handler()
                s.append(res)
            elif state_handler == self.blankline:
                state_handler()  # Bara att slänga bort
            else:
                #self.log.debug("            makeStycke: ...we're done")
                return s
        return s

    def makeNumreradLista(self):
        n = NumreradLista()
        while not self.reader.eof():
            # Utgå i första hand från att nästa stycke är ytterligare
            # en listpunkt (vissa tänkbara stycken kan även matcha
            # tabell m.fl.)
            if self.isNumreradLista():
                state_handler = self.makeNumreradLista
            else:
                state_handler = self.guess_state()

            if state_handler not in (self.blankline,
                                     self.makeNumreradLista,
                                     self.makeBokstavslista,
                                     self.makeStrecksatslista):
                return n
            elif state_handler == self.blankline:
                state_handler()
            else:
                if state_handler == self.makeNumreradLista:
                    self.log.debug("          Ny punkt: '%s...'" %
                                   self.reader.peekline()[:30])
                    listelement_ordinal = self.idOfNumreradLista()
                    li = Listelement(ordinal=listelement_ordinal)
                    p = self.reader.readparagraph()
                    li.append(p)
                    n.append(li)
                else:
                    # this must be a sublist
                    res = state_handler()
                    n[-1].append(res)
                self.log.debug(
                    "          Punkt %s avslutad" % listelement_ordinal)
        return n

    def makeBokstavslista(self):
        n = Bokstavslista()
        while not self.reader.eof():
            state_handler = self.guess_state()
            if state_handler not in (self.blankline, self.makeBokstavslista):
                return n
            elif state_handler == self.blankline:
                state_handler()
            else:
                self.log.debug("            Ny underpunkt: '%s...'" %
                               self.reader.peekline()[:30])
                listelement_ordinal = self.idOfBokstavslista()
                li = Listelement(ordinal=listelement_ordinal)
                p = self.reader.readparagraph()
                li.append(p)
                n.append(li)
                self.log.debug("            Underpunkt %s avslutad" %
                               listelement_ordinal)
        return n

    def makeStrecksatslista(self):
        n = Strecksatslista()
        cnt = 0
        while not self.reader.eof():
            state_handler = self.guess_state()
            if state_handler not in (self.blankline, self.makeStrecksatslista):
                return n
            elif state_handler == self.blankline:
                state_handler()
            else:
                self.log.debug("            Ny strecksats: '%s...'" %
                               self.reader.peekline()[:60])
                cnt += 1
                p = self.reader.readparagraph()
                li = Listelement(ordinal=str(cnt))
                li.append(p)
                n.append(li)
                self.log.debug("            Strecksats #%s avslutad" % cnt)
        return n

    def blankline(self):
        self.reader.readline()
        return None

    def eof(self):
        return None

    def makeOvergangsbestammelser(self, rubrik_saknas=False):  # svenska: övergångsbestämmelser
        # det kan diskuteras om dessa ska ses som en del av den
        # konsoliderade lagtexten öht, men det verkar vara kutym att
        # ha med åtminstone de som kan ha relevans för gällande rätt
        self.log.debug("    Ny Övergångsbestämmelser")

        if rubrik_saknas:
            rubrik = "[Övergångsbestämmelser]"
        else:
            rubrik = self.reader.readparagraph()
        obs = Overgangsbestammelser(rubrik=rubrik)

        while not self.reader.eof():
            state_handler = self.guess_state()
            if state_handler == self.makeBilaga:
                return obs

            res = state_handler()
            if res is not None:
                if state_handler != self.makeOvergangsbestammelse:
                    # assume these are the initial Övergångsbestämmelser
                    if hasattr(self, 'id') and '/' in self.id:
                        sfsnr = self.id
                        self.log.warning(
                            "%s: Övergångsbestämmelsen saknar SFS-nummer - antar [%s]" % (self.id, sfsnr))
                    else:
                        sfsnr = '0000:000'
                        self.log.warning(
                            "(unknown): Övergångsbestämmelsen saknar ett SFS-nummer - antar %s" % (sfsnr))

                    obs.append(Overgangsbestammelse([res], sfsnr=sfsnr))
                else:
                    obs.append(res)

        return obs

    def makeOvergangsbestammelse(self):
        p = self.reader.readline()
        self.log.debug("      Ny Övergångsbestämmelse: %s" % p)
        ob = Overgangsbestammelse(sfsnr=p)
        while not self.reader.eof():
            state_handler = self.guess_state()
            if state_handler in (self.makeOvergangsbestammelse,
                                 self.makeBilaga):
                return ob
            res = state_handler()
            if res is not None:
                ob.append(res)

        return ob

    def makeBilaga(self):  # svenska: bilaga
        rubrik = self.reader.readparagraph()
        (rubrik, upphor, ikrafttrader) = self.andringsDatum(rubrik)

        kwargs = {'rubrik': rubrik}
        if upphor:
            kwargs['upphor'] = upphor
        if ikrafttrader:
            kwargs['ikrafttrader'] = ikrafttrader
        b = Bilaga(**kwargs)
        self.log.debug("    Ny bilaga: %s" % rubrik)
        while not self.reader.eof():
            state_handler = self.guess_state()
            if state_handler in (self.makeBilaga,
                                 self.makeOvergangsbestammelser):
                return b
            res = state_handler()
            if res is not None:
                b.append(res)
        return b

    def andringsDatum(self, line, match=False):
        # Hittar ändringsdatumdirektiv i line. Om match, matcha från strängens
        # början, annars sök i hela strängen.
        dates = {'ikrafttrader': None,
                 'upphor': None}

        for (regex, key) in list({self.re_RevokeDate: 'upphor',
                                  self.re_RevokeAuthorization: 'upphor',
                                  self.re_EntryIntoForceDate: 'ikrafttrader',
                                  self.re_EntryIntoForceAuthorization: 'ikrafttrader'}.items()):
            if match:
                m = regex.match(line)
            else:
                m = regex.search(line)
            if m:
                if len(m.groups()) == 3:
                    dates[key] = datetime(int(m.group(1)),
                                          int(m.group(2)),
                                          int(m.group(3)))
                else:
                    dates[key] = m.group(1)
                line = regex.sub('', line)

        return (line.strip(), dates['upphor'], dates['ikrafttrader'])

    def guess_state(self):
        # sys.stdout.write("        Guessing for '%s...'" % self.reader.peekline()[:30])
        try:
            if self.reader.peekline() == "":
                handler = self.blankline
            elif self.isAvdelning():
                handler = self.makeAvdelning
            elif self.isUpphavtKapitel():
                handler = self.makeUpphavtKapitel
            elif self.isUpphavdParagraf():
                handler = self.makeUpphavdParagraf
            elif self.isKapitel():
                handler = self.makeKapitel
            elif self.isParagraf():
                handler = self.makeParagraf
            elif self.isTabell():
                handler = self.makeTabell
            elif self.isOvergangsbestammelser():
                handler = self.makeOvergangsbestammelser
            elif self.isOvergangsbestammelse():
                handler = self.makeOvergangsbestammelse
            elif self.isBilaga():
                handler = self.makeBilaga
            elif self.isNumreradLista():
                handler = self.makeNumreradLista
            elif self.isStrecksatslista():
                handler = self.makeStrecksatslista
            elif self.isBokstavslista():
                handler = self.makeBokstavslista
            elif self.isRubrik():
                handler = self.makeRubrik
            else:
                handler = self.makeStycke
        except IOError:
            handler = self.eof
        # sys.stdout.write("%r\n" % handler)
        return handler

    def isAvdelning(self):
        # The start of a part ("avdelning") should be a single line
        if '\n' in self.reader.peekparagraph() != "":
            return False

        return self.idOfAvdelning() is not None

    def idOfAvdelning(self):
        # There are four main styles of parts ("Avdelning") in swedish law
        #
        # 1998:808: "FÖRSTA AVDELNINGEN\n\nÖVERGRIPANDE BESTÄMMELSER"
        #  (also in 1932:130, 1942:740, 1956:623, 1957:297, 1962:381, 1962:700,
        #   1970:988, 1970:994, 1971:235 (revoked), 1973:370 (revoked),
        #   1977:263 (revoked), 1987:230, 1992:300 (revoked), 1994:200,
        #   1998:674, 2000:192, 2005:104 and 2007:528 -- not always in all
        #   uppercase. However, the initial line "FÖRSTA AVDELNININGEN"
        #   (in any casing) is always followed by another line that
        #   describes/labels the part.)
        #
        # 1979:1152: "Avd. 1. Bestämmelser om taxering av fastighet"
        #  (also in 1979:1193 (revoked))
        #
        # 1994:1009: "Avdelning I Fartyg"
        #
        # 1999:1229: "AVD. I INNEH\XE5LL OCH DEFINITIONER"
        #
        # 2009:400: "AVDELNING I. INLEDANDE BESTÄMMELSER"
        #
        # and also "1 avd." (in 1959:287 (revoked), 1959:420 (revoked)
        #
        #  The below code checks for all these patterns in turn
        #
        # The variant "Avdelning 1" has also been found, but only in
        # appendixes
        p = self.reader.peekline()
        if p.lower().endswith("avdelningen") and len(p.split()) == 2:
            ordinal = p.split()[0]
            return str(self._swedish_ordinal(ordinal))
        elif p.startswith("AVD. ") or p.startswith("AVDELNING "):
            roman = re.split(r'\s+', p)[1]
            if roman.endswith("."):
                roman = roman[:-1]
            if self.re_roman_numeral_matcher(roman):
                return str(util.from_roman(roman))
        elif p.startswith("Avdelning "):
            roman = re.split(r'\s+', p)[1]
            if self.re_roman_numeral_matcher(roman):
                return str(util.from_roman(roman))
        elif p[2:6] == "avd.":
            if p[0].isdigit():
                return p[0]
        elif p.startswith("Avd. "):
            idstr = re.split(r'\s+', p)[1]
            if idstr.isdigit():
                return idstr
        return None

    def isUpphavtKapitel(self):
        match = self.re_ChapterRevoked(self.reader.peekline())
        return match is not None

    def isKapitel(self, p=None):
        return self.idOfKapitel(p) is not None

    def idOfKapitel(self, p=None):
        if not p:
            p = self.reader.peekparagraph().replace("\n", " ")

        # '1 a kap.' -- almost always a headline, regardless if it
        # streches several lines but there are always special cases
        # (1982:713 1 a kap. 7 \xa7)
        #m = re.match(r'^(\d+( \w|)) [Kk]ap.',p)
        m = self.re_ChapterId(p)
        if m:
            # even though something might look like the start of a chapter, it's often just the
            # start of a paragraph in a section that lists the names of chapters. These following
            # attempts to filter these out by looking for some typical line endings for those cases
            if (p.endswith(",") or
                p.endswith(";") or
                # p.endswith(")") or  # but in some cases, a chapter actually ends in ),
                # eg 1932:131
                p.endswith(" och") or  # in unlucky cases, a chapter heading might span two lines in a way that the first line ends with "och" (eg 1998:808 kap. 3)
                p.endswith(" om") or
                p.endswith(" samt") or
                (p.endswith(".") and not
                 (m.span()[1] == len(p) or  # if the ENTIRE p is eg "6 kap." (like it is in 1962:700)
                  p.endswith(" m.m.") or
                  p.endswith(" m. m.") or
                  p.endswith(" m.fl.") or
                  p.endswith(" m. fl.") or
                  self.re_ChapterRevoked(p)))):  # If the entire chapter's
                                           # been revoked, we still
                                           # want to count it as a
                                           # chapter

                # sys.stdout.write("chapter_id: '%s' failed second check" % p)
                return None

            # sometimes (2005:1207) it's a headline, referencing a
            # specific section somewhere else - if the "1 kap. " is
            # immediately followed by "5 \xa7 " then that's probably the
            # case
            if (p.endswith(" \xa7") or
                p.endswith(" \xa7\xa7") or
                    (p.endswith(" stycket") and " \xa7 " in p)):
                return None

            # Om det ser ut som en tabell är det nog ingen
            # kapitelrubrik -- borttaget, triggade inget
            # regressionstest och orsakade bug 168
            # if self.isTabell(p, requireColumns=True):
            #    return None
            else:
                return m.group(1)
        else:
            # sys.stdout.write("chapter_id: '%s' failed first check" % p[:40])
            return None

    def isRubrik(self, p=None):
        if p is None:
            p = self.reader.peekparagraph()
            indirect = False
        else:
            indirect = True

        self.trace['rubrik'].debug("isRubrik (%s): indirect=%s" % (
            p[:50], indirect))

        if len(p) > 0 and p[0].lower() == p[0] and not p.startswith("/Rubriken"):
            self.trace['rubrik'].debug(
                "isRubrik (%s): starts with lower-case" % (p[:50]))
            return False

        # self.trace['rubrik'].debug("isRubrik: p=%s" % p)
        if len(p) > 110:  # it shouldn't be too long, but some headlines are insanely verbose
            self.trace['rubrik'].debug("isRubrik (%s): too long" % (p[:50]))
            return False

        # A headline should not look like the start of a paragraph or a numbered list
        if self.isParagraf(p):
            self.trace['rubrik'].debug(
                "isRubrik (%s): looks like para" % (p[:50]))
            return False

        if self.isNumreradLista(p):
            self.trace['rubrik'].debug(
                "isRubrik (%s): looks like numreradlista" % (p[:50]))
            return False

        if self.isStrecksatslista(p):
            self.trace['rubrik'].debug(
                "isRubrik (%s): looks like strecksatslista" % (p[:50]))
            return False

        if (p.endswith(".") and  # a headline never ends with a period, unless it ends with "m.m." or similar
            not (p.endswith("m.m.") or
                 p.endswith("m. m.") or
                 p.endswith("m.fl.") or
                 p.endswith("m. fl."))):
            self.trace['rubrik'].debug(
                "isRubrik (%s): ends with period" % (p[:50]))
            return False

        if (p.endswith(",") or  # a headline never ends with these characters
            p.endswith(":") or
            p.endswith("samt") or
                p.endswith("eller")):
            self.trace['rubrik'].debug(
                "isRubrik (%s): ends with comma/colon etc" % (p[:50]))
            return False

        if self.re_ChangeNote.search(p):  # eg 1994:1512 8 \xa7
            return False

        if p.startswith("/") and p.endswith("./"):
            self.trace['rubrik'].debug(
                "isRubrik (%s): Seems like a comment" % (p[:50]))
            return False

        try:
            nextp = self.reader.peekparagraph(2)
        except IOError:
            nextp = ''

        # finally, it should be followed by a paragraph - but this
        # test is only done if this check is not indirect (to avoid
        # infinite recursion)
        if not indirect:
            if (not self.isParagraf(nextp)) and (not self.isRubrik(nextp)):
                self.trace['rubrik'].debug(
                    "isRubrik (%s): is not followed by a paragraf or rubrik" % (p[:50]))
                return False

        # if this headline is followed by a second headline, that
        # headline and all subsequent headlines should be regardes as
        # sub-headlines
        if (not indirect) and self.isRubrik(nextp):
            self.current_headline_level = 1

        # ok, all tests passed, this might be a headline!
        self.trace['rubrik'].debug(
            "isRubrik (%s): All tests passed!" % (p[:50]))

        return True

    def isUpphavdParagraf(self):
        match = self.re_SectionRevoked(self.reader.peekline())
        return match is not None

    def isParagraf(self, p=None):
        if not p:
            p = self.reader.peekparagraph()
            self.trace['paragraf'].debug(
                "isParagraf: called w/ '%s' (peek)" % p[:30])
        else:
            self.trace['paragraf'].debug("isParagraf: called w/ '%s'" % p[:30])

        paragrafnummer = self.idOfParagraf(p)
        if paragrafnummer is None:
            self.trace['paragraf'].debug(
                "isParagraf: '%s': no paragrafnummer" % p[:30])
            return False
        if paragrafnummer == '1':
            self.trace['paragraf'].debug(
                "isParagraf: paragrafnummer = 1, return true")
            return True
        # now, if this sectionid is less than last section id, the
        # section is probably just a reference and not really the
        # start of a new section. One example of that is
        # /1991:1469#K1P7S1.
        if util.numcmp(paragrafnummer, self.current_section) < 0:
            self.trace['paragraf'].debug(
                "isParagraf: section numbering compare failed (%s <= %s)" % (paragrafnummer, self.current_section))
            return False

        # a similar case exists in 1994:260 and 2007:972, but there
        # the referenced section has a number larger than last section
        # id. Try another way to detect this by looking at the first
        # character in the paragraph - if it's in lower case, it's
        # probably not a paragraph.
        firstcharidx = (len(paragrafnummer) + len(' \xa7 '))
        # print "%r: %s" % (p, firstcharidx)
        if ((len(p) > firstcharidx) and
                (p[len(paragrafnummer) + len(' \xa7 ')].islower())):
            self.trace['paragraf'].debug(
                "isParagraf: section '%s' did not start with uppercase" % p[len(paragrafnummer) + len(' \xa7 '):30])
            return False
        return True

    def idOfParagraf(self, p):
        match = self.re_SectionId.match(p)
        if match:
            return match.group(1)
        else:
            match = self.re_SectionIdOld.match(p)
            if match:
                return match.group(1)
            else:
                return None

    # Om assumeTable är True är testerna något generösare än
    # annars. Den är False för den första raden i en tabell, men True
    # för de efterföljande.
    #
    # Om requireColumns är True krävs att samtliga rader är
    # spaltuppdelade

    def isTabell(self, p=None, assumeTable=False, requireColumns=False):
        shortline = 55
        shorterline = 52
        if not p:
            p = self.reader.peekparagraph()
        # Vissa snedformatterade tabeller kan ha en högercell som går
        # ned en rad för långt gentemot nästa rad, som har en tom
        # högercell:

        # xxx xxx xxxxxx     xxxx xx xxxxxx xx
        # xxxxx xx xx x      xxxxxx xxx xxx x
        #                    xx xxx xxx xxx
        # xxx xx xxxxx xx
        # xx xxx xx x xx

        # dvs något som egentligen är två stycken läses in som
        # ett. Försök hitta sådana fall, och titta i så fall endast på
        # första stycket
        lines = []
        emptyleft = False
        for l in p.split(self.reader.linesep):
            if l.startswith(' '):
                emptyleft = True
                lines.append(l)
            else:
                if emptyleft:
                    self.trace['tabell'].debug(
                        "isTabell('%s'): Snedformatterade tabellrader" % (p[:20]))
                    break
                else:
                    lines.append(l)

        numlines = len(lines)
        # Heuristiken för att gissa om detta stycke är en tabellrad:
        # Om varje rad
        # 1. Är kort (indikerar en tabellrad med en enda vänstercell)
        self.trace['tabell'].debug(
            "assumeTable: %s numlines: %s requireColumns: %s " % (assumeTable, numlines, requireColumns))
        if (assumeTable or numlines > 1) and not requireColumns:
            matches = [l for l in lines if len(l) < shortline]
            if numlines == 1 and '  ' in lines[0]:
                self.trace['tabell'].debug(
                    "isTabell('%s'): Endast en rad, men tydlig kolumnindelning" % (p[:20]))
                return True
            if len(matches) == numlines:
                self.trace['tabell'].debug(
                    "isTabell('%s'): Alla rader korta, undersöker undantag" % (p[:20]))

                # generellt undantag: Om en tabells första rad har
                # enbart vänsterkolumn M\XE5STE den följas av en
                # spaltindelad rad - annars är det nog bara två korta
                # stycken, ett kort stycke följt av kort rubrik, eller
                # liknande.
                try:
                    p2 = self.reader.peekparagraph(2)
                except IOError:
                    p2 = ''
                try:
                    p3 = self.reader.peekparagraph(3)
                except IOError:
                    p3 = ''
                if not assumeTable and not self.isTabell(p2,
                                                         assumeTable=True,
                                                         requireColumns=True):
                    self.trace['tabell'].debug(
                        "isTabell('%s'): generellt undantag från alla rader korta-regeln" % (p[:20]))
                    return False
                elif numlines == 1:
                    # Om stycket har en enda rad *kan* det vara en kort
                    # rubrik -- kolla om den följs av en paragraf, isåfall
                    # är nog tabellen slut
                    # FIXME: Kolla om inte generella undantaget borde
                    # fånga det här. Testfall
                    # regression-tabell-foljd-av-kort-rubrik.txt och
                    # temporal-paragraf-med-tabell.txt
                    if self.isParagraf(p2):
                        self.trace['tabell'].debug(
                            "isTabell('%s'): Specialundantag: följs av Paragraf, inte Tabellrad" % (p[:20]))
                        return False
                    if self.isRubrik(p2) and self.isParagraf(p3):
                        self.trace['tabell'].debug(
                            "isTabell('%s'): Specialundantag: följs av Rubrik och sedan Paragraf, inte Tabellrad" % (p[:20]))
                        return False
                    # Om stycket är *exakt* detta signalerar det nog
                    # övergången från tabell (kanske i slutet på en
                    # bilaga, som i SekrL) till övergångsbestämmelserna
                    if self.isOvergangsbestammelser():
                        self.trace['tabell'].debug(
                            "isTabell('%s'): Specialundantag: Övergångsbestämmelser" % (p[:20]))
                        return False
                    if self.isBilaga():
                        self.trace['tabell'].debug(
                            "isTabell('%s'): Specialundantag: Bilaga" % (p[:20]))
                        return False

                # Detta undantag behöves förmodligen inte när genererella undantaget används
                # elif (numlines == 2 and
                #      self.isNumreradLista() and (
                #    lines[1].startswith('Förordning (') or
                #    lines[1].startswith('Lag ('))):
                #
                #        self.trace['tabell'].debug("isTabell('%s'): Specialundantag: ser ut som nummerpunkt följd av ändringsförfattningshänvisning" % (p[:20]))
                #        return False

                # inget av undantagen tillämpliga, huvudregel 1 gäller
                self.trace['tabell'].debug(
                    "isTabell('%s'): %s rader, alla korta" % (p[:20], numlines))
                return True

        # 2. Har mer än ett mellanslag i följd på varje rad (spaltuppdelning)
        matches = [l for l in lines if '  ' in l]
        if numlines > 1 and len(matches) == numlines:
            self.trace['tabell'].debug(
                "isTabell('%s'): %s rader, alla spaltuppdelade" % (p[:20], numlines))
            return True

        # 3. Är kort ELLER har spaltuppdelning
        self.trace['tabell'].debug("test 3")
        if (assumeTable or numlines > 1) and not requireColumns:
            self.trace['tabell'].debug("test 3.1")
            matches = [l for l in lines if '  ' in l or len(l) < shorterline]
            if len(matches) == numlines:
                self.trace['tabell'].debug(
                    "isTabell('%s'): %s rader, alla korta eller spaltuppdelade" % (p[:20], numlines))
                return True

        # 3. Är enrading med TYDLIG tabelluppdelning
        if numlines == 1 and '   ' in l:
            self.trace['tabell'].debug(
                "isTabell('%s'): %s rader, alla spaltuppdelade" % (p[:20], numlines))
            return True

        self.trace['tabell'].debug("isTabell('%s'): %s rader, inga test matchade (aT:%r, rC: %r)" %
                                   (p[:20], numlines, assumeTable, requireColumns))
        return False

    def makeTabell(self):
        pcnt = 0
        t = Tabell()
        autostrip = self.reader.autostrip
        self.reader.autostrip = False
        p = self.reader.readparagraph()
        self.trace['tabell'].debug("makeTabell: 1st line: '%s'" % p[:30])
        (trs, tabstops) = self.makeTabellrad(p)
        t.extend(trs)
        while (not self.reader.eof()):
            (l, upphor, ikrafttrader) = self.andringsDatum(
                self.reader.peekline(), match=True)
            if upphor:
                current_upphor = upphor
                self.reader.readline()
                pcnt = 1
            elif ikrafttrader:
                current_ikrafttrader = ikrafttrader
                current_upphor = None
                self.reader.readline()
                pcnt = -pcnt + 1
            elif self.isTabell(assumeTable=True):
                kwargs = {}
                if pcnt > 0:
                    kwargs['upphor'] = current_upphor
                    pcnt += 1
                elif pcnt < 0:
                    kwargs['ikrafttrader'] = current_ikrafttrader
                    pcnt += 1
                elif pcnt == 0:
                    current_ikrafttrader = None
                p = self.reader.readparagraph()
                if p:
                    (trs, tabstops) = self.makeTabellrad(
                        p, tabstops, kwargs=kwargs)
                    t.extend(trs)
            else:
                self.reader.autostrip = autostrip
                return t

        self.reader.autostrip = autostrip
        return t

    def makeTabellrad(self, p, tabstops=None, kwargs={}):
        # Algoritmen är anpassad för att hantera tabeller där texten inte
        # alltid är så jämnt ordnat i spalter, som fallet är med
        # SFSR-datat (gissningvis på grund av någon trasig
        # tab-till-space-konvertering nånstans).
        def makeTabellcell(text):
            if len(text) > 1:
                text = self.re_dehyphenate("", text)
            return Tabellcell([util.normalize_space(text)])

        cols = ['', '', '', '', '', '', '', '']
            # Ingen tabell kommer nånsin ha mer än åtta kolumner
        if tabstops:
            statictabstops = True  # Använd de tabbstoppositioner vi fick förra raden
        else:
            statictabstops = False  # Bygg nya tabbstoppositioner från scratch
            self.trace['tabell'].debug("rebuilding tabstops")
            tabstops = [0, 0, 0, 0, 0, 0, 0, 0]
        lines = p.split(self.reader.linesep)
        numlines = len([x for x in lines if x])
        potentialrows = len(
            [x for x in lines if x and (x[0].isupper() or x[0].isdigit())])
        linecount = 0
        self.trace['tabell'].debug(
            "numlines: %s, potentialrows: %s" % (numlines, potentialrows))
        if (numlines > 1 and numlines == potentialrows):
            self.trace['tabell'].debug(
                'makeTabellrad: Detta verkar vara en tabellrad-per-rad')
            singlelinemode = True
        else:
            singlelinemode = False

        rows = []
        emptyleft = False
        for l in lines:
            if l == "":
                continue
            linecount += 1
            charcount = 0
            spacecount = 0
            lasttab = 0
            colcount = 0
            if singlelinemode:
                cols = ['', '', '', '', '', '', '', '']
            if l[0] == ' ':
                emptyleft = True
            else:
                if emptyleft:
                    self.trace['tabell'].debug(
                        'makeTabellrad: skapar ny tabellrad pga snedformatering')
                    rows.append(cols)
                    cols = ['', '', '', '', '', '', '', '']
                    emptyleft = False

            for c in l:
                charcount += 1
                if c == ' ':
                    spacecount += 1
                else:
                    if spacecount > 1:  # Vi har stött på en ny tabellcell
                                       # - fyll den gamla
                        # Lägg till en nyrad för att ersätta den vi kapat -
                        # överflödig whitespace trimmas senare
                        cols[colcount] += '\n' + l[
                            lasttab:charcount - (spacecount + 1)]
                        lasttab = charcount - 1

                        # för hantering av tomma vänsterceller
                        if linecount > 1 or statictabstops:
                            # tillåt en ojämnhet om max sju tecken
                            if tabstops[colcount + 1] + 7 < charcount:
                                if len(tabstops) <= colcount + 2:
                                    tabstops.append(0)
                                    cols.append('')
                                self.trace['tabell'].debug(
                                    'colcount is %d, # of tabstops is %d' % (colcount, len(tabstops)))
                                self.trace['tabell'].debug('charcount shoud be max %s, is %s - adjusting to next tabstop (%s)' % (
                                    tabstops[colcount + 1] + 5, charcount, tabstops[colcount + 2]))
                                if tabstops[colcount + 2] != 0:
                                    self.trace['tabell'].debug(
                                        'safe to advance colcount')
                                    colcount += 1
                        colcount += 1
                        if len(tabstops) <= charcount:
                            tabstops.append(0)
                            cols.append('')
                        tabstops[colcount] = charcount
                        self.trace['tabell'].debug(
                            "Tabstops now: %r" % tabstops)
                    spacecount = 0
            cols[colcount] += '\n' + l[lasttab:charcount]
            self.trace['tabell'].debug("Tabstops: %r" % tabstops)
            if singlelinemode:
                self.trace['tabell'].debug(
                    'makeTabellrad: skapar ny tabellrad')
                rows.append(cols)

        if not singlelinemode:
            rows.append(cols)

        self.trace['tabell'].debug(repr(rows))

        res = []
        for r in rows:
            tr = Tabellrad(**kwargs)
            emptyok = True
            for c in r:
                if c or emptyok:
                    tr.append(makeTabellcell(c.replace("\n", " ")))
                    if c.strip() != '':
                        emptyok = False
            res.append(tr)

        return (res, tabstops)

    def isFastbredd(self):
        return False

    def makeFastbredd(self):
        return None

    def isNumreradLista(self, p=None):
        return self.idOfNumreradLista(p) is not None

    def idOfNumreradLista(self, p=None):
        if not p:
            p = self.reader.peekline()
            self.trace['numlist'].debug(
                "idOfNumreradLista: called directly (%s)" % p[:30])
        else:
            self.trace['numlist'].debug(
                "idOfNumreradLista: called w/ '%s'" % p[:30])
        match = self.re_DottedNumber.match(p)

        if match is not None:
            self.trace['numlist'].debug(
                "idOfNumreradLista: match DottedNumber")
            return match.group(1).replace(" ", "")
        else:
            match = self.re_NumberRightPara(p)
            if match is not None:
                self.trace['numlist'].debug(
                    "idOfNumreradLista: match NumberRightPara")
                return match.group(1).replace(" ", "")

        self.trace['numlist'].debug("idOfNumreradLista: no match")
        return None

    def isStrecksatslista(self, p=None):
        if not p:
            p = self.reader.peekline()

        return (p.startswith("- ") or
                p.startswith("\x96 ") or
                p.startswith("--"))

    def isBokstavslista(self):
        return self.idOfBokstavslista() is not None

    def idOfBokstavslista(self):
        p = self.reader.peekline()
        match = self.re_Bokstavslista.match(p)

        if match is not None:
            return match.group(1).replace(" ", "")
        return None

    def isOvergangsbestammelser(self):
        separators = ['Övergångsbestämmelser',
                      'Ikraftträdande- och övergångsbestämmelser',
                      'Övergångs- och ikraftträdandebestämmelser']

        l = self.reader.peekline()
        if l not in separators:
            fuzz = difflib.get_close_matches(l, separators, 1, 0.9)
            if fuzz:
                self.log.warning("%s: Antar att '%s' ska vara '%s'?" %
                                 (self.id, l, fuzz[0]))
            else:
                return False
        try:
            # if the separator "Övergångsbestämmelser" (or similar) is
            # followed by a regular paragraph, it was probably not a
            # separator but an ordinary headline (occurs in a few law
            # texts)
            np = self.reader.peekparagraph(2)
            if self.isParagraf(np):
                return False

        except IOError:
            pass

        return True

    def isOvergangsbestammelse(self):
        return self.re_SimpleSfsId.match(self.reader.peekline())

    def isBilaga(self):
        (line, upphor, ikrafttrader) = self.andringsDatum(
            self.reader.peekline())
        return (line in ("Bilaga", "Bilaga*", "Bilaga *",
                         "Bilaga 1", "Bilaga 2", "Bilaga 3",
                         "Bilaga 4", "Bilaga 5", "Bilaga 6"))

    _document_name_cache = {}

    def _generateAnnotations(self, annotationfile, basefile):
        baseuri = self.canonical_uri(basefile)
        start = time()
        # Putting togeher a (non-normalized) RDF/XML file, suitable
        # for XSLT inclusion in six easy steps
        stuff = {}
        # 1. all rinfo:Rattsfallsreferat that has baseuri as a
        # rinfo:lagrum, either directly or through a chain of
        # dct:isPartOf statements
        start = time()
        rattsfall = self._store_run_query(
            "sparql/sfs_rattsfallsref.sq", uri=baseuri)
        self.log.debug('%s: Orig: Selected %d legal cases (%.3f sec)',
                       basefile, len(rattsfall), time() - start)
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

        # 2. all law sections that has a dct:references that matches this (using dct:isPartOf).
        start = time()
        start = time()
        inboundlinks = self._store_run_query(
            "sparql/sfs_inboundlinks.sq", uri=baseuri)
        self.log.debug('%s:  New: Selected %d inbound links (%.3f sec)',
                       basefile, len(inboundlinks), time() - start)
        self.log.debug('%s: Selected %d inbound links (%.3f sec)',
                       basefile, len(inboundlinks), time() - start)
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
        # 3. all wikientries that dct:description this
        start = time()
        #wikidesc = self._store_run_query("sparql/sfs_wikientries_orig.sq",uri=baseuri)
        #self.log.debug('%s: Orig: Selected %d wiki comments (%.3f sec)', basefile, len(wikidesc), time()-start)
        start = time()
        wikidesc = self._store_run_query(
            "sparql/sfs_wikientries.sq", uri=baseuri)
        self.log.debug('%s:  New: Selected %d wiki comments (%.3f sec)',
                       basefile, len(wikidesc), time() - start)
        # wikidesc = []
        for row in wikidesc:
            if not 'lagrum' in row:
                lagrum = baseuri
            else:
                lagrum = row['lagrum']

            if not lagrum in stuff:
                stuff[lagrum] = {}
            stuff[lagrum]['desc'] = row['desc']

        self.log.debug('%s: Selected %d wiki comments (%.3f sec)',
                       basefile, len(wikidesc), time() - start)

        # pprint(wikidesc)
        # (4. eurlex.nu data (mapping CELEX ids to titles))
        # (5. Propositionstitlar)
        # 6. change entries for each section
        # FIXME: we need to differentiate between additions, changes
        # and deletions
        start = time()
        #changes = self._store_run_query("sparql/sfs_changes_orig.sq",uri=baseuri)
        #self.log.debug('%s: Orig: Selected %d change annotations (%.3f sec)', basefile, len(changes), time()-start)
        start = time()
        changes = self._store_run_query("sparql/sfs_changes.sq", uri=baseuri)
        self.log.debug('%s:  New: Selected %d change annotations (%.3f sec)',
                       basefile, len(changes), time() - start)
        # changes = []

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
        #
        # The RDF dump looks something like:
        #
        # <rdf:RDF>
        # <rdf:Description about="http://rinfo.lagrummet.se/publ/sfs/1998:204#P1">
        #     <rinfo:isLagrumFor>
        #       <rdf:Description about="http://rinfo.lagrummet.se/publ/dom/rh/2004:51">
        #           <dct:identifier>RH 2004:51</dct:identifier>
        #           <dct:description>Hemsida på Internet. Fråga om...</dct:description>
        #       </rdf:Description>
        #     </rinfo:isLagrumFor>
        #     <dct:description>Personuppgiftslagens syfte är att skydda...</dct:description>
        #     <rinfo:isChangedBy>
        #        <rdf:Description about="http://rinfo.lagrummet.se/publ/sfs/2003:104">
        #           <dct:identifier>SFS 2003:104</dct:identifier>
        #           <rinfo:proposition>
        #             <rdf:Description about="http://rinfo.lagrummet.se/publ/prop/2002/03:123">
        #               <dct:title>Översyn av personuppgiftslagen</dct:title>
        #               <dct:identifier>Prop. 2002/03:123</dct:identifier>
        #             </rdf:Description>
        #           </rinfo:proposition>
        #        </rdf:Description>
        #     </rinfo:isChangedBy>
        #   </rdf:Description>
        # </rdf:RDF>

        start = time()
        root_node = etree.Element("rdf:RDF")
        for prefix in util.ns:
            # we need this in order to make elementtree not produce
            # stupid namespaces like "xmlns:ns0" when parsing an external
            # string like we do below (the etree.fromstring call)
            etree._namespace_map[util.ns[prefix]] = prefix
            root_node.set("xmlns:" + prefix, util.ns[prefix])

        for l in sorted(list(stuff.keys()), cmp=util.numcmp):
            lagrum_node = etree.SubElement(root_node, "rdf:Description")
            lagrum_node.set("rdf:about", l)
            if 'rattsfall' in stuff[l]:
                for r in stuff[l]['rattsfall']:
                    islagrumfor_node = etree.SubElement(
                        lagrum_node, "rinfo:isLagrumFor")
                    rattsfall_node = etree.SubElement(
                        islagrumfor_node, "rdf:Description")
                    rattsfall_node.set("rdf:about", r['uri'])
                    id_node = etree.SubElement(rattsfall_node, "dct:identifier")
                    id_node.text = r['id']
                    desc_node = etree.SubElement(
                        rattsfall_node, "dct:description")
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
                    # create a new dct:references node
                    if uri != prev_uri:
                        references_node = etree.Element("dct:references")
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
                    # dct:identifer
                    # print "uri: %s, next_uri: %s, baseuri: %s" %
                    # (uri[35:],next_uri[35:],baseuri[35:])
                    if (uri == next_uri) or (uri == baseuri):
                        form = "relative"
                    else:
                        form = "absolute"

                    inbound_node = etree.SubElement(
                        references_node, "rdf:Description")
                    inbound_node.set("rdf:about", inbound[i]['uri'])
                    id_node = etree.SubElement(inbound_node, "dct:identifier")
                    id_node.text = self.display_title(inbound[i]['uri'], form)

                    prev_uri = uri

            if 'changes' in stuff[l]:
                for r in stuff[l]['changes']:
                    ischanged_node = etree.SubElement(
                        lagrum_node, "rinfo:isChangedBy")
                    #rattsfall_node = etree.SubElement(islagrumfor_node, "rdf:Description")
                    # rattsfall_node.set("rdf:about",r['uri'])
                    id_node = etree.SubElement(ischanged_node, "rinfo:fsNummer")
                    id_node.text = r['id']
            if 'desc' in stuff[l]:
                desc_node = etree.SubElement(lagrum_node, "dct:description")
                xhtmlstr = "<xht2:div xmlns:xht2='%s'>%s</xht2:div>" % (
                    util.ns['xht2'], stuff[l]['desc'])
                xhtmlstr = xhtmlstr.replace(
                    ' xmlns="http://www.w3.org/2002/06/xhtml2/"', '')
                desc_node.append(etree.fromstring(xhtmlstr.encode('utf-8')))

        util.indent_et(root_node)
        # tree = etree.ElementTree(root_node)
        tmpfile = mktemp()
        treestring = etree.tostring(root_node, encoding="utf-8").replace(
            ' xmlns:xht2="http://www.w3.org/2002/06/xhtml2/"', '', 1)
        fp = open(tmpfile, "w")
        fp.write(treestring)
        fp.close()
        #tree.write(tmpfile, encoding="utf-8")
        util.replace_if_different(tmpfile, annotationfile)
        os.utime(annotationfile, None)
        self.log.debug(
            '%s: Serialized annotation (%.3f sec)', basefile, time() - start)

    def Generate(self, basefile):
        start = time()
        basefile = basefile.replace(":", "/")
        infile = util.relpath(self._xmlFileName(basefile))
        outfile = util.relpath(self._htmlFileName(basefile))

        annotations = "%s/%s/intermediate/%s.ann.xml" % (
            self.config.datadir, self.alias, basefile)

        force = (self.config.generateforce is True)

        dependencies = self._load_deps(basefile)
        wiki_comments = "data/wiki/parsed/SFS/%s.xht2" % basefile
        if os.path.exists(wiki_comments):
            dependencies.append(wiki_comments)

        if not force and self._outfile_is_newer(dependencies, annotations):
            if os.path.exists(self._depsFileName(basefile)):
                self.log.debug("%s: All %s dependencies untouched in rel to %s" %
                               (basefile, len(dependencies), util.relpath(annotations)))
            else:
                self.log.debug("%s: Has no dependencies" % basefile)

        else:
            self.log.info("%s: Generating annotation file", basefile)
            start = time()
            self._generateAnnotations(annotations, basefile)
            if time() - start > 5:
                self.log.info("openrdf-sesame is getting slow, reloading")
                cmd = "curl -u %s:%s http://localhost:8080/manager/reload?path=/openrdf-sesame" % (
                    self.config['tomcatuser'], self.config['tomcatpassword'])
                util.runcmd(cmd)
            else:
                sleep(0.5)  # let sesame catch it's breath

        if not force and self._outfile_is_newer([infile, annotations], outfile):
            self.log.debug("%s: Överhoppad", basefile)
            return

        util.mkdir(os.path.dirname(outfile))
        #params = {'annotationfile':annotations}
        # FIXME: create a relative version of annotations, instead of
        # hardcoding self.config.datadir like below
        params = {'annotationfile':
                  '../data/sfs/intermediate/%s.ann.xml' % basefile}

        # FIXME: Use pkg_resources to get at sfs.xsl
        self.transform_html("res/xsl/sfs.xsl", infile, outfile, parameters=params)

        self.log.info(
            '%s: OK (%s, %.3f sec)', basefile, outfile, time() - start)
        return

    def display_title(self, uri, form="absolute"):
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
                baseuri = legaluri.construct({'type': LegalRef.LAGRUM,
                                              'law': parts['law']})
                sq = """PREFIX dct:<http://purl.org/dc/terms/>
                        SELECT ?title WHERE {<%s> dct:title ?title }""" % baseuri
                changes = self._store_select(sq)
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

    def CleanupAnnulled(self, basefile):
        infile = self._xmlFileName(basefile)
        outfile = self._htmlFileName(basefile)
        if not os.path.exists(infile):
            util.robust_remove(outfile)

    @classmethod
    def relate_all_setup(cls, config):
        cls._build_mini_rdf()

    @classmethod
    def _build_mini_rdf(cls, config):
        # the resulting file contains one triple for each law text
        # that has comments (should this be in Wiki.py instead?
        termdir = os.path.sep.join([config.datadir, 'wiki', 'parsed', 'SFS'])
        minixmlfile = os.path.sep.join(
            [config.datadir, cls.alias, 'parsed', 'rdf-mini.xml'])
        files = list(util.list_dirs(termdir, ".xht2"))
        parser = LegalRef(LegalRef.LAGRUM)

        # self.log.info("Making a mini graph")
        mg = Graph()
        for key, value in list(util.ns.items()):
            mg.bind(key, Namespace(value))

        for f in files:
            basefile = ":".join(os.path.split(os.path.splitext(
                os.sep.join(os.path.normpath(f).split(os.sep)[-2:]))[0]))
            # print "Finding out URI for %s" % basefile
            try:
                uri = parser.parse(basefile)[0].uri
            except AttributeError:  # basefile is not interpretable as a SFS no
                continue
            mg.add((
                URIRef(uri), RDF.type, RPUBL['KonsolideradGrundforfattning']))

        # self.log.info("Serializing the minimal graph")
        f = open(minixmlfile, 'w')
        f.write(mg.serialize(format="pretty-xml"))
        f.close()

    def _file_to_basefile(self, f):
        """Override of LegalSource._file_to_basefile, with special
        handling of archived versions and two-part documents"""
        # this transforms 'foo/bar/baz/HDO/1-01.doc' to 'HDO/1-01'
        if '-' in f:
            return None
        basefile = "/".join(os.path.split(os.path.splitext(
            os.sep.join(os.path.normpath(f).split(os.sep)[-2:]))[0]))
        if basefile.endswith('_A') or basefile.endswith('_B'):
            basefile = basefile[:-2]
        return basefile

    def _indexpages_predicates(self):
        return [util.ns['dct'] + "title",
                util.ns['rinfo'] + 'fsNummer',
                util.ns['rdf'] + 'type',
                util.ns['rinfo'] + 'KonsolideradGrundforfattning']

    def _build_indexpages(self, by_pred_obj, by_subj_pred):
        documents = defaultdict(lambda: defaultdict(list))
        pagetitles = {}
        pagelabels = {}
        fsnr_pred = util.ns['rinfo'] + 'fsNummer'
        title_pred = util.ns['dct'] + 'title'
        type_pred = util.ns['rdf'] + 'type'
        type_obj = util.ns['rinfo'] + 'KonsolideradGrundforfattning'
        year_lbl = 'Ordnade efter utgivningsår'
        title_lbl = 'Ordnade efter titel'
        # construct the 404 page - we should really do this in the
        # form of a xht2 page that gets transformed using static.xsl,
        # but it's tricky to get xslt to output a href attribute with
        # an embedded (SSI) comment.
        doc = '''<?xml version="1.0"?>
<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Strict//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-strict.dtd"><html xmlns="http://www.w3.org/1999/xhtml" xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns:rinfo="http://rinfo.lagrummet.se/taxo/2007/09/rinfo/pub#" xmlns:xsd="http://www.w3.org/2001/XMLSchema#" xmlns:rinfoex="http://lagen.nu/terms#" xml:lang="sv" lang="sv"><head><meta http-equiv="Content-Type" content="text/html; charset=UTF-8" /><title>Författningstext saknas | Lagen.nu</title><script type="text/javascript" src="/js/jquery-1.2.6.min.js"></script><script type="text/javascript" src="/js/jquery.treeview.min.js"></script><script type="text/javascript" src="/js/base.js"></script><link rel="shortcut icon" href="/img/favicon.ico" type="image/x-icon" /><link rel="stylesheet" href="/css/screen.css" media="screen" type="text/css" /><link rel="stylesheet" href="/css/print.css" media="print" type="text/css" /></head><body><div id="vinjett"><h1><a href="/">lagen.nu</a></h1><ul id="navigation"><li><a href="/nyheter/">Nyheter</a></li><li><a href="/index/">Lagar</a></li><li><a href="/dom/index/">Domar</a></li><li><a href="/om/">Om</a></li></ul><form method="get" action="http://www.google.com/custom"><p><span class="accelerator">S</span>ök:<input type="text" name="q" id="q" size="40" maxlength="255" value="" accesskey="S" /><input type="hidden" name="cof" value="S:http://bself.log.tomtebo.org/;AH:center;AWFID:22ac01fa6655f6b6;" /><input type="hidden" name="domains" value="lagen.nu" /><input type="hidden" name="sitesearch" value="lagen.nu" checked="checked" /></p></form></div><div id="colmask" class="threecol"><div id="colmid"><div id="colleft"><div id="dokument">

    <h1>Författningstext saknas</h1>
    <p>Det verkar inte finnas någon författning med SFS-nummer
    <!--#echo var="REDIRECT_SFS" -->. Om den har funnits tidigare så
    kanske den har blivit upphävd?</p>
    <p>Om den har blivit upphävd kan den finnas i sin sista lydelse på
    Regeringskansliets rättsdatabaser:
    <a href="http://62.95.69.15/cgi-bin/thw?${HTML}=sfst_lst&amp;${OOHTML}=sfst_dok&amp;${SNHTML}=sfst_err&amp;${BASE}=SFST&amp;${TRIPSHOW}=format%3DTHW&amp;BET=<!--#echo var="REDIRECT_SFS" -->">Sök efter SFS <!--#echo var="REDIRECT_SFS" --></a>.</p>

  </div><div id="kommentarer"></div><div id="referenser"></div></div></div></div><div id="sidfot"><b>Lagen.nu</b> är en privat webbplats. Informationen här är  inte officiell och kan vara felaktig | <a href="/om/ansvarsfriskrivning.html">Ansvarsfriskrivning</a> | <a href="/om/kontakt.html">Kontaktinformation</a></div><script type="text/javascript">var gaJsHost = (("https:" == document.location.protocol) ? "https://ssl." : "http://www."); document.write(unescape("%3Cscript src='" + gaJsHost + "google-analytics.com/ga.js' type='text/javascript'%3E%3C/script%3E"));</script><script type="text/javascript">var pageTracker = _gat._getTracker("UA-172287-1"); pageTracker._trackPageview();</script></body></html>'''

        outfile = "%s/%s/generated/notfound.shtml" % (
            self.config.datadir, self.alias)
        fp = codecs.open(outfile, "w", encoding='utf-8')
        fp.write(doc)
        fp.close()
        print(("wrote %s" % outfile))

        # list all subjects that are of rdf:type rinfo:KonsolideradGrundforfattning
        for subj in by_pred_obj[type_pred][type_obj]:
            fsnr = by_subj_pred[subj][fsnr_pred]
            title = by_subj_pred[subj][title_pred]

            sorttitle = re.sub(r'Kungl\. Maj:ts ', '', title)
            sorttitle = re.sub(
                r'^(Lag|Förordning|Tillkännagivande|[kK]ungörelse) ?\([^\)]+\) ?(av|om|med|angående) ', '', sorttitle)
            year = fsnr.split(':')[0]
            letter = sorttitle[0].lower()

            pagetitles[year] = 'Författningar utgivna %s' % year
            pagelabels[year] = year
            documents[year_lbl][year].append({'uri': subj,
                                              'sortkey': fsnr,
                                              'title': title})

            if letter.isalpha():
                pagetitles[letter] = 'Författningar som börjar på "%s"' % letter.upper()
                pagelabels[letter] = letter.upper()
                documents[title_lbl][letter].append({'uri': subj,
                                                     'sortkey': sorttitle.lower(),
                                                     'title': sorttitle,
                                                     'leader': title.replace(sorttitle, '')})

        # FIXME: port the 'Nyckelbegrepp' code from 1.0
        #        import the old etiketter data and make a tag cloud or something

        for category in list(documents.keys()):
            for pageid in list(documents[category].keys()):
                outfile = "%s/%s/generated/index/%s.html" % (
                    self.config.datadir, self.alias, pageid)
                title = pagetitles[pageid]
                if category == year_lbl:
                    self._render_indexpage(
                        outfile, title, documents, pagelabels, category, pageid, docsorter=util.numcmp)
                else:
                    self._render_indexpage(outfile, title, documents,
                                           pagelabels, category, pageid)
                    if pageid == 'a':  # make index.html
                        outfile = "%s/%s/generated/index/index.html" % (
                            self.config.datadir, self.alias)
                        self._render_indexpage(outfile, title, documents,
                                               pagelabels, category, pageid)

    re_message = re.compile(r'(\d+:\d+) \[([^\]]*)\]')
    re_qname = re.compile(r'(\{.*\})(\w+)')
    re_sfsnr = re.compile(r'\s*(\(\d+:\d+\))')

    def _build_newspages(self, messages):
        changes = {}
        all_entries = []
        lag_entries = []
        ovr_entries = []
        for (timestamp, message) in messages:
            m = self.re_message.match(message)
            change = m.group(1)
            if change in changes:
                continue
            changes[change] = True
            bases = m.group(2).split(", ")
            basefile = "%s/%s/parsed/%s.xht2" % (
                self.config.datadir, self.alias, self.store.basefile_to_pathfrag(bases[0]))
            # print "opening %s" % basefile
            if not os.path.exists(basefile):
                # om inte den parseade filen finns kan det bero på att
                # författningen är upphävd _eller_ att det blev något
                # fel vid parseandet.
                self.log.warning("File %s not found" % basefile)
                continue
            tree, ids = etree.XMLID(open(basefile).read())

            if (change != bases[0]) and (not 'L' + change in ids):
                self.log.warning(
                    "ID %s not found in %s" % ('L' + change, basefile))
                continue

            if change != bases[0]:
                for e in ids['L' + change].findall(".//{http://www.w3.org/2002/06/xhtml2/}dd"):
                    if 'property' in e.attrib and e.attrib['property'] == 'dct:title':
                        title = e.text
            else:
                title = tree.find(
                    ".//{http://www.w3.org/2002/06/xhtml2/}title").text

            # use relative, non-rinfo uri:s here - since the atom
            # transform wont go through xslt and use uri.xslt
            uri = '/%s' % bases[0]

            for node in ids['L' + change]:
                m = self.re_qname.match(node.tag)
                if m.group(2) == 'dl':
                    content = self._element_to_string(node)

            entry = {'title': title,
                     'timestamp': timestamp,
                     'id': change,
                     'uri': uri,
                     'content': '<p><a href="%s">Författningstext</a></p>%s' % (uri, content)}
            all_entries.append(entry)

            basetitle = self.re_sfsnr.sub('', title)
            # print "%s: %s" % (change, basetitle)
            if (basetitle.startswith('Lag ') or
                (basetitle.endswith('lag') and not basetitle.startswith('Förordning')) or
                    basetitle.endswith('balk')):
                lag_entries.append(entry)
            else:
                ovr_entries.append(entry)

        htmlfile = "%s/%s/generated/news/all.html" % (
            self.config.datadir, self.alias)
        atomfile = "%s/%s/generated/news/all.atom" % (
            self.config.datadir, self.alias)
        self._render_newspage(
            htmlfile, atomfile, 'Nya och ändrade författningar', 'De senaste 30 dagarna', all_entries)

        htmlfile = "%s/%s/generated/news/lagar.html" % (
            self.config.datadir, self.alias)
        atomfile = "%s/%s/generated/news/lagar.atom" % (
            self.config.datadir, self.alias)
        self._render_newspage(htmlfile, atomfile, 'Nya och ändrade lagar',
                              'De senaste 30 dagarna', lag_entries)

        htmlfile = "%s/%s/generated/news/forordningar.html" % (
            self.config.datadir, self.alias)
        atomfile = "%s/%s/generated/news/forordningar.atom" % (
            self.config.datadir, self.alias)
        self._render_newspage(
            htmlfile, atomfile, 'Nya och ändrade förordningar och övriga författningar', 'De senaste 30 dagarna', ovr_entries)

    def _element_to_string(self, e):
        """Creates a XHTML1 string from a elementtree.Element,
        removing namespaces and rel/propery attributes"""
        m = self.re_qname.match(e.tag)
        tag = m.group(2)

        if list(e.attrib.keys()):
            attributestr = " " + \
                " ".join([x + '="' + e.attrib[x].replace('"', '&quot;') +
                         '"' for x in list(e.attrib.keys()) if x not in ['rel', 'property']])
        else:
            attributestr = ""

        childstr = ''
        for child in e:
            childstr += self._element_to_string(child)

        text = ''
        tail = ''
        if e.text:
            text = cgi.escape(e.text)
        if e.tail:
            tail = cgi.escape(e.tail)
        return "<%s%s>%s%s%s</%s>" % (tag, attributestr, text, childstr, tail, tag)
