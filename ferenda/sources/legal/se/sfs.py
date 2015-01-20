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
import shutil

from six.moves import html_parser
from six.moves.urllib_parse import quote, unquote
from six import text_type as str
from ferenda.compat import OrderedDict

# 3rdparty libs
import pkg_resources
from rdflib import Namespace, URIRef, Literal, RDF
from rdflib.namespace import DCTERMS
from lxml import etree
from lxml.builder import ElementMaker
import bs4
import requests
import requests.exceptions
from layeredconfig import LayeredConfig

# my own libraries
from . import Trips
# from trips import Trips
from ferenda import DocumentEntry, DocumentStore, TripleStore
from ferenda import TextReader, Describer, Facet
from ferenda import decorators
from ferenda.sources.legal.se import legaluri
from ferenda import util
from ferenda.elements import CompoundElement
from ferenda.elements import OrdinalElement
from ferenda.elements import TemporalElement
from ferenda.elements import UnicodeElement
from ferenda.elements import Link
from ferenda.errors import DocumentRemovedError, ParseError
from ferenda.sources.legal.se.legalref import LegalRef, LinkSubject
from ferenda.sources.legal.se import SwedishCitationParser
RPUBL = Namespace('http://rinfo.lagrummet.se/ns/2008/11/rinfo/publ#')


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
    tagname = "div"
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

    def as_xhtml(self, uri=None, parent_uri=None):
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

    def as_xhtml(self, uri=None, parent_uri=None):
        # FIXME: Render this better (particularly the rpubl:andring
        # property -- should be parsed and linked)
        return super(Registerpost, self).as_xhtml()

class IckeSFS(ParseError):

    """Slängs när en författning som inte är en egentlig
    SFS-författning parsas"""


class UpphavdForfattning(DocumentRemovedError):
    pass


class IdNotFound(DocumentRemovedError):
    pass

class InteUppdateradSFS(Exception):
    pass


class InteExisterandeSFS(Exception):
    pass  # same as IdNotFound?


DCTERMS = Namespace(util.ns['dcterms'])
XSD = Namespace(util.ns['xsd'])
RINFOEX = Namespace("http://lagen.nu/terms#")


class SFSDocumentStore(DocumentStore):

    def basefile_to_pathfrag(self, basefile):
        return quote(basefile.replace(":", "/"))

    def pathfrag_to_basefile(self, pathfrag):
        return unquote(pathfrag.replace("\\", "/").replace("/", ":"))

    def register_path(self, basefile):
        return self.path(basefile, "register", ".html")

    def open_register(self, basefile, mode="r"):
        filename = self.register_path(basefile)
        return self._open(filename, mode)

    def metadata_path(self, basefile):
        return self.path(basefile, "metadata", ".html")

    def intermediate_path(self, basefile):
        return self.path(basefile, "intermediate", ".txt")

    

class SFS(Trips):

    """Handles consolidated (codified) versions of statutes from SFS (Svensk förvattningssamling)

    A note about logging:

    There are four additional loggers available ('paragraf', 'tabell',
    'numlist' and 'rubrik'). By default, manager.py turns them off
    unless config.trace.[logname] is set. Do something like

    ./ferenda-build.py sfs parse 2009:924 --force --sfs-trace-rubrik

    (sets the sfs.rubrik logger level to DEBUG) or
    
    ./ferenda-build.py sfs parse 2009:924 --force --sfs-trace-tabell=INFO

    """
    alias = "sfs"
    rdf_type = RPUBL.KonsolideradGrundforfattning
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

        # the new DNS-based URLs used to be dog slow for some reasons
        # sometimes -- a quick hack to change them back to the old
        # IP-based ones. The hack hade to be sidabled since the
        # IP-based URLs stopped working. Fortunaltely the DNS-based
        # ones has been sped up.
        
#        for p in ('document_url_template',
#                  'document_sfsr_url_template',
#                  'document_sfsr_change_url_template'):
#            setattr(self, p,
#                    getattr(self, p).replace('rkrattsbaser.gov.se',
#                                           '62.95.69.15'))
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

    def _makeimages(self):
        # FIXME: make sure a suitable font exists
        font = "Helvetica" 
        def makeimage(basename, label):
            filename = "res/img/sfs/%s.png" % basename
            if not os.path.exists(filename):
                util.ensure_dir(filename)
                self.log.info("Creating img %s with label %s" % (filename,label))
                cmd = 'convert -background transparent -fill Grey -font Helvetica -pointsize 10 -size 44x14 -gravity East label:"%s " %s' % (label,filename)
                util.runcmd(cmd)
            return filename
            
        ret = []
        for i in range(1,150):
            for j in ('','a','b'):
                ret.append(makeimage("K%d%s"%(i,j),"%d%s kap."%(i,j)))
        for i in range(1,100):
            ret.append(makeimage("S%d"%i,"%d st."%i))
        return ret


        

    # make sure our EBNF-based parsers (which are expensive to create)
    # only gets created if they are demanded.
    @property
    def lagrum_parser(self):
        if not hasattr(self, '_lagrum_parser'):
            self._lagrum_parser = SwedishCitationParser(LegalRef(LegalRef.LAGRUM,
                                                                 LegalRef.EGLAGSTIFTNING),
                                                        self.config.url,
                                                        allow_relative=True)
        return self._lagrum_parser

    @property
    def forarbete_parser(self):
        if not hasattr(self, '_forarbete_parser'):
            self._forarbete_parser = SwedishCitationParser(LegalRef(LegalRef.FORARBETEN),
                                                           self.config.url)
        return self._forarbete_parser

    def get_default_options(self):
        opts = super(SFS, self).get_default_options()
        opts['keepexpired'] = False
        opts['revisit'] = list
        opts['next_sfsnr'] = str
        return opts
    
    def canonical_uri(self, basefile, konsolidering=False):
        prefix = self.config.url + self.config.urlpath
        basefile = basefile.replace(" ", "_")
        if konsolidering:
            if konsolidering == True:
                return "%s%s/konsolidering" % (prefix, basefile)
            else:
                konsolidering = konsolidering.replace(" ", "_")
                return "%s%s/konsolidering/%s" % (prefix, basefile, konsolidering)
        else:
            return "%s%s" % (prefix, basefile)

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
                    self.log.info('Peeking for SFS %s:%s' % (year, nr+1)) # increments below
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
    re_ChapterId = re.compile(r'^(\d+( \w|)) [Kk][Aa][Pp]\.').match
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
        desc = Describer(doc.meta, doc.uri)

        try:
            registry = self.parse_sfsr(sfsr_file, doc.uri)
        except (UpphavdForfattning, IdNotFound) as e:
            e.dummyfile = self.store.parsed_path(doc.basefile)
            raise e

        # for uri, graph in registry.items():
        #    print("==== %s ====" % uri)
        #    print(graph.serialize(format="turtle").decode("utf-8"))

        try:
            plaintext = self.extract_sfst(sfst_file)
            plaintextfile = self.store.intermediate_path(doc.basefile)
            util.writefile(plaintextfile, plaintext, encoding="iso-8859-1")
            (plaintext, patchdesc) = self.patch_if_needed(doc.basefile,
                                                          plaintext)
            if patchdesc:
                desc.value(self.ns['rinfoex'].patchdescription,
                           patchdesc)

            # Main parsing logic goes here
            self.parse_sfst(plaintext, doc)
        except IOError:
            self.log.warning("%s: Fulltext saknas" % self.id)
            # extractSFST misslyckades, då det fanns någon post i
            # SFST-databasen (det händer alltför ofta att bara
            # SFSR-databasen är uppdaterad).
            # attempt to find out a title from SFSR
            baseuri = self.canonical_uri(doc.basefile)
            if baseuri in registry:
                title = registry[baseuri].value(URIRef(baseuri),
                                                self.ns['dcterms'].title)
                desc.value(self.ns['dcterms'].title, title)
            desc.rel(self.ns['dcterms'].publisher,
                     self.lookup_resource("Regeringskansliet"))

            desc.value(self.ns['dcterms'].identifier, "SFS " + doc.basefile)

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
        desc.value(self.ns['prov'].wasGeneratedBy, self.qualified_class_name())
        de = DocumentEntry(docentry_file)
        
        desc.value(self.ns['rinfoex'].senastHamtad, de.orig_updated)
        desc.value(self.ns['rinfoex'].senastKontrollerad, de.orig_checked)

        # find any established abbreviation
        grf_uri = self.canonical_uri(doc.basefile)
        v = self.commondata.value(URIRef(grf_uri), self.ns['dcterms'].alternate, any=True)
        if v:
            desc.value(self.ns['dcterms'].alternate, v)

        # Finally: the dcterms:issued property for this
        # rpubl:KonsolideradGrundforfattning isn't readily
        # available. The true value is only found by parsing PDF files
        # in another docrepo. There are three general ways of finding
        # it out.
        issued = None
        # 1. if registry contains a single value (ie a
        # Grundforfattning that hasn't been amended yet), we can
        # assume that dcterms:issued == rpubl:utfardandedatum
        if len(registry) == 1 and desc.getvalues(self.ns['rpubl'].utfardandedatum):
            issued = desc.getvalue(self.ns['rpubl'].utfardandedatum)
        else:
            # 2. if the last post in registry contains a
            # rpubl:utfardandedatum, assume that this version of the
            # rpubl:KonsolideradGrundforfattning has the same dcterms:issued date
            last_post_uri = list(registry.keys())[-1]
            last_post_graph = registry[last_post_uri]
            pub_lit = last_post_graph.value(URIRef(last_post_uri),
                                            self.ns['rpubl'].utfardandedatum)
            if pub_lit:
                issued = pub_lit.toPython()
        if not issued:
            # 3. general fallback: Use the corresponding orig_updated
            # on the DocumentEntry. This is not correct (as it
            # represents the date we fetched the document, not the
            # date the document was made available), but it's as close
            # as we can get.
            issued = de.orig_updated.date()
        assert isinstance(issued, date)
        desc.value(self.ns['dcterms'].issued, issued)

        # use manual formatting of the issued date -- date.strftime
        # doesn't work with years < 1900 in older versions of python
        rinfo_sameas = "http://rinfo.lagrummet.se/publ/sfs/%s/konsolidering/%d-%02d-%02d" % (
            doc.basefile.replace(" ", "_"), issued.year, issued.month, issued.day)
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
            identifier = graph.value(URIRef(uri), self.ns['dcterms'].identifier)
            identifier = identifier.replace("SFS ", "L")
            rp = Registerpost(uri=uri, meta=graph, id=identifier)
            reg.append(rp)
            if uri in obs:
                rp.append(obs[uri])

        doc.body.append(reg)
        self.parse_entry_update(doc)
        return True

    def _forfattningstyp(self, forfattningsrubrik):
        if (forfattningsrubrik.startswith('Lag ') or
            (forfattningsrubrik.endswith('lag') and not forfattningsrubrik.startswith('Förordning')) or
                forfattningsrubrik.endswith('balk')):
            return self.ns['rpubl'].Lag
        else:
            return self.ns['rpubl'].Forordning

    def _dict_to_graph(self, d, graph, uri):
        mapping = {'SFS nr': self.ns['rpubl'].fsNummer,
                   'Rubrik': self.ns['dcterms'].title,
                   'Senast hämtad': self.ns['rinfoex'].senastHamtad,
                   'Utfärdad': self.ns['rpubl'].utfardandedatum,
                   'Utgivare': self.ns['dcterms'].publisher,
                   'Departement/ myndighet': self.ns['dcterms'].creator
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

        # do we really have a registry?
        notfound = soup.find(text="Sökningen gav ingen träff!")
        if notfound:
            raise IdNotFound(str(notfound))
        
        d = OrderedDict()
        rubrik = util.normalize_space(soup.body('table')[2].text)
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
                val = util.normalize_space(row('td')[1].text.replace('\xa0', ' '))
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
                    desc.value(self.ns['dcterms'].identifier, "SFS " + val)
                    desc.value(self.ns['rpubl'].arsutgava, arsutgava)
                    desc.value(self.ns['rpubl'].lopnummer, lopnummer)
                    # desc.value("rpubl:lopnummer", lopnummer)

                elif key == 'Ansvarig myndighet':
                    try:
                        authrec = self.lookup_resource(self.clean_departement(val))
                        desc.rel(self.ns['rpubl'].departement, authrec)
                    except Exception:
                        desc.value(self.ns['rpubl'].departement, val)
                elif key == 'Rubrik':
                    # Change acts to Balkar never contain the SFS no
                    # of the Balk.
                    if not self.id.replace("_", " ") in val and not val.endswith("balken"):
                        self.log.warning(
                            "%s: Base SFS %s not found in title %r" % (self.id, self.id, val))
                    desc.value(self.ns['dcterms'].title, Literal(val, lang="sv"))
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
                            self.log.warning("%s: Okänd omfattningstyp %r" % (self.id, changecat))
                            pred = None
                        old_currenturl = self.lagrum_parser._currenturl
                        self.lagrum_parser._currenturl = docuri
                        for node in self.lagrum_parser.parse_string(changecat, pred):
                            if hasattr(node, 'predicate'):
                                desc.rel(node.predicate, node.uri)
                        self.lagrum_parser._currenturl = old_currenturl
                    # Secondly, preserve the entire text
                    desc.value(self.ns['rpubl'].andrar, val)
                elif key == 'Förarbeten':
                    for node in self.forarbete_parser.parse_string(val, "rpubl:forarbete"):
                        if hasattr(node, 'uri'):
                            with desc.rel(self.ns['rpubl'].forarbete,
                                          node.uri):
                                desc.value(self.ns['dcterms'].identifier,
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
            desc.rel(self.ns['dcterms'].publisher,
                     self.lookup_resource("Regeringskansliet"))
            desc.rel(self.ns['rpubl'].beslutadAv,
                     self.lookup_resource("Regeringskansliet"))
            desc.rel(self.ns['rpubl'].forfattningssamling,
                     "http://rinfo.lagrummet.se/serie/fs/sfs")
            desc.rel(self.ns['owl'].sameAs,
                     "http://rinfo.lagrummet.se/publ/sfs/" + sfsnr.replace(" ", "_"))
            utfardandedatum = self._find_utfardandedatum(sfsnr)
            if utfardandedatum:
                desc.value(self.ns['rpubl'].utfardandedatum, utfardandedatum)

        return d

    def clean_departement(self, val):
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
        # add ending CRLF aids with producing better diffs
        txt += "\r\n"
        return txt

    # FIXME: should get hold of a real LNKeyword repo object and call
    # it's canonical_uri()
    def _term_to_subject(self, term):
        capitalized = term[0].upper() + term[1:]
        return 'https://lagen.nu/concept/%s' % capitalized.replace(' ', '_')

    # Post-processar dokumentträdet rekursivt och gör två saker:
    #
    # Hittar adresserbara enheter (delresurser som ska ha unika URI:s,
    # dvs kapitel, paragrafer, stycken, punkter) och konstruerar id's
    # för dem, exv K1P2S3N4 för 1 kap. 2 \xa7 3 st. 4 p
    #
    # Hittar begreppsdefinitioner i löptexten
    def _construct_ids(self, element, prefix, baseuri, skip_fragments=[],
                       find_definitions=False):
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

                for p in element:
                    if isinstance(p, Stycke):
                        # do an extra check in case "I denna paragraf
                        # avses med" occurs in the 2nd or later
                        # paragrapgh of a section
                        if self.re_definitions(p[0]):
                            find_definitions = "normal"
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
                    if hasattr(p, 'ordinal') and p.ordinal:
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
        self.lagrum_parser.parse_recursive(doc.body)

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
                desc.value(self.ns['dcterms'].title, Literal(val, lang="sv"))
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
                # this is only needed because of SFS 1942:724, which
                # has "Försvarsdepartementet, Socialdepartementet"...
                if "departementet, " in val:
                    vals = val.split(", ")
                else:
                    vals = [val]
                for val in vals:
                    authrec = self.lookup_resource(self.clean_departement(val))
                    desc.rel(self.ns['dcterms'].creator, authrec)
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

        desc.value(self.ns['dcterms'].identifier, identifier)
        desc.rel(self.ns['dcterms'].publisher,
                 self.lookup_resource("Regeringskansliet"))

        if not desc.getvalue(self.ns['dcterms'].title):
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
                    if hasattr(self, 'id'):
                        sfsnr = self.id
                        self.log.warning(
                            "%s: Övergångsbestämmelsen saknar SFS-nummer - antar %s" % (self.id, sfsnr))
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
                try:
                    if len(m.groups()) == 3:
                        dates[key] = datetime(int(m.group(1)),
                                              int(m.group(2)),
                                              int(m.group(3)))
                    else:
                        dates[key] = m.group(1)
                    line = regex.sub('', line)
                except ValueError: # eg if datestring was
                                   # "2014-081-01" or something
                                   # similarly invalid - result in no
                                   # match, eg unaffected line
                    pass

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

    def store_select(self, store, query_template, uri, context=None):
        if os.path.exists(query_template):
            fp = open(query_template, 'rb')
        elif pkg_resources.resource_exists('ferenda', query_template):
            fp = pkg_resources.resource_stream('ferenda', query_template)
        else:
            raise ValueError("query template %s not found" % query_template)
        params = {'uri': uri,
                  'context': context}
        sq = fp.read().decode('utf-8') % params
        fp.close()
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

        # 2. all law sections that has a dcterms:references that matches this (using dcterms:isPartOf).
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
                                          None, # need both mediawiki and sfs contexts
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
        # NOTE: The SFS RDF data does not yet contain change entries, this query always returns 0 rows
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
        #
        # The RDF dump looks something like:
        #
        # <rdf:RDF>
        # <rdf:Description about="http://rinfo.lagrummet.se/publ/sfs/1998:204#P1">
        #     <rpubl:isLagrumFor>
        #       <rdf:Description about="http://rinfo.lagrummet.se/publ/dom/rh/2004:51">
        #           <dcterms:identifier>RH 2004:51</dcterms:identifier>
        #           <dcterms:description>Hemsida på Internet. Fråga om...</dcterms:description>
        #       </rdf:Description>
        #     </rpubl:isLagrumFor>
        #     <dcterms:description>Personuppgiftslagens syfte är att skydda...</dcterms:description>
        #     <rpubl:isChangedBy>
        #        <rdf:Description about="http://rinfo.lagrummet.se/publ/sfs/2003:104">
        #           <dcterms:identifier>SFS 2003:104</dcterms:identifier>
        #           <rpubl:proposition>
        #             <rdf:Description about="http://rinfo.lagrummet.se/publ/prop/2002/03:123">
        #               <dcterms:title>Översyn av personuppgiftslagen</dcterms:title>
        #               <dcterms:identifier>Prop. 2002/03:123</dcterms:identifier>
        #             </rdf:Description>
        #           </rpubl:proposition>
        #        </rdf:Description>
        #     </rpubl:isChangedBy>
        #   </rdf:Description>
        # </rdf:RDF>

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
                xhtmlstr = "<div xmlns='http://www.w3.org/1999/xhtml'>%s</div>" % stuff[l]['desc']
                desc_node.append(etree.fromstring(xhtmlstr.encode('utf-8')))

        # tree = etree.ElementTree(root_node)
        treestring = etree.tostring(root_node, encoding="utf-8", pretty_print=True)
        with self.store.open_annotation(basefile, mode="wb") as fp:
            fp.write(treestring)
        return self.store.annotation_path(basefile)

    def _unlocalize_uri(self, uri):
        # may need to munge https://lagen.nu/2010:1770#K1P2S1 back to
        # http://rinfo.lagrummet.se/publ/sfs/2010:1770#K1P2S1 since
        # that's what legalref expects. This is (or should be) the
        # inverse of SwedishLegalSource.localize_uri()
        prefix = self.config.url + self.config.urlpath
        return uri.replace(prefix, "http://rinfo.lagrummet.se/publ/sfs/")
        
    def display_title(self, uri, form="absolute"):
        # "https://lagen.nu/2010:1770#K1P2S1" => "Lag (2010:1770) om blahonga, 1 kap. 2 § 1 st."
        parts = legaluri.parse(self._unlocalize_uri(uri))
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

                changes = self.store_select(store, "res/sparql/sfs_title.rq", uri, self.dataset_uri())
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
            m = re.match("/Rubriken upphör att gälla U:([^/]+)/ *([^/]+)/Rubriken träder i kraft I:([^/]+)/ *([^/]+)", title)
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
        title = re.sub('Kungl\. Maj:ts ','',title)
        title = re.sub('^(Lag|Förordning|Tillkännagivande|[kK]ungörelse) ?\([^\)]+\) ?(av|om|med|angående) ','',title)
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


    def tabs(self):
        return [("Författningar", self.dataset_uri())]


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

    templ = [
        "downloaded/(?P<type>\w+)/(?P<byear>\d+)/(?P<bnum>[\d_s\.bih]+)\.html",
        # these next are only interesting for sfst, not sfsr
        "downloaded/sfst/(?P<byear>\d+)/(?P<bnum>[\d_s\.bih]+)-(?P<vyear>\d+)-(?P<vnum>[\d_s\.bih]+)\.html",
        "downloaded/sfst/(?P<byear>\d+)/(?P<bnum>[\d_s\.bih]+)-(?P<vyear>first)-(?P<vnum>version)\.html",
        "downloaded/sfst/(?P<byear>\d+)/(?P<bnum>[\d_s\.bih]+)-(?P<vyear>\d+)-(?P<vnum>[\d_s\.bih]+)-checksum-(?P<vcheck>[\w\d]+)\.html"
        ]

    @decorators.action
    def importarchive(self, archivedir):
        """Imports downloaded data from an archive from legacy lagen.nu data.

        In particular, creates proper archive storage for older
        versions of each text.

        """
#        import tarfile
#        if not tarfile.is_tarfile(archivefile):
#            self.log.error("%s is not a readable tarfile" % archivefile)
#            return
#        t = tarfile.open(archivefile)
#        for ti in t.getmembers():
#            if not ti.isfile():
#                continue
#            f = ti.name
        current = archived = 0
        for f in util.list_dirs(archivedir, ".html"):
            if not f.startswith("downloaded/sfs"):  # sfst or sfsr
                continue
            for regex in self.templ:
                m = re.match(regex, f)
                if not m:
                    continue
                if "vcheck" in m.groupdict():  # silently ignore
                    break
                basefile = "%s:%s" % (m.group("byear"), m.group("bnum"))

                # need to look at the file to find out its version
                # text = t.extractfile(f).read(4000).decode("latin-1")
                text = open(f).read(4000).decode("latin-1")
                reader = TextReader(string=text)
                updated_to = self._find_uppdaterad_tom(basefile,
                                                       reader=reader)

                if "vyear" in m.groupdict():  # this file is marked as an archival version
                    archived += 1
                    version = updated_to

                    if m.group("vyear") == "first":
                        pass
                    else:
                        exp = "%s:%s" % (m.group("vyear"), m.group("vnum"))
                        if version != exp:
                            self.log.warning("%s: Expected %s, found %s" %
                                             (f, exp, version))
                else:
                    version = None
                    current += 1
                    de = DocumentEntry()
                    de.basefile = basefile
                    de.id = self.canonical_uri(basefile, updated_to)
                    # fudge timestamps best as we can
                    de.orig_created = datetime.fromtimestamp(os.path.getctime(f))
                    de.orig_updated = datetime.fromtimestamp(os.path.getmtime(f))
                    de.orig_updated = datetime.now()
                    de.orig_url = self.document_url_template % locals()
                    de.published = datetime.now()
                    de.url = self.generated_url(basefile)
                    de.title  = "SFS %s" % basefile
                    # de.set_content()
                    # de.set_link()
                    de.save(self.store.documententry_path(basefile))
                    
                # this yields more reasonable basefiles, but they are not
                # backwards compatible -- skip them for now
                # basefile = basefile.replace("_", "").replace(".", "")

                if "type" in m.groupdict() and m.group("type") == "sfsr":
                    dest = self.store.register_path(basefile)
                    current -= 1  # to offset the previous increment
                else:
                    dest = self.store.downloaded_path(basefile, version)

                self.log.debug("%s: extracting %s to %s" % (basefile, f, dest))

                #with openmethod(basefile, mode="wb", version=version) as fp:
                #    fp.write(t.extractfile(f).read())
                util.ensure_dir(dest)
                shutil.copy2(f, dest)
                break
                    
            else:
                self.log.warning("Couldn't process %s" % f)


        self.log.info("Extracted %s current versions and %s archived versions" % (current, archived))
