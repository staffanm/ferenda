#!/usr/bin/env python
# -*- coding: iso-8859-1 -*-
"""Hanterar domslut (detaljer och referat) från Domstolsverket. Data
hämtas från DV:s (ickepublika) FTP-server, eller från lagen.nu."""
# system libraries
import sys
import os
import re
import shutil
import pprint
import types
import codecs
from time import time, mktime, sleep
from tempfile import mktemp
from datetime import datetime
import xml.etree.cElementTree as ET  # Python 2.5 spoken here
import xml.etree.ElementTree as PET
import logging
import zipfile
import traceback
from collections import defaultdict
from operator import itemgetter
import cgi
import textwrap

# 3rdparty libs
from rdflib import Graph, Literal, Namespace, URIRef, RDF, RDFS

# my libs
from ferenda import DocumentRepository
from ferenda import util
from ferenda.legalref import LegalRef, ParseError, Link, LinkSubject
from ferenda.elements import UnicodeElement, CompoundElement, \
    MapElement, IntElement, DateElement, PredicateType, \
    serialize
from . import SwedishLegalSource, RPUBL

__version__ = (1, 6)
__author__ = "Staffan Malmgren <staffan@tomtebo.org>"
__shortdesc__ = "Domslut (referat)"
__moduledir__ = "dv"
log = logging.getLogger(__moduledir__)

# Objektmodellen för rättsfall:
#
# Referat(list)
#   Metadata(map)
#       'Domstol':                 LinkSubject('Högsta Domstolen',predicate='dc:creator',uri='http://[AP-uri]')
#       'Referatnummer':           UnicodeSubject('NJA 1987 s 187', predicate='dc:identifier')
#       '[rattsfallspublikation]': UnicodeSubject('NJA,predicate='rpubl:rattsfallspublikation')
#       '[publikationsordinal]':   UnicodeSubject('1987:39',predicate='rpubl:publikationsordinal')
#       '[arsutgava]':             DateSubject(1987,predicate='rpubl:arsutgava')
#       '[sidnummer]':             IntSubject(187, predicate='rpubl:sidnummer')
#       'Målnummer':               UnicodeSubject('B 123-86',predicate='rpubl:malnummer')
#       'Domsnummer'               UnicodeSubject('',predicate='rpubl:domsnummer')
#       'Diarienummer':            UnicodeSubject('',predicate='rpubl:diarienummer')
#       'Avgörandedatum'           DateSubject(date(1987,3,14),predicate='rpubl:avgorandedatum')
#       'Rubrik'                   UnicodeSubject('',predicate='dc:description')
#       'Lagrum': list
#           Lagrum(list)
#              unicode/LinkSubject('4 kap. 13 § rättegångsbalken',uri='http://...',predicate='rpubl:lagrum')
#       'Rättsfall': list
#           Rattsfall(list)
#               unicode/LinkSubject('RÅ 1980 2:68',
#                                   uri='http://...',
#                                   predicate='rpubl:rattsfallshanvisning')
#       'Sökord': list
#           UnicodeSubject('Förhandsbesked',predicate='dc:subject')
#       'Litteratur': list
#           UnicodeSubject('Grosskopf, Skattenytt 1988, s. 182-183.', predicate='dct:relation')
#   Referatstext(list)
#       Stycke(list)
#           unicode/Link


class Referat(CompoundElement):
    pass


class Metadata(MapElement):
    pass


class UnicodeSubject(PredicateType, UnicodeElement):
    pass


class IntSubject(PredicateType, IntElement):
    pass


class DateSubject(PredicateType, DateElement):
    pass


class Lagrum(CompoundElement):
    pass


class Rattsfall(CompoundElement):
    pass


class Referatstext(CompoundElement):
    pass


class Stycke(CompoundElement):
    pass

# NB: You can't use this class unless you have an account on
# domstolsverkets FTP-server, and unfortunately I'm not at liberty to
# give mine out in the source code...

DCT = Namespace(util.ns['dct'])
XSD = Namespace(util.ns['xsd'])
RINFOEX = Namespace("http://lagen.nu/terms#")

class DV(DocumentRepository):
    def __init__(self, config):
        super(DVDownloader, self).__init__(
            config)  # sets config, logging initializes browser
        self.intermediate_dir = os.path.sep.join(
            [config['datadir'], __moduledir__, 'intermediate', 'word'])

    def _get_module_dir(self):
        return __moduledir__

    def DownloadAll(self):
        unpack_all = False
        if unpack_all:
            zipfiles = []
            for d in os.listdir(self.download_dir):
                if os.path.isdir("%s/%s" % (self.download_dir, d)):
                    for f in os.listdir("%s/%s" % (self.download_dir, d)):
                        if os.path.isfile("%s/%s/%s" % (self.download_dir, d, f)):
                            zipfiles.append(
                                "%s/%s/%s" % (self.download_dir, d, f))
            for f in os.listdir("%s" % (self.download_dir)):
                if os.path.isfile("%s/%s" % (self.download_dir, f)) and f.endswith(".zip"):
                    zipfiles.append("%s/%s" % (self.download_dir, f))

            for f in zipfiles:
                self.process_zipfile(f)
        else:
            self.download(recurse=True)

    def DownloadNew(self):
        self.download(recurse=False)

    def download(self, dirname='', recurse=False):
        if 'ftp_user' in self.config[__moduledir__]:
            try:
                self.download_ftp(dirname, recurse, self.config[__moduledir__]['ftp_user'], self.config[__moduledir__]['ftp_pass'])
            except util.ExternalCommandError:
                log.warning("download_ftp failed, not downloading anything")
        else:
            self.download_www(dirname, recurse)

    def download_ftp(self, dirname, recurse, user, password, ftp=None):
        #url = 'ftp://ftp.dom.se/%s' % dirname
        log.info('Listar innehåll i %s' % dirname)
        lines = []
        if not ftp:
            from ftplib import FTP
            ftp = FTP('ftp.dom.se')
            ftp.login(user, password)

        ftp.cwd(dirname)
        ftp.retrlines('LIST', lines.append)

        #cmd = "ncftpls -m -u %s -p %s %s" % (user, password, url)
        #(ret, stdout, stderr) = util.runcmd(cmd)
        #if ret != 0:
        #    raise util.ExternalCommandError(stderr)

        for line in lines:
            parts = line.split()
            filename = parts[-1].strip()
            if line.startswith('d') and recurse:
                self.download(filename, recurse)
            elif line.startswith('-'):
                if os.path.exists(os.path.sep.join([self.download_dir, dirname, filename])):
                    pass
                    # localdir = self.download_dir + os.path.sep + dirname
                    # self.process_zipfile(localdir + os.path.sep + filename)
                else:
                    if dirname:
                        fullname = '%s/%s' % (dirname, filename)
                        localdir = self.download_dir + os.path.sep + dirname
                        util.mkdir(localdir)
                    else:
                        fullname = filename
                        localdir = self.download_dir

                    log.info('Hämtar %s till %s' % (filename, localdir))
                    #os.system("ncftpget -u %s -p %s ftp.dom.se %s %s" %
                    #          (user, password, localdir, fullname))
                    ftp.retrbinary('RETR %s' % filename, open(
                        localdir + os.path.sep + filename, 'wb').write)
                    self.process_zipfile(localdir + os.path.sep + filename)
        ftp.cwd('/')

    def download_www(self, dirname, recurse):
        url = 'https://lagen.nu/dv/downloaded/%s' % dirname
        log.info('Listar innehåll i %s' % url)
        self.browser.open(url)
        links = list(self.browser.links())
        for l in links:
            if l.url.startswith("/"):
                continue
            elif l.url.endswith("/") and recurse:
                self.download_www(l.url, recurse)
            elif l.url.endswith(".zip"):
                if dirname:
                    fullname = dirname + l.url
                    localdir = self.download_dir + os.path.sep + dirname
                    util.mkdir(localdir)
                else:
                    fullname = l.url
                    localdir = self.download_dir

                localfile = "%s/%s" % (self.download_dir, fullname)
                if not os.path.exists(localfile):
                    log.info("Downloading %s" % (l.absolute_url))
                    self.browser.retrieve(l.absolute_url, localfile)
                    self.process_zipfile(localfile)

    # eg. HDO_T3467-96.doc or HDO_T3467-96_1.doc
    re_malnr = re.compile(r'([^_]*)_([^_\.]*)_?(\d*)\.(docx?)')
    # eg. HDO_T3467-96_BYTUT_2010-03-17.doc or HDO_T3467-96_BYTUT_2010-03-17_1.doc
    re_bytut_malnr = re.compile(
        r'([^_]*)_([^_\.]*)_BYTUT_\d+-\d+-\d+_?(\d*)\.(docx?)')
    re_tabort_malnr = re.compile(
        r'([^_]*)_([^_\.]*)_TABORT_\d+-\d+-\d+_?(\d*)\.(docx?)')

    def process_zipfile(self, zipfilename):
        removed = replaced = created = untouched = 0
        zipf = zipfile.ZipFile(zipfilename, "r")
        for name in zipf.namelist():
            if "_notis_" in name:
                continue
            # Namnen i zipfilen använder codepage 437 - retro!
            uname = name.decode('cp437')
            uname = os.path.split(uname)[1]
            log.debug("In: %s" % uname)
            if 'BYTUT' in name:
                m = self.re_bytut_malnr.match(uname)
            elif 'TABORT' in name:
                m = self.re_tabort_malnr.match(uname)
                # log.info('Ska radera!')
            else:
                m = self.re_malnr.match(uname)
            if m:
                (court, malnr, referatnr, suffix) = (
                    m.group(1), m.group(2), m.group(3), m.group(4))
                # log.debug("court %s, malnr %s, referatnr %s, suffix %s" % (court,malnr, referatnr, suffix))
                assert ((suffix == "doc") or (suffix == "docx")
                        ), "Unknown suffix %s in %r" % (suffix, uname)
                if referatnr:
                    outfilename = os.path.sep.join([self.intermediate_dir, court, "%s_%s.%s" % (malnr, referatnr, suffix)])
                else:
                    outfilename = os.path.sep.join([self.intermediate_dir, court, "%s.%s" % (malnr, suffix)])

                if "TABORT" in name:
                    log.info(
                        'Raderar befintligt referat %s %s' % (court, malnr))
                    if not os.path.exists(outfilename):
                        log.warning('Filen %s som ska tas bort fanns inte' %
                                    outfilename)
                    else:
                        os.unlink(outfilename)
                    removed += 1
                else:
                    # log.debug('%s: Packar upp %s' % (zipfilename, outfilename))
                    if "BYTUT" in name:
                        log.info('Byter ut befintligt referat %s %s' %
                                 (court, malnr))
                        if not os.path.exists(outfilename):
                            log.warning('Filen %s som ska bytas ut fanns inte' % outfilename)
                        self.download_log.info(outfilename)
                        replaced += 1
                    else:
                        if os.path.exists(outfilename):
                            untouched += 1
                            continue
                        else:
                            self.download_log.info(outfilename)
                            created += 1
                    data = zipf.read(name)

                    util.ensure_dir(outfilename)
                    # sys.stdout.write(".")
                    outfile = open(outfilename, "wb")
                    outfile.write(data)
                    outfile.close()
                    # Make the unzipped files have correct timestamp
                    zi = zipf.getinfo(name)
                    dt = datetime(*zi.date_time)
                    ts = mktime(dt.timetuple())
                    os.utime(outfilename, (ts, ts))
                    #log.debug("Out: %s" % outfilename)
            else:
                log.warning('Kunde inte tolka filnamnet %r i %s' %
                            (name, util.relpath(zipfilename)))
        log.info('Processade %s, skapade %s,  bytte ut %s, tog bort %s, lät bli %s filer' % (util.relpath(zipfilename), created, replaced, removed, untouched))

    re_NJAref = re.compile(r'(NJA \d{4} s\. \d+) \(alt. (NJA \d{4}:\d+)\)')
    # I wonder if we really should have : in this. Let's try without!
    re_delimSplit = re.compile("[;,] ?").split

    # Mappar termer för enkel metadata (enstaka
    # strängliteraler/datum/URI:er) från de strängar som används i
    # worddokumenten ('Målnummer') till de URI:er som används i
    # rpubl-vokabulären
    # ("http://rinfo.lagrummet.se/ns/2008/11/rinfo/publ##avgorandedatum").
    # FIXME: för allmänna och förvaltningsdomstolar ska kanske hellre
    # referatAvDomstolsavgorande användas än malnummer - det är
    # skillnad på ett domstolsavgörande och referatet av detsamma
    #
    # 'Referat' delas upp i rattsfallspublikation ('NJA'),
    # publikationsordinal ('1987:39'), arsutgava (1987) och sidnummer
    # (187). Alternativt kan publikationsordinal/arsutgava/sidnummer
    # ersättas med publikationsplatsangivelse.
    labels = {'Rubrik': DCT['description'],
              'Domstol': DCT['creator'],  # konvertera till auktoritetspost
              'Målnummer': RPUBL['malnummer'],
              'Domsnummer': RPUBL['domsnummer'],
              'Diarienummer': RPUBL['diarienummer'],
              'Avdelning': RPUBL['domstolsavdelning'],
              'Referat': DCT['identifier'],
              'Avgörandedatum': RPUBL['avgorandedatum'],  # konvertera till xsd:date
              }

    # Metadata som kan innehålla noll eller flera poster.
    # Litteratur/sökord har ingen motsvarighet i RPUBL-vokabulären
    multilabels = {'Lagrum': RPUBL['lagrum'],
                   'Rättsfall': RPUBL['rattsfallshanvisning'],
                   'Litteratur': DCT['relation'],  # dct:references vore bättre, men sådana ska inte ha literalvärden
                   'Sökord': DCT['subject']
                   }

    # Listan härledd från containers.n3/rattsfallsforteckningar.n3 i
    # rinfoprojektets källkod - en ambitiösare lösning vore att läsa
    # in de faktiska N3-filerna i en rdflib-graf.
    publikationsuri = {'NJA': 'http://rinfo.lagrummet.se/ref/rff/nja',
                       'RH': 'http://rinfo.lagrummet.se/ref/rff/rh',
                       'MÖD': 'http://rinfo.lagrummet.se/ref/rff/mod',
                       'RÅ': 'http://rinfo.lagrummet.se/ref/rff/ra',
                       'RK': 'http://rinfo.lagrummet.se/ref/rff/rk',
                       'MIG': 'http://rinfo.lagrummet.se/ref/rff/mig',
                       'AD': 'http://rinfo.lagrummet.se/ref/rff/ad',
                       'MD': 'http://rinfo.lagrummet.se/ref/rff/md',
                       'FÖD': 'http://rinfo.lagrummet.se/ref/rff/fod'}

    domstolsforkortningar = {'ADO': 'http://lagen.nu/org/2008/arbetsdomstolen',
                             'HDO': 'http://lagen.nu/org/2008/hogsta-domstolen',
                             'HGO': 'http://lagen.nu/org/2008/gota-hovratt',
                             'HNN': 'http://lagen.nu/org/2008/hovratten-for-nedre-norrland',
                             'HON': 'http://lagen.nu/org/2008/hovratten-for-ovre-norrland',
                             'HSB': 'http://lagen.nu/org/2008/hovratten-over-skane-och-blekinge',
                             'HSV': 'http://lagen.nu/org/2008/svea-hovratt',
                             'HVS': 'http://lagen.nu/org/2008/hovratten-for-vastra-sverige',
                             'MDO': 'http://lagen.nu/org/2008/marknadsdomstolen',
                             'MIG': 'http://lagen.nu/org/2008/migrationsoverdomstolen',
                             'MÖD': 'http://lagen.nu/org/2008/miljooverdomstolen',
                             'REG': 'http://lagen.nu/org/2008/regeringsratten',
                             'KST': 'http://lagen.nu/org/2008/kammarratten-i-stockholm'}

    wrapper = textwrap.TextWrapper(break_long_words=False,
                                   width=72)

    def Parse(self, id, docfile, config=None):
        import codecs
        self.id = id
        self.config = config
        self.lagrum_parser = LegalRef(LegalRef.LAGRUM)
        self.rattsfall_parser = LegalRef(LegalRef.RATTSFALL)

        filetype = "docx" if docfile.endswith("docx") else "doc"

        # Parsing is a two step process: First extract some version of
        # the text from the binary blob (either through running
        # antiword for old-style doc documents, or by unzipping
        # document.xml, for new-style docx documents)
        if filetype == "docx":
            parsablefile = docfile.replace(
                'word', 'ooxml').replace('.docx', '.xml')
            self.word_to_ooxml(docfile, parsablefile)
        else:
            parsablefile = docfile.replace(
                'word', 'docbook').replace('.doc', '.xml')
            try:
                self.word_to_docbook(docfile, parsablefile)
            except util.ExternalCommandError:
                # Some .doc files are .docx with wrong suffix
                parsablefile = docfile.replace(
                    'word', 'ooxml').replace('.doc', '.xml')
                log.info("%s: Retrying as OOXML" % id)
                self.word_to_ooxml(docfile, parsablefile)
                filetype = "docx"

        # FIXME: This is almost identical to the code in
        # SFSManager.Parse - should be refactored somehow
        #
        # Patches produced for the MS Word HTML export will need to be
        # modified for the new antiword docbook output...
        patchfile = 'patches/dv/%s.patch' % id
        descfile = 'patches/dv/%s.desc' % id
        patchdesc = None
        if os.path.exists(patchfile):
            patchedfile = mktemp()
            # we don't want to sweep the fact that we're patching under the carpet
            log.warning('%s: Applying patch %s' % (id, patchfile))
            cmd = 'patch -s %s %s -o %s' % (
                parsablefile, patchfile, patchedfile)
            log.debug('%s: running %s' % (id, cmd))
            (ret, stdout, stderr) = util.runcmd(cmd)
            if ret == 0:  # successful patch
                parsablefile = patchedfile
                assert os.path.exists(
                    descfile), "No description of patch %s found" % patchfile
                patchdesc = codecs.open(
                    descfile, encoding='utf-8').read().strip()

            else:
                # If patching fails, do not continue (patching
                # generally done for privacy reasons -- if it fails
                # and we go on, we could expose sensitive information)
                raise util.ExternalCommandError("%s: Could not apply patch %s: %s" % (id, patchfile, stdout.strip()))
                # log.warning("%s: Could not apply patch %s: %s" % (id, patchfile, stdout.strip()))

        # The second step is to mangle the crappy XML produced by
        # antiword (docbook) or Word 2007 (OOXML) into a nice XHTML2
        # structure.
        if filetype == "docx":
            return self.parse_ooxml(parsablefile, patchdesc)
        else:
            return self.parse_antiword_docbook(parsablefile, patchdesc)

    def _sokord_to_subject(self, sokord):
        return 'http://lagen.nu/concept/%s' % sokord.capitalize().replace(' ', '_')

    def parse_ooxml(self, ooxmlfile, patchdescription=None):
        # FIXME: Change this code bit by bit to handle OOXML instead
        # of docbook (generalizing where possible)
        soup = util.load_soup(ooxmlfile, encoding='utf-8')
        head = Metadata()

        # Högst uppe på varje domslut står domstolsnamnet ("Högsta
        # domstolen") följt av referatnumret ("NJA 1987
        # s. 113").
        firstfield = soup.find("w:t")
        # domstol = Util.elementText(firstfield)
        # Ibland är
        # domstolsnamnet uppsplittat på två w:r-element. Bäst att gå
        # på all text i föräldra-w:tc-cellen
        firstfield = firstfield.findParent("w:tc")
        domstol = ""
        for text_el in firstfield.findAll("w:t"):
            domstol += Util.elementText(text_el)
        # nextfield = firstfield.findParent("w:tc").findNext("w:tc")
        nextfield = firstfield.findNext("w:tc")
        referat = ''
        for e in nextfield.findAll("w:t"):
            referat += e.string
        referat = util.normalize_space(referat)

        # log.info("Domstol: %r, referat: %r" % (domstol,referat))
        #firstfields = soup.findAll("w:t",limit=4)
        #if not re.search('\d{4}', referat):
        #    referat += " " + util.element_text(firstfields[2])
        #
        #tmp = util.element_text(firstfields[3])
        #if tmp.startswith("NJA "):
        #    referat += " (" + tmp + ")"

        # FIXME: Could be generalized
        domstolsuri = self.domstolsforkortningar[self.id.split("/")[0]]
        head['Domstol'] = LinkSubject(domstol,
                                      uri=domstolsuri,
                                      predicate=self.labels['Domstol'])

        head['Referat'] = UnicodeSubject(referat,
                                         predicate=self.labels['Referat'])

        # Hitta övriga enkla metadatafält i sidhuvudet
        for key in list(self.labels.keys()):
            node = soup.find(text=re.compile(key + ':'))
            if node:
                # can't just use the next w:t element, sometimes the
                # text field is broken up (so that "20" is in one
                # cell, "10" in another, and "-06-09" in a third...)
                next_text = node.findNext("w:t")
                text_root = next_text.findParent("w:p")
                txt = ""
                for text_el in text_root.findAll("w:t"):
                    txt += Util.elementText(text_el)

                if txt:  # skippa fält med tomma strängen-värden
                    head[key] = UnicodeSubject(txt, predicate=self.labels[key])
            else:
                # Sometimes these text fields are broken up
                # (eg "<w:t>Avgörand</w:t>...<w:t>a</w:t>...<w:t>tum</w:t>")
                # Use (ridiculous) fallback method
                nodes = soup.findAll('w:statustext', attrs={'w:val': key})
                if nodes:
                    node = nodes[-1]
                    txt = util.element_text(node.findNext("w:t"))
                    if txt:  # skippa fält med tomma strängen-värden
                        # log.info("Fallback %r=%r" % (key,txt))
                        head[key] = UnicodeSubject(
                            txt, predicate=self.labels[key])
                #else:
                #    log.warning("%s: Couldn't find field %r" % (self.id,key))

        # Hitta sammansatta metadata i sidhuvudet
        for key in ["Lagrum", "Rättsfall"]:
            node = soup.find(text=re.compile(key + ':'))
            if node:
                items = []
                textnodes = node.findParent('w:tc').findNextSibling('w:tc')
                if not textnodes:
                    continue
                for textnode in textnodes.findAll('w:t'):
                    items.append(util.element_text(textnode))

                if items and items != ['']:
                    if key == 'Lagrum':
                        containercls = Lagrum
                        parsefunc = self.lagrum_parser.parse
                    elif key == 'Rättsfall':
                        containercls = Rattsfall
                        parsefunc = self.rattsfall_parser.parse

                    head[key] = []
                    for i in items:
                        l = containercls()
                        # Modify the result of parsing for references
                        # and change all Link objects to LinkSubject
                        # objects with an extra RDF predicate
                        # property. Maybe the link class should be
                        # changed to do this instead?
                        for node in parsefunc(i):
                            if isinstance(node, Link):
                                l.append(LinkSubject(str(node),
                                                     uri=str(node.uri),
                                                     predicate=self.multilabels[key]))
                            else:
                                l.append(node)

                        head[key].append(l)

        if not head['Referat']:
            # För specialdomstolarna kan man lista ut referatnumret
            # från målnumret
            if head['Domstol'] == 'Marknadsdomstolen':
                head['Referat'] = 'MD %s' % head[
                    'Domsnummer'].replace('-', ':')
            else:
                raise AssertionError(
                    "Kunde inte hitta referatbeteckningen i %s" % docbookfile)

        # Hitta själva referatstexten... här kan man göra betydligt
        # mer, exv hitta avsnitten för de olika instanserna, hitta
        # dissenternas domskäl, ledamöternas namn, hänvisning till
        # rättsfall och lagrum i löpande text...
        body = Referatstext()
        for p in soup.find(text=re.compile('EFERAT')).findParent('w:tr').findNextSibling('w:tr').findAll('w:p'):
            ptext = ''
            for e in p.findAll("w:t"):
                ptext += e.string
            body.append(Stycke([ptext]))

        # Hitta sammansatta metadata i sidfoten
        txt = util.element_text(
            soup.find(text=re.compile('Sökord:')).findNext('w:t'))
        sokord = []
        for s in self.re_delimSplit(txt):
            s = util.normalize_space(s)
            if not s:
                continue
            # terms longer than 72 chars are not legitimate
            # terms. more likely descriptions. If a term has a - in
            # it, it's probably a separator between a term and a
            # description
            while len(s) >= 72 and " - " in s:
                h, s = s.split(" - ", 1)
                sokord.append(h)
            if len(s) < 72:
                sokord.append(s)

        # Using LinkSubjects (below) is more correct, but we need some
        # way of expressing the relation:
        # <http://lagen.nu/concept/Förhandsbesked> rdfs:label "Förhandsbesked"@sv
        head['Sökord'] = [UnicodeSubject(x,
                                         predicate=self.multilabels['Sökord'])
                          for x in sokord]
        #head['Sökord'] = [LinkSubject(x,
        #                               uri=self._sokord_to_subject(x),
        #                               predicate=self.multilabels['Sökord'])
        #                   for x in sokord]

        if soup.find(text=re.compile('^\s*Litteratur:\s*$')):
            n = soup.find(
                text=re.compile('^\s*Litteratur:\s*$')).findNext('w:t')
            txt = util.element_text(n)
            head['Litteratur'] = [UnicodeSubject(util.normalize_space(x), predicate=self.multilabels['Litteratur'])
                                  for x in txt.split(";")]

        # pprint.pprint(head)
        self.polish_metadata(head)
        if patchdescription:
            head['Textändring'] = UnicodeSubject(patchdescription,
                                                 predicate=RINFOEX['patchdescription'])

        xhtml = self.generate_xhtml(head, body, None, __moduledir__, globals())
        return xhtml

    def parse_antiword_docbook(self, docbookfile, patchdescription=None):
        soup = util.load_soup(docbookfile, encoding='utf-8')
        head = Metadata()
        header_elements = soup.first("para")
        header_text = ''
        for el in header_elements.contents:
            if hasattr(el, 'name') and el.name == "informaltable":
                break
            else:
                header_text += el.string

        # Högst uppe på varje domslut står domstolsnamnet ("Högsta
        # domstolen") följt av referatnumret ("NJA 1987
        # s. 113"). Beroende på worddokumentet ser dock XML-strukturen
        # olika ut. Det vanliga är att informationen finns i en
        # pipeseparerad paragraf:

        parts = [x.strip() for x in header_text.split("|")]
        if len(parts) > 1:
            domstol = parts[0]
            referat = parts[1]
        else:
            # alternativ står de på första raden i en informaltable
            domstol = soup.first(
                "informaltable").tgroup.tbody.row.findAll('entry')[0].string
            referat = soup.first(
                "informaltable").tgroup.tbody.row.findAll('entry')[1].string

        domstolsuri = self.domstolsforkortningar[self.id.split("/")[0]]

        head['Domstol'] = LinkSubject(domstol,
                                      uri=domstolsuri,
                                      predicate=self.labels['Domstol'])

        head['Referat'] = UnicodeSubject(referat,
                                         predicate=self.labels['Referat'])

        # Hitta övriga enkla metadatafält i sidhuvudet
        for key in list(self.labels.keys()):
            node = soup.find(text=re.compile(key + ':'))
            if node:
                txt = util.element_text(
                    node.findParent('entry').findNextSibling('entry'))
                if txt:  # skippa fält med tomma strängen-värden
                    head[key] = UnicodeSubject(txt, predicate=self.labels[key])

        # Hitta sammansatta metadata i sidhuvudet
        for key in ["Lagrum", "Rättsfall"]:
            node = soup.find(text=re.compile(key + ':'))
            if node:
                items = []
                textchunk = node.findParent(
                    'entry').findNextSibling('entry').string
                #for line in [x.strip() for x in self.re_delimSplit(textchunk)]:
                for line in [x.strip() for x in textchunk.split("\n\n")]:
                    if line:
                        items.append(util.normalize_space(line))
                if items and items != ['']:
                    if key == 'Lagrum':
                        containercls = Lagrum
                        parsefunc = self.lagrum_parser.parse
                    elif key == 'Rättsfall':
                        containercls = Rattsfall
                        parsefunc = self.rattsfall_parser.parse

                    head[key] = []
                    for i in items:
                        l = containercls()
                        # Modify the result of parsing for references
                        # and change all Link objects to LinkSubject
                        # objects with an extra RDF predicate
                        # property. Maybe the link class should be
                        # changed to do this instead?
                        for node in parsefunc(i):
                            if isinstance(node, Link):
                                l.append(LinkSubject(str(node),
                                                     uri=str(node.uri),
                                                     predicate=self.multilabels[key]))
                            else:
                                l.append(node)

                        head[key].append(l)

        if not head['Referat']:
            # För specialdomstolarna kan man lista ut referatnumret
            # från målnumret
            if head['Domstol'] == 'Marknadsdomstolen':
                head['Referat'] = 'MD %s' % head[
                    'Domsnummer'].replace('-', ':')
            else:
                raise AssertionError(
                    "Kunde inte hitta referatbeteckningen i %s" % docbookfile)

        # Hitta själva referatstexten... här kan man göra betydligt
        # mer, exv hitta avsnitten för de olika instanserna, hitta
        # dissenternas domskäl, ledamöternas namn, hänvisning till
        # rättsfall och lagrum i löpande text...
        body = Referatstext()
        for p in soup.find(text=re.compile('REFERAT')).findParent('tgroup').findNextSibling('tgroup').find('entry').string.strip().split("\n\n"):
            body.append(Stycke([p]))

        # Hitta sammansatta metadata i sidfoten

        txt = util.element_text(soup.find(text=re.compile(
            'Sökord:')).findParent('entry').nextSibling.nextSibling)
        sokord = []
        for s in self.re_delimSplit(txt):
            s = util.normalize_space(s)
            if not s:
                continue
            # terms longer than 72 chars are not legitimate
            # terms. more likely descriptions. If a term has a - in
            # it, it's probably a separator between a term and a
            # description
            while len(s) >= 72 and " - " in s:
                h, s = s.split(" - ", 1)
                sokord.append(h)
            if len(s) < 72:
                sokord.append(s)

        # Using LinkSubjects (below) is more correct, but we need some
        # way of expressing the relation:
        # <http://lagen.nu/concept/Förhandsbesked> rdfs:label "Förhandsbesked"@sv
        head['Sökord'] = [UnicodeSubject(x,
                                         predicate=self.multilabels['Sökord'])
                          for x in sokord]
        #head['Sökord'] = [LinkSubject(x,
        #                               uri=self._sokord_to_subject(x),
        #                               predicate=self.multilabels['Sökord'])
        #                   for x in sokord]

        if soup.find(text=re.compile('^\s*Litteratur:\s*$')):
            n = soup.find(text=re.compile('^\s*Litteratur:\s*$')
                          ).findParent('entry').nextSibling.nextSibling
            txt = util.element_text(n)
            head['Litteratur'] = [UnicodeSubject(util.normalize_space(x), predicate=self.multilabels['Litteratur'])
                                  for x in txt.split(";")]

        self.polish_metadata(head)
        if patchdescription:
            head['Textändring'] = UnicodeSubject(patchdescription,
                                                 predicate=RINFOEX['patchdescription'])

        xhtml = self.generate_xhtml(head, body, None, __moduledir__, globals())
        return xhtml

    def polish_metadata(self, head):
        # Putsa upp metadatan på olika sätt
        #
        # Lägg till utgivare
        authrec = self.find_authority_rec('Domstolsverket'),
        head['Utgivare'] = LinkSubject('Domstolsverket',
                                       uri=str(authrec[0]),
                                       predicate=DCT['publisher'])

        # I RPUBL-vokabulären motsvaras en referatsbeteckning (exv
        # "NJA 1987 s 187 (NJA 1987:39)") av upp till fyra separata
        # properties
        if 'Referat' in head:
            # print "finding out stuff from %s" % head['Referat']
            txt = str(head['Referat'])
            for (pred, regex) in list({'rattsfallspublikation': r'([^ ]+)',
                                       'publikationsordinal': r'(\d{4}:\d+)',
                                       'arsutgava': r'(\d{4})',
                                       'sidnummer': r's.? ?(\d+)'}.items()):
                m = re.search(regex, txt)
                # print "Trying to match %s with %s" % (regex, txt)
                if m:
                    # print "success"
                    # FIXME: arsutgava should be typed as DateSubject
                    if pred == 'rattsfallspublikation':
                        tmp_publikationsid = m.group(1)
                        # head['[%s]'%pred] = self.publikationsuri[m.group(1)]
                        head['[%s]' % pred] = LinkSubject(m.group(1),
                                                          uri=self.publikationsuri[m.group(1)],
                                                          predicate=RPUBL[pred])
                        if pred == 'publikationsordinal':
                            # This field often has erronous spaces, eg "MOD 2012: 33". Fix it.
                            head['[%s]' % pred] = UnicodeSubject(m.group(
                                1).replace(" ", ""), predicate=RPUBL[pred])
                    else:
                        head['[%s]' % pred] = UnicodeSubject(
                            m.group(1), predicate=RPUBL[pred])
            if not '[publikationsordinal]' in head:  # Workaround för AD-domar
                m = re.search(r'(\d{4}) nr (\d+)', txt)
                if m:
                    head['[publikationsordinal]'] = m.group(
                        1) + ":" + m.group(2)
                else:  # workaround för RegR-domar
                    m = re.search(r'(\d{4}) ref. (\d+)', txt)
                    if m:
                        head['[publikationsordinal]'] = m.group(
                            1) + ":" + m.group(2)

            m = re.search(r'(NJA \d{4} s.? \d+)', head['Referat'])
            if m:
                head['[referatkortform]'] = UnicodeSubject(m.group(1),
                                                           predicate=self.labels['Referat'])
                head['Referat'] = str(head['Referat'])

        # Find out correct URI for this case, preferably by leveraging
        # the URI formatting code in LegalRef
        if 'Referat' in head:
            assert '[rattsfallspublikation]' in head, "missing rpubl:rattsfallspublikation for %s" % head['Referat']
            assert '[publikationsordinal]' in head, "missing rpubl:publikationsordinal for %s" % head['Referat']
        else:
            assert '[rattsfallspublikation]' in head, "missing rpubl:rattsfallspublikation"
            assert '[publikationsordinal]' in head, "missing rpubl:publikationsordinal"

        head['xml:base'] = None
        if 'Referat' in head:
            res = self.rattsfall_parser.parse(head['Referat'])
            if hasattr(res[0], 'uri'):
                head['xml:base'] = res[0].uri

        if not head['xml:base']:
            log.error('%s: Could not find out URI for this doc automatically (%s)' % (self.id, head['Referat']))

        # Putsa till avgörandedatum - det är ett date, inte en string
        # pprint.pprint(head)
        head['Avgörandedatum'] = DateSubject(datetime.strptime(str(head['Avgörandedatum']), '%Y-%m-%d'),
                                             predicate=self.labels['Avgörandedatum'])

        # OK, färdigputsat!
    re_xmlbase = re.compile(
        'xml:base="http://rinfo.lagrummet.se/publ/rattsfall/([^"]+)"').search
    # Converts a NT file to RDF/XML -- needed for uri.xsl to work for legal cases

    def NTriplesToXML(self):
        ntfile = util.relpath(os.path.sep.join(
            [self.baseDir, self.moduleDir, 'parsed', 'rdf.nt']))
        xmlfile = os.path.sep.join(
            [self.baseDir, self.moduleDir, 'parsed', 'rdf.xml'])
        minixmlfile = os.path.sep.join(
            [self.baseDir, self.moduleDir, 'parsed', 'rdf-mini.xml'])
        if self._outfile_is_newer([ntfile], xmlfile) and self._outfile_is_newer([ntfile], minixmlfile):
            log.info("Not regenerating RDF/XML files")
            return
        log.info("Loading NT file %s" % ntfile)
        g = Graph()
        for key, value in list(util.ns.items()):
            g.bind(key, Namespace(value))
        g.parse(ntfile, format="nt")

        log.info("Making a minimal graph")
        mg = Graph()
        for key, value in list(util.ns.items()):
            mg.bind(key, Namespace(value))
        for triple in g:
            if triple[1] == RDF.type:
                mg.add(triple)

        log.info("Serializing the minimal graph")
        f = open(minixmlfile, 'w')
        f.write(mg.serialize(format="pretty-xml"))
        f.close()

        log.info("Serializing to file %s" % xmlfile)
        f = open(xmlfile, 'w')
        f.write(g.serialize(format="pretty-xml"))
        f.close()

    ####################################################################
    # IMPLEMENTATION OF Manager INTERFACE
    ####################################################################
    def Parse(self, basefile, verbose=False):
        """'basefile' here is a alphanumeric string representing the
        filename on disc, which may or may not correspond with any ID
        found in the case itself """

        if verbose:
            print("Setting verbosity")
        log.setLevel(logging.DEBUG)
        start = time()

        if '~$' in basefile:  # word autosave file
            log.debug("%s: Överhoppad", basefile)
            return

        suffix = ".doc"
        infile = os.path.sep.join([self.baseDir, __moduledir__,
                                  'intermediate', 'word', basefile]) + suffix
        if not os.path.exists(infile):
            suffix = ".docx"
            infile = os.path.sep.join([self.baseDir, __moduledir__, 'intermediate', 'word', basefile]) + suffix

        outfile = os.path.sep.join(
            [self.baseDir, __moduledir__, 'parsed', basefile]) + ".xht2"

        # check to see if the outfile is newer than all ingoing files and don't parse if so
        force = (self.config[__moduledir__]['parse_force'] == 'True')
        if not force and self._outfile_is_newer([infile], outfile):
            log.debug("%s: Överhoppad", basefile)
            return

        # print "Force: %s, infile: %s, outfile: %s" % (force,infile,outfile)

        p = self.__parserClass()
        p.verbose = verbose
        parsed = p.Parse(basefile, infile, self.config)
        util.ensure_dir(outfile)

        tmpfile = mktemp()
        out = file(tmpfile, "w")
        out.write(parsed)
        out.close()
        # util.indent_xml_file(tmpfile)
        util.replace_if_different(tmpfile, outfile)
        log.info('%s: OK (%.3f sec, %s)', basefile, time() - start, suffix)

    def ParseAll(self):
        intermediate_dir = os.path.sep.join(
            [self.baseDir, 'dv', 'intermediate', 'word'])
        self._do_for_all(intermediate_dir, '.doc', self.Parse)
        self._do_for_all(intermediate_dir, '.docx', self.Parse)

    def _generateAnnotations(self, annotationfile, basefile, uri):

        sq = """
PREFIX dct:<http://purl.org/dc/terms/>
PREFIX rpub:<http://rinfo.lagrummet.se/ns/2008/11/rinfo/publ#>

SELECT ?uri ?id ?desc
WHERE {
      ?uri dct:description ?desc .
      ?uri dct:identifier ?id .
      ?uri rpubl:rattsfallshanvisning <%s>
}
""" % uri

        rattsfall = self._store_select(sq)

        root_node = PET.Element("rdf:RDF")
        for prefix in util.ns:
            PET._namespace_map[util.ns[prefix]] = prefix
            root_node.set("xmlns:" + prefix, util.ns[prefix])

        main_node = PET.SubElement(root_node, "rdf:Description")
        main_node.set("rdf:about", uri)

        for r in rattsfall:
            subject_node = PET.SubElement(main_node, "dct:subject")
            rattsfall_node = PET.SubElement(subject_node, "rdf:Description")
            rattsfall_node.set("rdf:about", r['uri'])
            id_node = PET.SubElement(rattsfall_node, "dct:identifier")
            id_node.text = r['id']
            desc_node = PET.SubElement(rattsfall_node, "dct:description")
            desc_node.text = r['desc']

        util.indent_et(root_node)
        tree = PET.ElementTree(root_node)
        tmpfile = mktemp()
        tree.write(tmpfile, encoding="utf-8")
        Util.replace_if_different(tmpfile, annotationfile)

    def Generate(self, basefile):
        start = time()
        infile = Util.relpath(self._xmlFileName(basefile))
        outfile = Util.relpath(self._htmlFileName(basefile))
        annotations = "%s/%s/intermediate/annotations/%s.ann.xml" % (
            self.baseDir, self.moduleDir, basefile)

        infile = Util.relpath(self._xmlFileName(basefile))
        # get URI from basefile as fast as possible
        head = codecs.open(infile, encoding='utf-8').read(1024)
        m = self.re_xmlbase(head)
        if m:
            uri = "http://rinfo.lagrummet.se/publ/rattsfall/%s" % m.group(1)
            mapfile = os.path.sep.join(
                [self.baseDir, self.moduleDir, 'generated', 'uri.map.new'])
            Util.ensureDir(mapfile)
            f = codecs.open(mapfile, 'a', encoding='iso-8859-1')
            f.write("%s\t%s\n" % (m.group(1), basefile))
            f.close()
        else:
            log.warning("could not find xml:base in %s" % infile)

        force = (self.config[__moduledir__]['generate_force'] == 'True')

        dependencies = self._load_deps(basefile)

        if not force and self._outfile_is_newer(dependencies, annotations):
            if os.path.exists(self._depsFileName(basefile)):
                log.debug("%s: All %s dependencies untouched in rel to %s" %
                          (basefile, len(dependencies), Util.relpath(annotations)))
            else:
                log.debug("%s: Has no dependencies" % basefile)
        else:
            log.info("%s: Generating annotation file", basefile)
            start = time()
            self._generateAnnotations(annotations, basefile, uri)
            if time() - start > 5:
                log.info("openrdf-sesame is getting slow, reloading")
                cmd = "curl -u %s:%s http://localhost:8080/manager/reload?path=/openrdf-sesame" % (self.config['tomcatuser'], self.config['tomcatpassword'])
                Util.runcmd(cmd)
            else:
                sleep(0.5)  # let sesame catch it's breath

        if not force and self._outfile_is_newer([infile, annotations], outfile):
            log.debug("%s: Överhoppad", basefile)
            return

        util.mkdir(os.path.dirname(outfile))

        # xsltproc silently fails to open files through the document()
        # functions if the filename has non-ascii
        # characters. Therefore, we copy the annnotation file to a
        # separate temp copy first.
        tmpfile = mktemp()
        shutil.copy2(annotations, tmpfile)
        params = {'annotationfile': tmpfile.replace("\\", "/")}
        util.transform("xsl/dv.xsl",
                       infile,
                       outfile,
                       parameters=params,
                       validate=False)
        log.info('%s: OK (%s, %.3f sec)', basefile, outfile, time() - start)

    def GenerateAll(self):
        mapfile = os.path.sep.join(
            [self.baseDir, 'dv', 'generated', 'uri.map'])
        util.robust_remove(mapfile + ".new")

        parsed_dir = os.path.sep.join([self.baseDir, 'dv', 'parsed'])
        self._do_for_all(parsed_dir, '.xht2', self.Generate)
        util.robust_rename(mapfile + ".new", mapfile)

    def GenerateMapAll(self):
        mapfile = os.path.sep.join(
            [self.baseDir, 'dv', 'generated', 'uri.map'])
        Util.robust_remove(mapfile + ".new")

        parsed_dir = os.path.sep.join([self.baseDir, 'dv', 'parsed'])
        self._do_for_all(parsed_dir, '.xht2', self.GenerateMap)
        Util.robustRename(mapfile + ".new", mapfile)

    def GenerateMap(self, basefile):
        start = time()
        infile = Util.relpath(self._xmlFileName(basefile))
        head = codecs.open(infile, encoding='utf-8').read(1024)
        m = self.re_xmlbase(head)
        if m:
            uri = "http://rinfo.lagrummet.se/publ/rattsfall/%s" % m.group(1)
            mapfile = os.path.sep.join(
                [self.baseDir, self.moduleDir, 'generated', 'uri.map.new'])
            Util.ensureDir(mapfile)
            f = codecs.open(mapfile, 'a', encoding='iso-8859-1')
            f.write("%s\t%s\n" % (m.group(1), basefile))
            f.close()
            log.info("%s ok" % basefile)
            return
        else:
            log.warning("could not find xml:base in %s" % infile)

    def ParseGen(self, basefile):
        self.Parse(basefile)
        self.Generate(basefile)

    def DownloadAll(self):
        sd = DVDownloader(self.config)
        sd.DownloadAll()

    def DownloadNew(self):
        sd = DVDownloader(self.config)
        sd.DownloadNew()

    def RelateAll(self):
        super(DVManager, self).RelateAll()
        self.NTriplesToXML()

    ####################################################################
    # OVERRIDES OF Manager METHODS
    ####################################################################

    def _get_module_dir(self):
        return __moduledir__

    publikationer = {'http://rinfo.lagrummet.se/ref/rff/nja': 'Högsta domstolen',
                     'http://rinfo.lagrummet.se/ref/rff/rh': 'Hovrätterna',
                     'http://rinfo.lagrummet.se/ref/rff/rk': 'Kammarrätterna',
                     'http://rinfo.lagrummet.se/ref/rff/ra': 'Regeringsrätten',
                     'http://rinfo.lagrummet.se/ref/rff/hfd': 'Högsta förvaltningsdomstolen',
                     'http://rinfo.lagrummet.se/ref/rff/ad': 'Arbetsdomstolen',
                     'http://rinfo.lagrummet.se/ref/rff/fod': 'Försäkringsöverdomstolen',
                     'http://rinfo.lagrummet.se/ref/rff/md': 'Marknadsdomstolen',
                     'http://rinfo.lagrummet.se/ref/rff/mig': 'Migrationsöverdomstolen',
                     'http://rinfo.lagrummet.se/ref/rff/mod': 'Miljööverdomstolen'
                     }

    def _indexpages_predicates(self):
        return [Util.ns['rpubl'] + 'rattsfallspublikation',
                Util.ns['rpubl'] + 'arsutgava',
                Util.ns['dct'] + 'identifier',
                Util.ns['dct'] + 'description',
                Util.ns['dct'] + 'subject']

    def _build_indexpages(self, by_pred_obj, by_subj_pred):
        documents = defaultdict(lambda: defaultdict(list))
        pagetitles = {}
        pagelabels = {}
        publ_pred = util.ns['rpubl'] + 'rattsfallspublikation'
        year_pred = util.ns['rpubl'] + 'arsutgava'
        id_pred = util.ns['dct'] + 'identifier'
        desc_pred = util.ns['dct'] + 'description'
        subj_pred = util.ns['dct'] + 'subject'
        for obj in by_pred_obj[publ_pred]:
            label = self.publikationer[obj]
            for subject in list(set(by_pred_obj[publ_pred][obj])):
                if not desc_pred in by_subj_pred[subject]:
                    log.warning("No description for %s, skipping" % subject)
                    continue
                if not id_pred in by_subj_pred[subject]:
                    log.warning("No identifier for %s, skipping" % subject)
                    continue
                year = by_subj_pred[subject][year_pred]
                identifier = by_subj_pred[subject][id_pred]
                desc = by_subj_pred[subject][desc_pred]
                if len(desc) > 80:
                    desc = desc[:80].rsplit(' ', 1)[0] + '...'
                pageid = '%s-%s' % (obj.split('/')[-1], year)
                pagetitles[
                    pageid] = 'Rättsfall från %s under %s' % (label, year)
                pagelabels[pageid] = year
                documents[label][pageid].append({'uri': subject,
                                                 'sortkey': identifier,
                                                 'title': identifier,
                                                 'trailer': ' ' + desc[:80]})

        # FIXME: build a fancy three level hierarchy ('Efter sökord' /
        # 'A' / 'Anställningsförhållande' / [list...])

        # build index.html - same as Högsta domstolens verdicts for current year
        outfile = "%s/%s/generated/index/index.html" % (
            self.baseDir, self.moduleDir)
        category = 'Högsta domstolen'
        if 'nja-%d' % (datetime.today().year) in pagetitles:
            pageid = 'nja-%d' % (datetime.today().year)
        else:
            # handles the situation in january, before any verdicts
            # for the new year is available
            pageid = 'nja-%d' % (datetime.today().year - 1)

        title = pagetitles[pageid]
        self._render_indexpage(outfile, title, documents, pagelabels,
                               category, pageid, docsorter=util.numcmp)

        for category in list(documents.keys()):
            for pageid in list(documents[category].keys()):
                outfile = "%s/%s/generated/index/%s.html" % (
                    self.baseDir, self.moduleDir, pageid)
                title = pagetitles[pageid]
                self._render_indexpage(outfile, title, documents, pagelabels, category, pageid, docsorter=util.numcmp)

    def _build_newspages(self, messages):
        basefile = {'de allmänna domstolarna': 'allmanna',
                    'förvaltningsdomstolarna': 'forvaltning',
                    'Arbetsdomstolen': 'ad',
                    'Marknadsdomstolen': 'md',
                    'Migrationsöverdomstolen': 'mig',
                    'Mark- och miljööverdomstolen': 'mod'}

        #entries = defaultdict(list)
        entries = {}
        for base in list(basefile.keys()):
            entries[base] = []
        for (timestamp, message) in messages:
            # f = message.replace('\\','/').replace('intermediate/word','parsed').replace('.doc','.xht2')
            f = message.replace('\\', '/').replace(
                'intermediate/word', 'parsed').replace('.docx', '.xht2')
            if not os.path.exists(f):
                # kan hända om parsandet gick snett
                log.warning("File %s not found" % f)
                continue
            tree, ids = ET.XMLID(open(f).read())
            metadata = tree.find(".//{http://www.w3.org/2002/06/xhtml2/}dl")
            sokord = []

            for e in metadata:
                if 'property' in e.attrib:
                    if e.attrib['property'] == "dct:description":
                        content = '<p>%s</p>' % cgi.escape(e.text)
                    elif e.attrib['property'] == "dct:identifier":
                        title = e.text
                    elif e.attrib['property'] == "rpubl:avgorandedatum":
                        timestamp = datetime.strptime(e.text, "%Y-%m-%d")
                    elif e.attrib['property'] == "dct:subject" and e.text:
                        sokord.append(e.text)
                    elif e.attrib['property'] == "rpubl:rattsfallspublikation":
                        if e.text in ('http://rinfo.lagrummet.se/ref/rff/nja',
                                      'http://rinfo.lagrummet.se/ref/rff/rh'):
                            slot = 'de allmänna domstolarna'
                        elif e.text in ('http://rinfo.lagrummet.se/ref/rff/ra',
                                        'http://rinfo.lagrummet.se/ref/rff/rk'):
                            slot = 'förvaltningsdomstolarna'
                        else:
                            slot = self.publikationer[e.text]
                elif ('rel' in e.attrib):
                    if e.attrib['rel'] == "rpubl:rattsfallspublikation":
                        if e.attrib['href'] in ('http://rinfo.lagrummet.se/ref/rff/nja',
                                                'http://rinfo.lagrummet.se/ref/rff/rh'):
                            slot = 'de allmänna domstolarna'
                        elif e.attrib['href'] in ('http://rinfo.lagrummet.se/ref/rff/ra',
                                                  'http://rinfo.lagrummet.se/ref/rff/rk',
                                                  'http://rinfo.lagrummet.se/ref/rff/hfd'):
                            slot = 'förvaltningsdomstolarna'
                        elif e.attrib['href'] in ('http://rinfo.lagrummet.se/ref/rff/mod'):
                            slot = 'Mark- och miljööverdomstolen'
                        else:
                            slot = None
                    elif e.attrib['rel'] == "dct:creator":
                        domstol = e.text

                if e.text and e.text.startswith('http://rinfo.lagrummet.se/publ/rattsfall'):
                    uri = e.text.replace(
                        'http://rinfo.lagrummet.se/publ/rattsfall', '/dom')

            if not slot:
                slot = domstol

            if sokord:
                title += " (%s)" % ", ".join(sokord)

            entry = {'title': title,
                     'timestamp': timestamp,
                     'id': uri,
                     'uri': uri,
                     'content': '%s<p><a href="%s">Referat i fulltext</a></p>' % (content, uri)}
            entries[slot].append(entry)

        for slot in list(entries.keys()):
            slotentries = sorted(
                entries[slot], key=itemgetter('timestamp'), reverse=True)
            base = basefile[slot]
            htmlfile = util.relpath("%s/%s/generated/news/%s.html" %
                                    (self.baseDir, self.moduleDir, base))
            atomfile = util.relpath("%s/%s/generated/news/%s.atom" %
                                    (self.baseDir, self.moduleDir, base))
            self._render_newspage(htmlfile, atomfile, 'Nya r\xe4ttsfall fr\xe5n %s' % slot, 'De senaste 30 dagarna', slotentries)


    ####################################################################
    # CLASS-SPECIFIC HELPER FUNCTIONS
    ####################################################################

    # none for now...

if __name__ == "__main__":
    import logging.config
    logging.config.fileConfig('etc/log.conf')
    DVManager.__bases__ += (DispatchMixin,)
    mgr = DVManager()
    mgr.Dispatch(sys.argv)
