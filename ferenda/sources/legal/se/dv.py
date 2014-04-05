# -*- coding: utf-8 -*-
from __future__ import unicode_literals
"""Hanterar domslut (detaljer och referat) från Domstolsverket. Data
hämtas fran DV:s (ickepublika) FTP-server, eller fran lagen.nu."""

# system libraries (incl six-based renames)
from datetime import datetime
from ftplib import FTP
from time import mktime
import codecs
import itertools
import os
import re
import zipfile
from six import text_type as str
from six.moves.urllib_parse import urljoin
import tempfile

# 3rdparty libs
import pkg_resources
from rdflib import Namespace, URIRef, Graph, RDF
import requests
import lxml.html
from lxml import etree
from bs4 import BeautifulSoup, NavigableString

# my libs
from ferenda import Document, DocumentStore, Describer, WordReader, FSMParser
from ferenda.decorators import managedparsing, newstate
from ferenda import util
from ferenda.sources.legal.se.legalref import LegalRef, Link
from ferenda.elements import Body, Paragraph, CompoundElement, OrdinalElement, Heading

from ferenda.elements.html import Strong, Em
from . import SwedishLegalSource, SwedishCitationParser, RPUBL
DCT = Namespace(util.ns['dct'])
PROV = Namespace(util.ns['prov'])

# Objektmodellen för rättsfall:
#
# meta:
#  <http://localhost:8000/res/dv/nja/2009/s_695> a rpubl:Rattsfallsreferat;
#     owl:sameAs <http://localhost:8000/res/dv/nja/2009:68>,
#                <http://rinfo.lagrummet.se/publ/rf/nja/2009:68>;
# This should be owl:sameAs <http://rinfo.lagrummet.se/serie/rf/nja>
#     rpubl:rattsfallspublikation <http:///localhost:8000/coll/dv/nja>;
#     rpubl:arsutgava "2009";
#     rpubl:lopnummer "68";
#     rpubl:sidnummer "695";
#     rpubl:referatAvDomstolsavgorande <http://localhost:8000/res/dv/hd/t170-08/2009-11-04>;
#     rpubl:referatrubrik "Överföring av mönsterregistrering..."@sv;
#     dct:identifier "NJA 1987 s 187";
#     dct:bibliographicCitation "NJA 1987:68"
# This shld b owl:sameAs <http://rinfo.lagrummet.se/org/domstolsverket>
#     dct:publisher <http://localhost:8000/org/domstolsverket>;
#     dct:issued "2009-11-05"^^xsd:date.
#
#
#  <http://localhost:8000/res/dv/hd/t170-08/2009-11-04> a VagledandeDomstolsavgorande;
#     owl:sameAs <http://rinfo.lagrummet.se/publ/dom/hd/t_170-08/2009-11-04>;
#     rpubl:avgorandedatum "2009-11-04"^^xsd:date;
#     rpubl:domstolsavdelning "2";
#     rpubl:malnummer "T 170-08";
# rpubl:lagrum <http://localhost:8000/res/sfs/1970:485#P1>;
# rpubl:lagrum <http://localhost:8000/res/sfs/1970:485#P1a>;
# rpubl:lagrum <http://localhost:8000/res/sfs/1970:485#P2>;
# rpubl:lagrum <http://localhost:8000/res/sfs/1970:485#P5>;
# rpubl:lagrum <http://localhost:8000/res/sfs/1970:485#P31>;
# rpubl:lagrum <http://localhost:8000/res/sfs/1970:485#P32>;
#     dct:title "Överföring av mönsterregistrering..."@sv;
# shld be owl:sameAs <http://rinfo.lagrummet.se/org/hoegsta_domstolen>
#     dct:publisher <http://localhost:8000/org/hoegsta_domstolen>;
#     dct:issued "2009-11-05"^^xsd:date;
#     dct:subject <http://localhost:8000/concept/Mönsterrätt>;
#     dct:subject <http://localhost:8000/concept/Dubbelöverlåtelse>;
#     dct:subject <http://localhost:8000/concept/Formgivarrätt>;
#     dct:subject <http://localhost:8000/concept/Godtrosförvärv>;
#     dct:subject <http://localhost:8000/concept/Formgivning>;
# litteratur? dct:references t bnodes...
#
# uri: http://localhost:8000/res/dv/nja/2009/s_695 # hard to construct from "HDO/T170-08", requires a rdf lookup like .value(pred=RDF.type, object=RPUBL.Rattsfallsreferat)
# lang: sv
# body: [Paragraph(), Paragraph(), Paragraph(), ...]


class MaxDownloadsReached(Exception):
    pass


class DVStore(DocumentStore):

    """Customized DocumentStore.
    """

    def basefile_to_pathfrag(self, basefile):
        return basefile

    def pathfrag_to_basefile(self, pathfrag):
        return pathfrag

    def downloaded_path(self, basefile, version=None, attachment=None, suffix=None):
        if not suffix:
            if os.path.exists(self.path(basefile, "downloaded", ".doc")):
                suffix = ".doc"
            elif os.path.exists(self.path(basefile, "downloaded", ".docx")):
                suffix = ".docx"
            else:
                suffix = self.downloaded_suffix
        return self.path(basefile, "downloaded", suffix, version, attachment)

    def intermediate_path(self, basefile, version=None, attachment=None):
        return self.path(basefile, "intermediate", ".xml")

    def list_basefiles_for(self, action, basedir=None):
        if not basedir:
            basedir = self.datadir
        if action == "parse":
            # Note: This pulls everything into memory before first
            # value is yielded. A more nifty variant is at
            # http://code.activestate.com/recipes/491285/
            d = os.path.sep.join((basedir, "downloaded"))
            for x in sorted(itertools.chain(util.list_dirs(d, ".doc"),
                                            util.list_dirs(d, ".docx"))):
                suffix = os.path.splitext(x)[1]
                pathfrag = x[len(d) + 1:-len(suffix)]
                yield self.pathfrag_to_basefile(pathfrag)
        else:
            for x in super(DVStore, self).list_basefiles_for(action, basedir):
                yield x

# (ab)use the CitationClass, with it's useful parse_recursive method,
# to use a legalref based parser instead of a set of pyparsing
# grammars. 

class OrderedParagraph(Paragraph, OrdinalElement):
    def as_html(self, baseuri):
        element = super(Instans, self).as_xhtml(baseuri)
        element.set('id', self.ordinal)

class DomElement(CompoundElement):
    tagname = "div"
    prop = None
    def _get_classname(self):
        return self.__class__.__name__.lower()
    classname = property(_get_classname)

    def as_xhtml(self, baseuri):
        element = super(DomElement, self).as_xhtml(baseuri)
        if self.prop:
            # ie if self.prop = ('ordinal', 'dct:identifier'), then
            # dct:identifier = self.ordinal
            if hasattr(self, self.prop[0]) and getattr(self, self.prop[0]):
                element.set('content', getattr(self, self.prop[0]))
                element.set('property', self.prop[1])
        return element

class Delmal(DomElement):
    prop = ('ordinal', 'dct:identifier')
    
class Instans(DomElement):
    prop = ('court', 'dct:creator')
        
class Dom(DomElement):
    prop = ('malnr', 'dct:identifier')
    
class Domskal(DomElement): pass 
class Domslut(DomElement): pass # dct:author <- names of judges
class Betankande(DomElement): pass # dct:author <- referent
class Skiljaktig(DomElement): pass # dct:author <- name
class Tillagg(DomElement): pass # dct:author <- name
class Endmeta(DomElement): pass
        
class DV(SwedishLegalSource):
    alias = "dv"
    downloaded_suffix = ".zip"
    rdf_type = RPUBL.Rattsfallsreferat
    documentstore_class = DVStore
    namespaces = ('rdf',  # always needed
                  'xsi',  # XML Schema/RDFa validation
                  'dct',  # title, identifier, etc
                  'xsd',  # datatypes
                  'owl',  # : sameAs
                  'prov',
                  ('rpubl', 'http://rinfo.lagrummet.se/ns/2008/11/rinfo/publ#')
                  )
    # This is very similar to SwedishLegalSource.required_predicates,
    # only DCT.title has been changed to RPUBL.referatrubrik (and if
    # our validating function grokked that rpubl:referatrubrik
    # rdfs:isSubpropertyOf dct:title, we wouldn't need this). Also, we
    # removed dct:issued because there is no actual way of getting
    # this data (apart from like the file time stamps).  On further
    # thinking, we remove RPUBL.referatrubrik as it's not present (or
    # required) for rpubl:Rattsfallsnotis
    required_predicates = [RDF.type, DCT.identifier, PROV.wasGeneratedBy]
    
    DCT = Namespace(util.ns['dct'])
    sparql_annotations = "res/sparql/dv-annotations.rq"

    def get_default_options(self):
        opts = super(DV, self).get_default_options()
        opts['ftpuser'] = None
        opts['ftppassword'] = None
        opts['parsebodyrefs'] = True
        return opts

    def canonical_uri(self, basefile):
        # The canonical URI for HDO/B3811-03 should be
        # http://localhost:8000/res/dv/nja/2004s510. We can't know
        # this URI before we parse the document. Once we have, we can
        # find the first rdf:type = rpubl:Rattsfallsreferat (or
        # rpubl:Rattsfallsnotis) and get its url.
        #
        # FIXME: It would be simpler and faster to read
        # DocumentEntry(self.store.entry_path(basefile))['id'], but
        # parse does not yet update the DocumentEntry once it has the
        # canonical uri/id for the document.
        p = self.store.distilled_path(basefile)
        if not os.path.exists(p):
            raise ValueError("No distilled file for basefile %s at %s" % (basefile, p))

        with self.store.open_distilled(basefile) as fp:
            g = Graph().parse(data=fp.read())
        for uri, rdftype in g.subject_objects(predicate=self.ns["rdf"].type):
            if rdftype in (self.ns['rpubl'].Rattsfallsreferat, self.ns['rpubl'].Rattsfallsnotis):
                return str(uri)
        raise ValueError("Can't find canonical URI for basefile %s in %s" % (basefile, p))

    # we override make_document to avoid having it calling
    # canonical_uri prematurely
    def make_document(self, basefile=None):
        doc = Document()
        doc.basefile = basefile
        doc.meta = self.make_graph()
        doc.lang = self.lang 
        doc.body = Body()
        doc.uri = None # can't know this yet
        return doc

    # FIXME: store.list_basefiles_for("parse") must be fixed to handle two
    # different suffixes. Maybe store.downloaded_path() as well, so that
    # it returns .docx if a .docx file indeed exists, and .doc otherwise.
    # But this case (where documents can be in two (or more) formats depending
    # on age isn't uncommon, maybe DocumentStore should support it natively
    # (like with optional suffix parameter to download_path)?

    def download(self):
        # recurse =~ download everything, which we do if force is
        # specified OR if we've never downloaded before
        recurse = False

        if self.config.force or not self.config.lastdownload:
            recurse = True

        self.downloadcount = 0  # number of files extracted from zip files
                               # (not number of zip files)
        try:
            if self.config.ftpuser:
                self.download_ftp("", recurse,
                                  self.config.ftpuser,
                                  self.config.ftppassword)
            else:
                self.log.warning("Config variable ftpuser not set, downloading from secondary source (https://lagen.nu/dv/downloaded/) instead")
                self.download_www("", recurse)
        except MaxDownloadsReached:  # ok we're done!
            pass

    def download_ftp(self, dirname, recurse, user, password, connection=None):
        self.log.debug('Listing contents of %s' % dirname)
        lines = []
        if not connection:
            connection = FTP('ftp.dom.se')
            connection.login(user, password)

        connection.cwd(dirname)
        connection.retrlines('LIST', lines.append)

        for line in lines:
            parts = line.split()
            filename = parts[-1].strip()
            if line.startswith('d') and recurse:
                self.download(filename, recurse)
            elif line.startswith('-'):
                basefile = os.path.splitext(filename)[0]
                if dirname:
                    basefile = dirname + "/" + basefile
                # localpath = self.store.downloaded_path(basefile)
                localpath = self.store.path(basefile, 'downloaded/zips', '.zip')
                if os.path.exists(localpath) and not self.config.force:
                    pass  # we already got this
                else:
                    util.ensure_dir(localpath)
                    self.log.debug('Fetching %s to %s' % (filename,
                                                          localpath))
                    connection.retrbinary('RETR %s' % filename,
                                          # FIXME: retrbinary calls .close()?
                                          open(localpath, 'wb').write)
                    self.process_zipfile(localpath)
        connection.cwd('/')

    def download_www(self, dirname, recurse):
        url = 'https://lagen.nu/dv/downloaded/%s' % dirname
        self.log.debug('Listing contents of %s' % url)
        resp = requests.get(url)
        iterlinks = lxml.html.document_fromstring(resp.text).iterlinks()
        for element, attribute, link, pos in iterlinks:
            if link.startswith("/"):
                continue
            elif link.endswith("/") and recurse:
                self.download_www(link, recurse)
            elif link.endswith(".zip"):
                basefile = os.path.splitext(link)[0]
                if dirname:
                    basefile = dirname + basefile

                # localpath = self.store.downloaded_path(basefile)
                localpath = self.store.path(basefile, 'downloaded/zips', '.zip')
                if os.path.exists(localpath) and not self.config.force:
                    pass  # we already got this
                else:
                    absolute_url = urljoin(url, link)
                    self.log.debug('Fetching %s to %s' % (link, localpath))
                    resp = requests.get(absolute_url)
                    with self.store._open(localpath, "wb") as fp:
                        fp.write(resp.content)
                    self.process_zipfile(localpath)

    # eg. HDO_T3467-96.doc or HDO_T3467-96_1.doc
    re_malnr = re.compile(r'([^_]*)_([^_\.]*)()_?(\d*)(\.docx?)')
    # eg. HDO_T3467-96_BYTUT_2010-03-17.doc or
    #     HDO_T3467-96_BYTUT_2010-03-17_1.doc or
    #     HDO_T254-89_1_BYTUT_2009-04-28.doc (which is sort of the
    #     same as the above but the "_1" goes in a different place)
    re_bytut_malnr = re.compile(
        r'([^_]*)_([^_\.]*)_?(\d*)_BYTUT_\d+-\d+-\d+_?(\d*)(\.docx?)')
    re_tabort_malnr = re.compile(
        r'([^_]*)_([^_\.]*)_?(\d*)_TABORT_\d+-\d+-\d+_?(\d*)(\.docx?)')

    # temporary helper
    def process_all_zipfiles(self):
        self.downloadcount = 0
        zippath = self.store.path('', 'downloaded/zips', '')
        for zipfilename in util.list_dirs(zippath, suffix=".zip"):
            self.log.info("%s: Processing..." % zipfilename)
            self.process_zipfile(zipfilename)

    def process_zipfile(self, zipfilename):
        """Extract a named zipfile into appropriate documents"""
        removed = replaced = created = untouched = 0
        if not hasattr(self, 'downloadcount'):
            self.downloadcount = 0
        try:
            zipf = zipfile.ZipFile(zipfilename, "r")
        except zipfile.BadZipfile as e:
            self.log.error("%s is not a valid zip file: %s" % (zipfilename,e))
            return 
        for bname in zipf.namelist():
            if not isinstance(bname, str):  # py2
                # Files in the zip file are encoded using codepage 437
                name = bname.decode('cp437')
            else:
                name = bname
            if "_notis_" in name:
                base, suffix = os.path.splitext(name)
                segments = base.split("_")
                coll, year = segments[0], segments[1]
                # Extract this doc as a temp file -- we won't be
                # creating an actual permanent file, but let
                # extract_notis extract individual parts of this file
                # to individual basefiles
                fp = tempfile.NamedTemporaryFile("wb", suffix=suffix, delete=False)
                filebytes = zipf.read(bname)
                fp.write(filebytes)
                fp.close()
                tempname = fp.name
                r = self.extract_notis(tempname, year, coll)
                created += r[0]
                untouched += r[1]
                os.unlink(tempname)
            else:
                name = os.path.split(name)[1]
                if 'BYTUT' in name:
                    m = self.re_bytut_malnr.match(name)
                elif 'TABORT' in name:
                    m = self.re_tabort_malnr.match(name)
                else:
                    m = self.re_malnr.match(name)
                if m:
                    (court, malnr, opt_referatnr, referatnr, suffix) = (
                        m.group(1), m.group(2), m.group(3), m.group(4), m.group(5))
                    assert ((suffix == ".doc") or (suffix == ".docx")
                            ), "Unknown suffix %s in %r" % (suffix, name)
                    if referatnr:
                        basefile = "%s/%s_%s" % (court, malnr, referatnr)
                    elif opt_referatnr:
                        basefile = "%s/%s_%s" % (court, malnr, opt_referatnr)
                    else:
                        basefile = "%s/%s" % (court, malnr)

                    outfile = self.store.path(basefile, 'downloaded', suffix)

                    if "TABORT" in name:
                        self.log.info("%s: Removing" % basefile)
                        if not os.path.exists(outfile):
                            self.log.warning("%s: %s doesn't exist" % (basefile,
                                                                       outfile))
                        else:
                            os.unlink(outfile)
                        removed += 1
                    elif "BYTUT" in name:
                        self.log.info("%s: Replacing with new" % basefile)
                        if not os.path.exists(outfile):
                            self.log.warning("%s: %s doesn't exist" %
                                             (basefile, outfile))
                        replaced += 1
                    else:
                        self.log.info("%s: Unpacking" % basefile)
                        if os.path.exists(outfile):
                            untouched += 1
                            continue
                        else:
                            created += 1
                    if not "TABORT" in name:
                        data = zipf.read(bname)
                        with self.store.open(basefile, "downloaded", suffix, "wb") as fp:
                            fp.write(data)

                        # Make the unzipped files have correct timestamp
                        zi = zipf.getinfo(bname)
                        dt = datetime(*zi.date_time)
                        ts = mktime(dt.timetuple())
                        os.utime(outfile, (ts, ts))

                        self.downloadcount += 1
                    if self.config.downloadmax and self.downloadcount >= self.config.downloadmax:
                        raise MaxDownloadsReached()
                else:
                    self.log.warning('Could not interpret filename %r i %s' %
                                     (name, os.path.relpath(zipfilename)))
        self.log.debug('Processed %s, created %s, replaced %s, removed %s, untouched %s files' %
                       (os.path.relpath(zipfilename), created, replaced, removed, untouched))


    def extract_notis(self, docfile, year, coll="HDO"):
        def find_month_in_previous(basefile):
            # The big word file with all notises might not
            # start with a month name -- try to find out
            # current month by examining the previous notis
            # (belonging to a previous word file)

            self.log.warning("No month specified in %s, attempting to look in previous file" % basefile)
            # HDO/2009_not_26 -> HDO/2009_not_25
            tmpfunc = lambda x: str(int(x.group(0)) - 1)
            prev_basefile = re.sub('\d+$', tmpfunc, basefile)
            prev_path = self.store.intermediate_path(prev_basefile)
            avd_p = None
            if os.path.exists(prev_path):
                soup = BeautifulSoup(util.readfile(prev_path))
                tmp = soup.find(["w:p", "para"])
                if re_avdstart.match(tmp.get_text().strip()):
                    avd_p = tmp
            if not avd_p:
                raise RuntimeError("Cannot find value for month in %s (looked in %s" % (basefile, prev_path))
            return avd_p

        # Given a word document containing a set of "notisfall" from
        # either HD or HFD (earlier RegR), spit out a intermediate XML
        # file for each notis.
        if coll == "HDO":
            re_notisstart = re.compile("(?P<day>Den \d+:[ae]. |)(?P<ordinal>\d+)\s*\.\s*\((?P<malnr>\w\s\d+-\d+)\)", flags=re.UNICODE)
            re_avdstart = re.compile("(Januari|Februari|Mars|April|Maj|Juni|Juli|Augusti|September|Oktober|November|December)$")
        else: # REG / HFD
            re_notisstart = re.compile("[\w\: ]*Lnr:(?P<court>\w+) ?(?P<year>\d+) ?not ?(?P<ordinal>\d+)", flags=re.UNICODE)
            re_avdstart = None
        created = untouched = 0
        intermediatefile = os.path.splitext(docfile)[0] + ".xml"
        r = WordReader()
        intermediatefile, filetype = r.read(docfile, intermediatefile)
        if filetype == "docx":
            self._simplify_ooxml(intermediatefile, pretty_print=False)
            soup = BeautifulSoup(util.readfile(intermediatefile))
            soup = self._merge_ooxml(soup)
            p_tag = "w:p"
            xmlns = ' xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml" xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
        else:
            soup = BeautifulSoup(util.readfile(intermediatefile))
            p_tag = "para"
            xmlns = ''
        iterator = soup.find_all(p_tag)
        basefile = None
        fp = None
        avd_p = None
        day = None
        for p in iterator:
            t = p.get_text().strip()
            if re_avdstart:
                # keep track of current month, store that in avd_p
                m = re_avdstart.match(t)
                if m:
                    avd_p = p
                    continue

            m = re_notisstart.match(t)
            if m:
                ordinal = m.group("ordinal")
                try:
                    if m.group("day"):
                        day = m.group("day")
                    else:
                        # inject current day in the first text node of
                        # p (which should inside of a <emphasis
                        # role="bold" or equivalent).
                        subnode = None
                        # FIXME: is this a deprecated method?
                        for c in p.recursiveChildGenerator():
                            if isinstance(c, NavigableString):
                                c.string.replace_with(day + str(c.string))
                                break
                except IndexError:
                    pass

                previous_basefile = basefile
                basefile = "%(coll)s/%(year)s_not_%(ordinal)s" % locals()
                self.log.info("%s: Extracting from %s file" % (basefile, filetype))
                created += 1
                downloaded_path = self.store.path(basefile, 'downloaded', '.'+filetype)
                with self.store._open(downloaded_path, "w"): 
                    pass # just create an empty placeholder file
                if fp:
                    fp.write("</body>\n")
                    fp.close()
                    if filetype == "docx":
                        self._simplify_ooxml(self.store.intermediate_path(previous_basefile))
                util.ensure_dir(self.store.intermediate_path(basefile))
                fp = open(self.store.intermediate_path(basefile), "w")
                fp.write('<body%s>' % xmlns)
                if filetype != "docx":
                    fp.write("\n")
                if coll == "HDO" and not avd_p:
                    avd_p = find_month_in_previous(basefile)
                if avd_p:
                    fp.write(repr(avd_p))
            if fp:
                fp.write(repr(p))
                if filetype != "docx":
                    fp.write("\n")
        if fp: # should always be the case
            fp.write("</body>\n")
            fp.close()
            if filetype == "docx":
                self._simplify_ooxml(self.store.intermediate_path(basefile))
        else:
            self.log.error("%s/%s: No notis were extracted (%s)" %
                           (coll,year,docfile))
        return created, untouched

    re_delimSplit = re.compile("[;,] ?").split
    labels = {'Rubrik': DCT.description,
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
                   # dct:references vore bättre, men sådana ska inte ha literalvärden
                   'Litteratur': DCT['relation'],
                   'Sökord': DCT['subject']
                   }

    # Listan härledd från containers.n3/rattsfallsforteckningar.n3 i
    # rinfoprojektets källkod - en ambitiösare lösning vore att
    # läsa in de faktiska N3-filerna i en rdflib-graf.
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

    # This is information you can get from RDL, but we hardcode it for
    # now.
    slugs = {'arbetsdomstolen': 'ad',
             'domstolsverket': 'dv',
             'göta hovrätt': 'hgo',
             'högsta domstolen': 'hd',
             'högsta förvaltningsdomstolen': 'hfd',
             'hovrätten för nedre norrland': 'hnn',
             'hovrätten för övre norrland': 'hon',
             'hovrätten för västra sverige': 'hvs',
             'hovrätten över skåne och blekinge': 'hsb',
             'justitiekanslern': 'jk',
             'kammarrätten i göteborg': 'kgg',
             'kammarrätten i jönköping': 'kjo',
             'kammarrätten i stockholm': 'kst',
             'kammarrätten i sundsvall': 'ksu',
             'marknadsdomstolen': 'md',
             'migrationsöverdomstolen': 'mig',
             'miljööverdomstolen': 'mod',
             'mark- och miljööverdomstolen': 'mod',
             'patentbesvärsrätten': 'pbr',
             'rättshjälpsnämnden': 'rhn',
             'regeringsrätten': 'regr',
             'statens ansvarsnämnd': 'san',
             'svea hovrätt': 'hsv'}

    @managedparsing
    def parse(self, doc):
        if not hasattr(self, 'lagrum_parser'):
            self.lagrum_parser = LegalRef(LegalRef.LAGRUM)
        if not hasattr(self, 'rattsfall_parser'):
            self.rattsfall_parser = LegalRef(LegalRef.RATTSFALL)
        docfile = self.store.downloaded_path(doc.basefile)

        intermediatefile = self.store.intermediate_path(doc.basefile)
        r = WordReader()
        intermediatefile, filetype = r.read(docfile, intermediatefile)

        if filetype == "docx":
            self._simplify_ooxml(intermediatefile)

        with codecs.open(intermediatefile, encoding="utf-8") as fp:
            patchedtext, patchdesc = self.patch_if_needed(doc.basefile,
                                                          fp.read())
        # The second step is to mangle the crappy XML produced by
        # antiword (docbook) or Word 2007 (OOXML) into a nice pair of
        # structures. rawhead is a simple dict that we'll later transform
        # into a rdflib Graph. rawbody is a list of plaintext strings, each
        # representing a paragraph.
        #
        # long-term FIXME: WordReader should expose a unified
        # interface for handling both kinds of word files so that we
        # wouldn't need both parse_ooxml() and
        # parse_antiword_docbook(). This might require some other tool
        # than antiword for old .doc files, as this throws away a LOT
        # of info.
        if "not" in doc.basefile:
            rawhead, rawbody = self.parse_not(patchedtext, doc.basefile, filetype)
        elif filetype == "docx":
            rawhead, rawbody = self.parse_ooxml(patchedtext, doc.basefile)
        else:
            rawhead, rawbody = self.parse_antiword_docbook(patchedtext, doc.basefile)
        sanitized_head = self.sanitize_metadata(rawhead, doc.basefile)
        doc.uri = self.polish_metadata(sanitized_head, doc)
        if patchdesc:
            doc.meta.add((URIRef(doc.uri),
                          self.ns['ferenda'].patchdescription,
                          patchdesc))
        doc.body = self.format_body(rawbody)
        return True


    def parse_not(self, text, basefile, filetype):
        basefile_regex = re.compile("(?P<type>\w+)/(?P<year>\d+)_not_(?P<ordinal>\d+)")
        referat_templ = {'REG': 'RÅ %(year)s not %(ordinal)s',
                         'HDO': 'NJA %(year)s not %(ordinal)s',
                         'HFD': 'HFD %(year)s not %(ordinal)s'}

        head = {}
        body = []

        m = basefile_regex.match(basefile).groupdict()
        coll = m['type']
        head["Referat"] = referat_templ[coll] % m

        soup = BeautifulSoup(text)
        if filetype == "docx":
            ptag = "w:p"
            soup = self._merge_ooxml(soup)
        else:
            ptag = "para"

        iterator = soup.find_all(ptag)
        if coll == "HDO":
            # keep in sync w extract_notis
            re_notisstart = re.compile("(?:Den (?P<avgdatum>\d+):[ae].\s+|)(?P<ordinal>\d+)\. ?\((?P<malnr>\w \d+-\d+)\)", flags=re.UNICODE)
            re_avgdatum = re_malnr = re_notisstart
            re_lagrum = re_sokord = None
            # headers consist of the first two chunks (month, then
            # date+ordinal+malnr)
            header = iterator.pop(0), iterator[0] # need to re-read
                                                  # the second chunk
                                                  # later
            curryear = m['year']
            currmonth = self.swedish_months[header[0].get_text().strip().lower()]
        else: # "REG", "HFD"
            # keep in sync like above
            re_notisstart = re.compile("[\w\: ]*Lnr:(?P<court>\w+) ?(?P<year>\d+) ?not ?(?P<ordinal>\d+)")
            re_malnr = re.compile(r"D:(?P<malnr>\d+\-\d+)")
            re_avgdatum = re.compile(r"[AD]:(?P<avgdatum>\d+\-\d+\-\d+)")
            re_sokord = re.compile("Uppslagsord: (?P<sokord>.*)", flags=re.DOTALL)
            re_lagrum = re.compile("Lagrum: ?(?P<lagrum>.*)", flags=re.DOTALL)
            # headers consists of the first five or six
            # chunks. Doesn't end until "^Not \d+."
            header = []
            done = False
            while not done:
                if re.match("Not \d+\. ", iterator[0].get_text().strip()):
                    done = True
                else:
                    tmp = iterator.pop(0)
                    if tmp.get_text().strip():
                        # REG specialcase 
                        if header and header[-1].get_text() == "Lagrum:":
                            header[-1].append(list(tmp.children)[0])
                        else:
                            header.append(tmp)
            
        if coll == "HDO":
            head['Domstol'] = "Högsta domstolen"
        elif coll == "HFD":
            head['Domstol'] = "Högsta förvaltningsdomstolen"
        elif coll == "REG":
            head['Domstol'] = "Regeringsrätten"
        else:
            raise ValueError("Unsupported: %s" % coll)
        for node in header:
            t = node.get_text()
            # if not malnr, avgdatum found, look for those
            for fld, key, rex in (('Målnummer', 'malnr', re_malnr),
                                  ('Avgörandedatum', 'avgdatum', re_avgdatum),
                                  ('Lagrum', 'lagrum', re_lagrum),
                                  ('Sökord', 'sokord', re_sokord)):
                if not rex: continue
                m = rex.search(t)
                if m and m.group(key):
                    if fld in ('Lagrum'): # Sökord is split by sanitize_metadata
                        head[fld] = self.re_delimSplit(m.group(key))
                    else:
                        head[fld] = m.group(key)

        if coll == "HDO" and 'Avgörandedatum' in head:
            head['Avgörandedatum'] = "%s-%02d-%02d" % (curryear, currmonth, int(head['Avgörandedatum']))

        # Do a basic conversion of the rest (bodytext) to Element objects
        #
        # This is generic enough that it could be part of WordReader
        for node in iterator:
            line = []
            if filetype == "doc":
                subiterator = node
            elif filetype == "docx":
                subiterator = node.find_all("w:r")
            for part in subiterator:
                if part.name:  
                    t = part.get_text()
                else:
                    t = str(part)  # convert NavigableString to pure string
                # if not t.strip():
                #     continue
                if filetype == "doc" and part.name == "emphasis": # docbook
                    if part.get("role") == "bold":
                        if line and isinstance(line[-1], Strong):
                            line[-1][-1] += t
                        else:
                            line.append(Strong([t]))
                    else:
                        if line and isinstance(line[-1], Em):
                            line[-1][-1] += t
                        else:
                            line.append(Em([t]))
                elif filetype == "docx" and part.find("w:rpr") and part.find("w:rpr").find(["w:b", "w:i"]): # ooxml
                    if part.rpr.b:
                        if line and isinstance(line[-1], Strong):
                            line[-1][-1] += t
                        else:
                            line.append(Strong([t]))
                    elif part.rpr.i:
                        if line and isinstance(line[-1], Em):
                            line[-1][-1] += t
                        else:
                            line.append(Em([t]))
                else:
                    if line and isinstance(line[-1], str):
                        line[-1] += t
                    else:
                        line.append(t)
            if line:
                body.append(line)
        return head, body
        
    def parse_ooxml(self, text, basefile):
        soup = BeautifulSoup(text)
        soup = self._merge_ooxml(soup)

        head = {}
        
        # Högst uppe på varje domslut står domstolsnamnet ("Högsta
        # domstolen") följt av referatnumret ("NJA 1987
        # s. 113").
        firstfield = soup.find("w:t")
        # Ibland ärdomstolsnamnet uppsplittat på två
        # w:r-element. Bäst att gå på all text i
        # föräldra-w:tc-cellen
        firstfield = firstfield.find_parent("w:tc")
        head['Domstol'] = firstfield.get_text(strip=True)

        nextfield = firstfield.find_next("w:tc")
        head['Referat'] = nextfield.get_text(strip=True)
        # Hitta övriga enkla metadatafält i sidhuvudet
        for key in self.labels:
            if key in head:
                continue
            node = soup.find(text=re.compile(key + ':'))
            if not node:
                # Sometimes these text fields are broken up
                # (eg "<w:t>Avgörand</w:t>...<w:t>a</w:t>...<w:t>tum</w:t>")
                # Use (ridiculous) fallback method
                if key not in ('Diarienummer', 'Domsnummer', 'Avdelning'): # not always present
                    self.log.warning("%s: Couldn't find field %r" % (basefile, key))
                continue

            txt = node.find_next("w:t").find_parent("w:p").get_text(strip=True)
            if txt:  # skippa fält med tomma strängen-värden
                head[key] = txt

        # Hitta sammansatta metadata i sidhuvudet
        for key in ["Lagrum", "Rättsfall"]:
            node = soup.find(text=re.compile(key + ':'))
            if node:
                textnodes = node.find_parent('w:tc').find_next_sibling('w:tc')
                if not textnodes:
                    continue
                items = []
                for textnode in textnodes.find_all('w:t'):
                    t = textnode.get_text(strip=True)
                    if t:
                        items.append(t)
                if items:
                    head[key] = items

        # The main text body of the verdict
        body = []
        for p in soup.find(text=re.compile('EFERAT')).find_parent('w:tr').find_next_sibling('w:tr').find_all('w:p'):
            ptext = ''
            for e in p.find_all("w:t"):
                ptext += e.string
            body.append(ptext)

        # Finally, some more metadata in the footer
        if soup.find(text=re.compile(r'Sökord:')):
            head['Sökord'] = soup.find(
                text=re.compile(r'Sökord:')).find_next('w:t').get_text(strip=True)

        if soup.find(text=re.compile('^\s*Litteratur:\s*$')):
            n = soup.find(text=re.compile('^\s*Litteratur:\s*$'))
            head['Litteratur'] = n.findNext('w:t').get_text(strip=True)
        return head, body

    def parse_antiword_docbook(self, text, basefile):
        soup = BeautifulSoup(text)
        head = {}
        header_elements = soup.find("para")
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
            head['Domstol'] = parts[0]
            head['Referat'] = parts[1]
        else:
            # alternativ står de på första raden i en informaltable
            row = soup.find("informaltable").tgroup.tbody.row.find_all('entry')
            head['Domstol'] = row[0].get_text(strip=True)
            head['Referat'] = row[1].get_text(strip=True)

        # Hitta övriga enkla metadatafält i sidhuvudet
        for key in self.labels:
            node = soup.find(text=re.compile(key + ':'))
            if node:
                txt = node.find_parent('entry').find_next_sibling('entry').get_text(strip=True)
                if txt:
                    head[key] = txt

        # Hitta sammansatta metadata i sidhuvudet
        for key in ["Lagrum", "Rättsfall"]:
            node = soup.find(text=re.compile(key + ':'))
            if node:
                head[key] = []
                textchunk = node.find_parent(
                    'entry').find_next_sibling('entry').string
                for line in [util.normalize_space(x) for x in textchunk.split("\n\n")]:
                    if line:
                        head[key].append(line)

        body = []
        for p in soup.find(text=re.compile('REFERAT')).find_parent('tgroup').find_next_sibling('tgroup').find('entry').get_text(strip=True).split("\n\n"):
            body.append(p)

        # Hitta sammansatta metadata i sidfoten
        head['Sökord'] = soup.find(text=re.compile('Sökord:')).find_parent(
            'entry').next_sibling.next_sibling.get_text(strip=True)

        if soup.find(text=re.compile('^\s*Litteratur:\s*$')):
            n = soup.find(text=re.compile('^\s*Litteratur:\s*$')).find_parent(
                'entry').next_sibling.next_sibling.get_text(strip=True)
            head['Litteratur'] = n
        return head, body

    # correct broken/missing metadata
    def sanitize_metadata(self, head, basefile):
        basefile_regex = re.compile('(?P<type>\w+)/(?P<year>\d+)-(?P<ordinal>\d+)')
        nja_regex = re.compile("NJA ?(\d+) ?s\.? ?(\d+) ?\( ?(?:NJA|) ?[ :]?(\d+) ?: ?(\d+)")
        date_regex = re.compile("(\d+)[^\d]+(\d+)[^\d]+(\d+)")
        referat_regex = re.compile("(?P<type>[A-ZÅÄÖ]+)[^\d]*(?P<year>\d+)[^\d]+(?P<ordinal>\d+)")
        referat_templ = {'ADO': 'AD %(year)s nr %(ordinal)s',
                         'AD': '%(type)s %(year)s nr %(ordinal)s',
                         'MDO': 'MD %(year)s:%(ordinal)s',
                         'NJA': '%(type)s %(year)s s. %(ordinal)s',
                         None: '%(type)s %(year)s:%(ordinal)s'
        }

        # 1. Attempt to fix missing Referat
        if not head.get("Referat"):
            # For some courts (MDO, ADO) it's possible to reconstruct a missing Referat from the basefile
            m = basefile_regex.match(basefile)
            if m and m.group("type") in ('ADO', 'MDO'):
                head["Referat"] = referat_templ[m.group("type")] % (m.groupdict())

        # 2. Correct known problems with Domstol not always being correctly specified
        if "Hovrättenför" in head["Domstol"] or "Hovrättenöver" in head["Domstol"]:
            head["Domstol"] = head["Domstol"].replace("Hovrätten", "Hovrätten ")
        try:
            # if this throws a KeyError, it's not canonically specified
            self.lookup_resource(head["Domstol"], cutoff=1)
        except KeyError:
            # lookup URI with fuzzy matching, then turn back to canonical label
            head["Domstol"] = self.lookup_label(str(self.lookup_resource(head["Domstol"])))

        # 3. Convert head['Målnummer'] to a list. Occasionally more than one
        # Malnummer is provided (c.f. AD 1994 nr 107, AD
        # 2005-117, AD 2003-111) in a comma, semicolo or space
        # separated list. AD 2006-105 even separates with " och ".
        #
        # HOWEVER, "Ö 2475-12" must not be converted to ["Ö2475-12"], not ['Ö', '2475-12']
        if head.get("Målnummer"):
            if head["Målnummer"][:2] in ('Ö ', 'B ', 'T '):
                head["Målnummer"] = [head["Målnummer"].replace(" ", "")]
            else:
                res = []
                for v in re.split("och|,|;|\s", head['Målnummer']):
                    if v.strip():
                        res.append(v.strip())
                head['Målnummer'] = res
        
        # 4. Create a general term for Målnummer or Domsnummer to act
        # as a local identifier
        if head.get("Målnummer"):
            head["_localid"] = head["Målnummer"]
        elif head.get("Domsnummer"):
            head["_localid"] = head["Domsnummer"]
        else:
            raise ValueError("Required key (Målnummer/Domsnummer) missing")

        # 5. For NJA, Canonicalize the identifier through a very
        # forgiving regex and split of the alternative identifier
        # as head['_nja_ordinal']
        #
        # "NJA 2008 s 567 (NJA 2008:86)"=>("NJA 2008 s 567", "NJA 2008:86")
        # "NJA 2011 s. 638(NJA2011:57)" => ("NJA 2011 s 638", "NJA 2001:57")
        # "NJA 2012 s. 16(2012:2)" => ("NJA 2012 s 16", "NJA 2012:2")
        if "NJA" in head["Referat"] and " not " not in head["Referat"]:
            m = nja_regex.match(head["Referat"])
            if m:
                head["Referat"] = "NJA %s s %s" % (m.group(1), m.group(2))
                head["_nja_ordinal"] = "NJA %s:%s" % (m.group(3), m.group(4))
            else:
                raise ValueError("Unparseable NJA ref '%s'" % head["Referat"])

        # 6 Canonicalize referats: Fix crap like "AD 2010nr 67",
        # "AD2011 nr 17", "HFD_2012 ref.58", "RH 2012_121", "RH2010
        # :180", "MD:2012:5", "MIG2011:14", "-MÖD 2010:32" and many
        # MANY more
        if " not " not in head["Referat"]: # notiser always have OK Referat
            m = referat_regex.search(head["Referat"])
            if m:
                if m.group("type") in referat_templ:
                    head["Referat"] = referat_templ[m.group("type")] % m.groupdict()
                else:
                    head["Referat"] = referat_templ[None] % m.groupdict()
            else:
                raise ValueError("Unparseable ref '%s'" % head["Referat"])

        # 7. Convert Sökord string to an actual list 
        res = []
        if head.get("Sökord"):
            for s in self.re_delimSplit(head["Sökord"]):
                s = util.normalize_space(s)
                if not s:
                    continue
                # terms longer than 72 chars are not legitimate
                # terms. more likely descriptions. If a term has a - in
                # it, it's probably a separator between a term and a
                # description
                while len(s) >= 72 and " - " in s:
                    h, s = s.split(" - ", 1)
                    res.append(h)
                if len(s) < 72:
                    res.append(s)
            head["Sökord"] = res

        # 8. Convert Avgörandedatum to a sensible value in the face of
        # irregularities like '2010-11 30', '2011 03-23' '2011-
        # 01-27', '2009.08.28' or '07-12-28'
        m = date_regex.match(head["Avgörandedatum"])
        if m:
            if len(m.group(1)) < 4:
                if int(m.group(1) <= 80): # '80-01-01' => '1980-01-01',
                    year = '19' + m.group(1) 
                else:                     # '79-01-01' => '2079-01-01',
                    year = '20' + m.group(1) 
            else:
                year = m.group(1)
            head["Avgörandedatum"] = "%s-%s-%s" % (year, m.group(2), m.group(3))
        else:
            raise ValueError("Unparseable date %s" % head["Avgörandedatum"])

        # 9. Done!
        return head

    # create nice RDF from the sanitized metadata
    def polish_metadata(self, head, doc):

        def ref_to_uri(ref):
            # FIXME: We'd like to retire legalref and replace it with
            # pyparsing grammars.
            nodes = self.rattsfall_parser.parse(ref)
            assert isinstance(nodes[0], Link), "Couldn't make URI from ref %s" % ref
            uri = nodes[0].uri
            return localize_uri(uri)

        def dom_to_uri(domstol, malnr, avg):
            baseuri = self.config.url
            slug = self.slugs[domstol.lower()]
            # FIXME: We should create multiple urls if we have multiple malnummers?
            first_malnr = malnr[0]
            return "%(baseuri)sres/dv/%(slug)s/%(first_malnr)s/%(avg)s" % locals()

        def localize_uri(uri):
            if "publ/rattsfall" in uri:
                return uri.replace("http://rinfo.lagrummet.se/publ/rattsfall",
                                   self.config.url + "res/dv")
            elif "publ/sfs/" in uri:
                return uri.replace("http://rinfo.lagrummet.se/publ/sfs",
                                   self.config.url + "res/sfs")

        def split_nja(value):
            return [x[:-1] for x in value.split("(")]

        def sokord_uri(value):
            return self.config.url + "concept/%s" % util.ucfirst(value).replace(' ', '_')

        # 1. mint uris and create the two Describers we'll use
        refuri = ref_to_uri(head["Referat"])

            
        refdesc = Describer(doc.meta, refuri)
        
        domuri = dom_to_uri(head["Domstol"],
                            head["_localid"],
                            head["Avgörandedatum"])
        domdesc = Describer(doc.meta, domuri)

        # 2. convert all strings in head to proper RDF
        #
        # 
        for label, value in head.items():
            if label == "Rubrik":
                value = util.normalize_space(value)
                refdesc.value(self.ns['rpubl'].referatrubrik, value, lang="sv")
                domdesc.value(self.ns['dct'].title, value, lang="sv")

            elif label == "Domstol":
                domdesc.rel(self.ns['dct'].publisher, self.lookup_resource(value))
            elif label == "Målnummer":
                for v in value:
                    # FIXME: In these cases (multiple målnummer, which
                    # primarily occurs with AD), we should really
                    # create separate domdesc objects (there are two
                    # verdicts, just summarized in one document)
                    domdesc.value(self.ns['rpubl'].malnummer, v)
            elif label == "Domsnummer":
                domdesc.value(self.ns['rpubl'].domsnummer, value)
            elif label == "Diarienummer":
                domdesc.value(self.ns['rpubl'].diarienummer, value)
            elif label == "Avdelning":
                domdesc.value(self.ns['rpubl'].avdelning, value)
            elif label == "Referat":
                for pred, regex in {'rattsfallspublikation': r'([^ ]+)',
                                    'arsutgava': r'(\d{4})',
                                    'lopnummer': r'\d{4}(?:\:| nr | not )(\d+)',
                                    'sidnummer': r's.? ?(\d+)'}.items():
                    m = re.search(regex, value)
                    if m:
                        if pred == 'rattsfallspublikation':
                            # "NJA" -> "http://localhost:8000/coll/dv/nja"
                            # "RÅ" -> "http://localhost:8000/coll/dv/rå" <-- FIXME, should be .../dv/ra
                            uri = self.config.url + "coll/dv/" + m.group(1).lower()
                            refdesc.rel(self.ns['rpubl'][pred], uri)
                        else:
                            refdesc.value(self.ns['rpubl'][pred], m.group(1))
                refdesc.value(self.ns['dct'].identifier, value)

            elif label == "_nja_ordinal":
                refdesc.value(self.ns['dct'].bibliographicCitation,
                              value)
                m = re.search(r'\d{4}(?:\:| nr | not )(\d+)', value)
                if m:
                    refdesc.value(self.ns['rpubl'].lopnummer, m.group(1))

                refdesc.rel(self.ns['owl'].sameAs,
                            self.config.url + "res/dv/nja/" + value.split(" ")[1])

            elif label == "Avgörandedatum":
                domdesc.value(self.ns['rpubl'].avgorandedatum, self.parse_iso_date(value))

            elif label == "Lagrum":
                for i in value:  # better be list not string
                    for node in self.lagrum_parser.parse(i):
                        if isinstance(node, Link):

                            domdesc.rel(self.ns['rpubl'].lagrum,
                                        localize_uri(node.uri))
            elif label == "Rättsfall":
                for i in value:
                    for node in self.rattsfall_parser.parse(i):
                        if isinstance(node, Link):
                            domdesc.rel(self.ns['rpubl'].rattsfall,
                                        localize_uri(node.uri))
            elif label == "Litteratur":
                for i in value.split(";"):
                    domdesc.value(self.ns['dct'].relation, util.normalize_space(i))
            elif label == "Sökord":
                for s in value:
                    domdesc.rel(self.ns['dct'].subject, sokord_uri(s))

        # 3. mint some owl:sameAs URIs
        refdesc.rel(self.ns['owl'].sameAs, self.sameas_uri(refuri))
        domdesc.rel(self.ns['owl'].sameAs, self.sameas_uri(domuri))

        # 4. Add some same-for-everyone properties
        refdesc.rel(self.ns['dct'].publisher, self.lookup_resource('Domstolsverket'))
        if 'not' in head['Referat']:
            refdesc.rdftype(self.ns['rpubl'].Rattsfallsnotis)
        else:
            refdesc.rdftype(self.ns['rpubl'].Rattsfallsreferat)
        domdesc.rdftype(self.ns['rpubl'].VagledandeDomstolsavgorande)
        refdesc.rel(self.ns['rpubl'].referatAvDomstolsavgorande, domuri)
        refdesc.value(self.ns['prov'].wasGeneratedBy, self.qualified_class_name())
# FIXME: implement this
#        # 5. Create a meaningful identifier for the verdict itself (ie. "Göta hovrätts dom den 42 september 2340 i mål B 1234-42")
#        domdesc.value(self.ns['dct'].identifier, dom_to_identifier(head["Domstol"],
#                                                                   head["_localid"],
#                                                                   head["Avgörandedatum"])
#
        
        # 6. assert that we have everything we need
        
        # 7. done!
        return refuri


    def format_body(self, paras):
        b = Body()
        # paras is typically a list of strings, but can be a list of
        # lists, where the innermost list consists of strings
        # interspersed with Element objects.
        for x in paras:
            if isinstance(x, str):
                x = [x]
            b.append(Paragraph(x))

        
        # find and link references -- this increases processing time
        # 5-10x, so do it only if requested. For some types (NJA
        # notiser) we could consider finding references anyway, as
        # these documents do not have headnotes with fixed fields for
        # references to statutory law and caselaw -- if we don't parse
        # body, we don't have nothing.
        if self.config.parsebodyrefs:
            if not hasattr(self, 'ref_parser'):
                self.ref_parser = LegalRef(LegalRef.RATTSFALL, LegalRef.LAGRUM, LegalRef.FORARBETEN)
            citparser = SwedishCitationParser(self.ref_parser)
            b = citparser.parse_recursive(b)

        # convert the unstructured list of Paragraphs to a
        # hierarchical tree of instances, domslut, domskäl, etc
        b = self.structure_body(b)
        return b

    def structure_body(self, paras):
        courtnames = ["Linköpings tingsrätt",
                      "Lunds tingsrätt",
                      "Umeå tingsrätt",
                      "Stockholms tingsrätt", 

                      "Göta hovrätt",
                      "Hovrätten över Skåne och Blekinge",
                      "Hovrätten för Övre Norrland",
                      "Svea hovrätt",

                      "Högsta domstolen"]

        rx = ('(?P<court>Försäkringskassan|Migrationsverket) beslutade (därefter|) den (?P<date>\d+ \w+ \d+) att',
              '(A överklagade beslutet till |)(?P<court>(Förvaltningsrätten|Länsrätten|Kammarrätten) i \w+(| län)(|, migrationsdomstolen|, Migrationsöverdomstolen)|Högsta förvaltningsdomstolen) \((?P<date>\d+-\d+\d+)')
        instans_matchers = [re.compile(x, re.UNICODE) for x in rx]
            
        
        def is_delmal(parser):
            chunk = parser.reader.peek()
            return str(chunk) in ("I", "II", "III")

        def is_instans(parser):
            chunk = parser.reader.peek()
            strchunk = str(chunk)
            if strchunk in courtnames:
                return True
            # elif re.search('Migrationsverket överklagade migrationsdomstolens beslut', strchunk):
            #     return True
            elif parser._state_stack == ['body']:
                # if we're at root level, *anything* starts a new instans
                return True
            else:
                for sentence in split_sentences(strchunk):
                    for r in (instans_matchers):
                        if r.match(sentence):
                            return True
            return False

        def is_heading(parser):
            chunk = parser.reader.peek()
            strchunk = str(chunk)
            # a heading is reasonably short and does not end with a
            # period (or other sentence ending typography)
            return len(strchunk) < 140 and not (strchunk.endswith(".") or
                                                strchunk.endswith(":") or
                                                strchunk.startswith("”"))
                                            

        def is_betankande(parser):
            strchunk = str(parser.reader.peek())
            return strchunk == "Målet avgjordes efter föredragning."
            
        def is_dom(parser):
            res = is_domskal(parser)
            return res

        def is_domskal(parser):
            strchunk = str(parser.reader.peek())
            if strchunk == "Skäl":
                return True
            if re.match("(Tingsrätten|Hovrätten|HD|Högsta förvaltningsdomstolen) \([^)]*\) (meddelade|anförde|fastställde|yttrade)", strchunk):
                return True

        def is_domslut(parser):
            strchunk = str(parser.reader.peek())
            return strchunk in ("Domslut", "Hovrättens avgörande", "HD:s avgörande", "Högsta förvaltningsdomstolens avgörande")
            
        def is_skiljaktig(parser):
            strchunk = str(parser.reader.peek())
            return re.match("(Justitie|Kammarrätts)råde[nt] ([^\.]*) var (skiljaktig|av skiljaktig mening)", strchunk)

        def is_tillagg(parser):
            strchunk = str(parser.reader.peek())
            return re.match("Justitieråde[nt] ([^\.]*) (tillade för egen del|gjorde för egen del ett tillägg)", strchunk)

        def is_endmeta(parser):
            strchunk = str(parser.reader.peek())
            return re.match("HD:s (beslut|dom) meddela(de|d|t): den", strchunk)

        def is_paragraph(parser):
            return True

        # FIXME: This and make_paragraph ought to be expressed as
        # generic functions in the ferenda.fsmparser module
        @newstate('body')
        def make_body(parser):
            return parser.make_children(Body())

        @newstate('delmal')
        def make_delmal(parser):
            d = Delmal(ordinal=str(parser.reader.next()), malnr=None)
            return parser.make_children(d)

        @newstate('instans')
        def make_instans(parser):
            chunk = parser.reader.next()
            strchunk = str(chunk)
            if strchunk in courtnames:
                i = Instans(court=strchunk)
            else:
                i = False
                for sentence in split_sentences(strchunk):
                    for r in (instans_matchers):
                        m = r.match(sentence)
                        if m:
                            if 'court' in m.groupdict():
                                i = Instans([chunk], court=m.groupdict()['court'])
                            break
                    if i:
                        break
                if not i:
                    i = Instans([chunk])
                    
            return parser.make_children(i)

        def split_sentences(text):
            text = util.normalize_space(text)
            text += " "
            return text.split(". ")
                

        def make_heading(parser):
            # a heading is by definition a single line
            return Heading(parser.reader.next())

        @newstate('betankande')
        def make_betankande(parser):
            b = Betankande()
            b.append(parser.reader.next())
            return parser.make_children(b)

        @newstate('dom')
        def make_dom(parser):
            d = Dom(avgorandedatum=None, malnr=None)
            return parser.make_children(d)

        @newstate('domskal')
        def make_domskal(parser):
            d = Domskal()
            return parser.make_children(d)

        @newstate('domslut')
        def make_domslut(parser):
            d = Domslut()
            return parser.make_children(d)

        @newstate('skiljaktig')
        def make_skiljaktig(parser):
            s = Skiljaktig()
            s.append(parser.reader.next())
            return parser.make_children(s)

        @newstate('tillagg')
        def make_tillagg(parser):
            t = Tillagg()
            t.append(parser.reader.next())
            return parser.make_children(t)

        @newstate('endmeta')
        def make_endmeta(parser):
            m = Endmeta()
            m.append(parser.reader.next())
            return parser.make_children(m)
            
        
            
        def make_paragraph(parser):
            chunk = parser.reader.next()
            strchunk = str(chunk)
            if ordered(strchunk):
                # FIXME: Cut the ordinal from chunk somehow
                if isinstance(chunk, Paragraph):
                    p = OrderedParagraph(list(chunk), ordinal=ordered(strchunk))
                else:
                    p = OrderedParagraph([chunk], ordinal=ordered(strchunk))
            else:
                if isinstance(chunk, Paragraph):
                    p = chunk
                else: 
                    p = Paragraph([chunk])
            return p

        def ordered(chunk):
            if re.match("(\d+).", chunk):
                return chunk.split(".", 1)

        def transition_domskal(symbol, statestack):
            if 'betankande' in statestack:
                # Ugly hack: mangle the statestack so that *next time*
                # we encounter a is_domskal, we pop the statestack,
                # but for now we push to it.
                statestack[statestack.index('betankande')] = "__done__"
                return make_domskal, "domskal"
            else:
                # here's where we pop the stack
                return False, None
                
        p = FSMParser()
        p.set_recognizers(is_delmal,
                          is_endmeta,
                          is_instans,
                          is_dom,
                          is_betankande,
                          is_domskal,
                          is_domslut,
                          is_skiljaktig,
                          is_tillagg,
                          is_heading,
                          is_paragraph)
        commonstates = ("body", "delmal", "instans", "domskal", "domslut", "betankande", "skiljaktig", "tillagg")
        
        p.set_transitions({
            ("body", is_delmal): (make_delmal, "delmal"),
            ("body", is_instans): (make_instans, "instans"),
            ("body", is_endmeta): (make_endmeta, "endmeta"),
            ("delmal", is_instans): (make_instans, "instans"),
            ("delmal", is_delmal): (False, None),
            ("delmal", is_endmeta): (False, None),
            ("instans", is_betankande): (make_betankande, "betankande"),
            ("instans", is_dom): (make_dom, "dom"),
            ("instans", is_domslut): (make_domslut, "domslut"),
            ("instans", is_instans): (False, None),
            ("instans", is_skiljaktig): (make_skiljaktig, "skiljaktig"),
            ("instans", is_tillagg): (make_tillagg, "tillagg"),
            ("instans", is_delmal): (False, None),
            ("instans", is_endmeta): (False, None),
            ("betankande", is_domskal): transition_domskal, # either (make_domskal, "domskal") or (False, None)
            ("betankande", is_domslut): (make_domslut, "domslut"),
            ("__done__", is_domskal): (False, None), 
            ("__done__", is_skiljaktig): (False, None), 
            ("__done__", is_tillagg): (False, None), 
            ("__done__", is_delmal): (False, None), 
            ("__done__", is_endmeta): (False, None), 
            ("__done__", is_domslut): (make_domslut, "domslut"),
            ("dom", is_domskal): (make_domskal, "domskal"),
            ("dom", is_domslut): (make_domslut, "domslut"),
            ("dom", is_instans): (False, None),
            ("dom", is_skiljaktig): (False, None), # Skiljaktig mening is not considered
                                                   # part of the dom, but rather an appendix
            ("dom", is_tillagg): (False, None), 
            ("dom", is_endmeta): (False, None),
            ("domskal", is_delmal): (False, None), 
            ("domskal", is_domslut): (False, None),
            ("domskal", is_instans): (False, None), 
            ("domslut", is_instans): (False, None),
            ("domslut", is_domskal): (False, None),
            ("domslut", is_skiljaktig): (False, None), 
            ("domslut", is_tillagg): (False, None),
            ("domslut", is_endmeta): (False, None),
            ("skiljaktig", is_domslut): (False, None),
            ("skiljaktig", is_instans): (False, None),
            ("skiljaktig", is_skiljaktig): (False, None),
            ("skiljaktig", is_tillagg): (False, None),
            ("skiljaktig", is_delmal): (False, None),
            ("tillagg", is_tillagg): (False, None),
            ("tillagg", is_delmal): (False, None),
            ("tillagg", is_endmeta): (False, None),
            ("endmeta", is_paragraph): (make_paragraph, None),
            (commonstates, is_heading): (make_heading, None),
            (commonstates, is_paragraph): (make_paragraph, None),
                       })
        p.initial_state = "body"
        p.initial_constructor = make_body
        p.debug = os.environ.get('FERENDA_FSMDEBUG', False)
        return p.parse(paras)
        
    # FIXME: port to relate_all_setup / _teardown
    def GenerateMapAll(self):
        mapfile = os.path.sep.join(
            [self.baseDir, 'dv', 'generated', 'uri.map'])
        util.robust_remove(mapfile + ".new")

        parsed_dir = os.path.sep.join([self.baseDir, 'dv', 'parsed'])
        self._do_for_all(parsed_dir, '.xht2', self.GenerateMap)
        util.robustRename(mapfile + ".new", mapfile)

    def GenerateMap(self, basefile):
        start = time()
        infile = os.path.relpath(self._xmlFileName(basefile))
        head = codecs.open(infile, encoding='utf-8').read(1024)
        m = self.re_xmlbase(head)
        if m:
            uri = "http://rinfo.lagrummet.se/publ/rattsfall/%s" % m.group(1)
            mapfile = self.store.path('generated', 'uri.map', '.new')
            util.ensure_dir(mapfile)
            f = codecs.open(mapfile, 'a', encoding='iso-8859-1')
            f.write("%s\t%s\n" % (m.group(1), basefile))
            f.close()
            self.log.info("%s ok" % basefile)
            return
        else:
            self.log.warning("could not find xml:base in %s" % infile)

    def _simplify_ooxml(self, filename, pretty_print=True):
        # simplify the horrendous mess that is OOXML through simplify-ooxml.xsl
        with open(filename) as fp:
            intree = etree.parse(fp)
        fp = pkg_resources.resource_stream('ferenda', "res/xsl/simplify-ooxml.xsl")
        transform = etree.XSLT(etree.parse(fp))
        fp.close()
        resulttree = transform(intree)
        with open(filename, "wb") as fp:
            fp.write(etree.tostring(resulttree, pretty_print=pretty_print, encoding="utf-8"))
        

    def _merge_ooxml(self, soup):
        # this is a similar step to _simplify_ooxml, but merges w:p
        # elements in a BeautifulSoup tree. This step probably should
        # be performed through XSL and be put in _simplify_ooxml as
        # well.
                # The soup now contains a simplified version of OOXML where
        # lot's of nonessential tags has been stripped. However, the
        # central w:p tag often contains unneccessarily splitted
        # subtags (eg "<w:t>Avgörand</w:t>...<w:t>a</w:t>...
        # <w:t>tum</w:t>"). Attempt to join these
        #
        # FIXME: This could be a part of simplify_ooxml instead.
        for p in soup.find_all("w:p"):
            current_r = None
            for r in p.find_all("w:r"):
                # find out if formatting instructions (bold, italic)
                # are identical
                if current_r and current_r.find("w:rpr") == r.find("w:rpr"):
                    # ok, merge
                    ts = list(current_r.find_all("w:t"))
                    assert len(ts) == 1, "w:r should not contain exactly one w:t"
                    ns = ts[0].string
                    ns.replace_with(str(ns) + r.find("w:t").string)
                    r.decompose()
                else:
                    current_r = r
        return soup
    
            
    # gonna need this for news_criteria()
    pubs = {'http://rinfo.lagrummet.se/ref/rff/nja': 'Högsta domstolen',
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
