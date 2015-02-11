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
from six import BytesIO
import tempfile
from collections import defaultdict
from copy import deepcopy
from operator import attrgetter

# 3rdparty libs
import pkg_resources
from rdflib import Namespace, URIRef, Graph, RDF, Literal
import requests
import lxml.html
from lxml import etree
from bs4 import BeautifulSoup, NavigableString

# my libs
from ferenda import (Document, DocumentStore, Describer, WordReader, FSMParser,
                     Facet, TocPage, TocPageset, Feed, Feedset)
from ferenda.decorators import managedparsing, newstate
from ferenda import util, fulltextindex, errors
from ferenda.sources.legal.se.legalref import LegalRef
from ferenda.elements import (Body, Paragraph, CompoundElement, OrdinalElement,
                              Heading, Link)

from ferenda.elements.html import Strong, Em
from . import SwedishLegalSource, SwedishCitationParser, RPUBL
# from swedishlegalsource import SwedishLegalSource, SwedishCitationParser, RPUBL
DCTERMS = Namespace(util.ns['dcterms'])
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
#     dcterms:identifier "NJA 1987 s 187";
#     dcterms:bibliographicCitation "NJA 1987:68"
# This shld b owl:sameAs <http://rinfo.lagrummet.se/org/domstolsverket>
#     dcterms:publisher <http://localhost:8000/org/domstolsverket>;
#     dcterms:issued "2009-11-05"^^xsd:date.
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
#     dcterms:title "Överföring av mönsterregistrering..."@sv;
# shld be owl:sameAs <http://rinfo.lagrummet.se/org/hoegsta_domstolen>
#     dcterms:publisher <http://localhost:8000/org/hoegsta_domstolen>;
#     dcterms:issued "2009-11-05"^^xsd:date;
#     dcterms:subject <http://localhost:8000/concept/Mönsterrätt>;
#     dcterms:subject <http://localhost:8000/concept/Dubbelöverlåtelse>;
#     dcterms:subject <http://localhost:8000/concept/Formgivarrätt>;
#     dcterms:subject <http://localhost:8000/concept/Godtrosförvärv>;
#     dcterms:subject <http://localhost:8000/concept/Formgivning>;
# litteratur? dcterms:references t bnodes...
#
# uri: http://localhost:8000/res/dv/nja/2009/s_695 # hard to construct from "HDO/T170-08", requires a rdf lookup like .value(pred=RDF.type, object=RPUBL.Rattsfallsreferat)
# lang: sv
# body: [Paragraph(), Paragraph(), Paragraph(), ...]


class DVStore(DocumentStore):

    """Customized DocumentStore.
    """

    
    def basefile_to_pathfrag(self, basefile):
        return basefile

    def pathfrag_to_basefile(self, pathfrag):
        return pathfrag

    def downloaded_path(self, basefile, version=None, attachment=None,
                        suffix=None):
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

class OrderedParagraph(Paragraph, OrdinalElement):
    def as_xhtml(self, baseuri, parent_uri=None):
        element = super(OrderedParagraph, self).as_xhtml(baseuri, parent_uri)
        # FIXME: id needs to be unique in document by prepending a
        # instans identifier
        # element.set('id', self.ordinal)
        return element

class DomElement(CompoundElement):
    tagname = "div"
    prop = None
    def _get_classname(self):
        return self.__class__.__name__.lower()
    classname = property(_get_classname)

    def as_xhtml(self, baseuri, parent_uri=None):
        element = super(DomElement, self).as_xhtml(baseuri, parent_uri)
        if self.prop:
            # ie if self.prop = ('ordinal', 'dcterms:identifier'), then
            # dcterms:identifier = self.ordinal
            if (hasattr(self, self.prop[0]) and
                getattr(self, self.prop[0]) and
                isinstance(getattr(self, self.prop[0]), str)):
                element.set('content', getattr(self, self.prop[0]))
                element.set('property', self.prop[1])
        return element

class Delmal(DomElement):
    prop = ('ordinal', 'dcterms:identifier')
    
class Instans(DomElement):
    prop = ('court', 'dcterms:creator')
        
class Dom(DomElement):
    prop = ('malnr', 'dcterms:identifier')
    
class Domskal(DomElement): pass 
class Domslut(DomElement): pass # dcterms:author <- names of judges
class Betankande(DomElement): pass # dcterms:author <- referent
class Skiljaktig(DomElement): pass # dcterms:author <- name
class Tillagg(DomElement): pass # dcterms:author <- name
class Endmeta(DomElement): pass


class DV(SwedishLegalSource):
    """Handles legal cases, in report form, from primarily final instance courts.

    Cases are fetched from Domstolsverkets FTP server for "Vägledande
    avgöranden", and are converted from doc/docx format.

    """
    alias = "dv"
    downloaded_suffix = ".zip"
    rdf_type = (RPUBL.Rattsfallsreferat, RPUBL.Rattsfallsnotis)
    documentstore_class = DVStore
    # This is very similar to SwedishLegalSource.required_predicates,
    # only DCTERMS.title has been changed to RPUBL.referatrubrik (and if
    # our validating function grokked that rpubl:referatrubrik
    # rdfs:isSubpropertyOf dcterms:title, we wouldn't need this). Also, we
    # removed dcterms:issued because there is no actual way of getting
    # this data (apart from like the file time stamps).  On further
    # thinking, we remove RPUBL.referatrubrik as it's not present (or
    # required) for rpubl:Rattsfallsnotis
    required_predicates = [RDF.type, DCTERMS.identifier, PROV.wasGeneratedBy]
    
    DCTERMS = Namespace(util.ns['dcterms'])
    sparql_annotations = "res/sparql/dv-annotations.rq"
    xslt_template = "res/xsl/dv.xsl"


    @classmethod
    def relate_all_setup(cls, config):
        # FIXME: If this was an instancemethod, we could use
        # self.store methods instead
        parsed_dir = os.path.sep.join([config.datadir, 'dv', 'parsed'])
        mapfile = os.path.sep.join(
            [config.datadir, 'dv', 'generated', 'uri.map'])
        log = cls._setup_logger(cls.alias)
        if not util.outfile_is_newer(util.list_dirs(parsed_dir, ".xhtml"), mapfile):
            prefix = config.url + config.urlpath
            # prefix = config.url + "res/" + cls.alias + "/"
            re_xmlbase = re.compile('<head about="%s([^"]+)"' % prefix)
            log.info("Creating uri.map file")
            cnt = 0
            util.robust_remove(mapfile + ".new")
            util.ensure_dir(mapfile)
            # FIXME: Not sure utf-8 is the correct codec for us -- it
            # might be iso-8859-1 (it's to be used by mod_rewrite).
            with codecs.open(mapfile+".new", "w", encoding="utf-8") as fp:
                for f in util.list_dirs(parsed_dir, ".xhtml"):
                    # get basefile from f in the simplest way
                    basefile = f[len(parsed_dir)+1:-6]
                    head = codecs.open(f, encoding='utf-8').read(1024)
                    m = re_xmlbase.search(head)
                    if m:
                        fp.write("%s\t%s\n" % (m.group(1), basefile))
                        cnt += 1
                    else:
                        log.warning("%s: Could not find valid head[@about] in %s" % (basefile, f))
            util.robust_rename(mapfile + ".new", mapfile)
            log.info("uri.map created, %s entries" % cnt)
        else:
            log.debug("Not regenerating uri.map")
            pass
        return super(cls, DV).relate_all_setup(config)

    # def relate(self, basefile, otherrepos): pass
        
    def get_default_options(self):
        opts = super(DV, self).get_default_options()
        opts['ftpuser'] = None
        opts['ftppassword'] = None
        opts['parsebodyrefs'] = True
        return opts

    def canonical_uri(self, basefile):
        # The canonical URI for HDO/B3811-03 should be
        # https://lagen.nu/dom/nja/2004s510. We can't know
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

    # override DocumentRepository.basefile_from_uri to account for the
    # fact that there is no 1:1 correspondance between basefiles and
    # uris
    def basefile_from_uri(self, uri):
        prefix = self.config.url + self.config.urlpath
        if uri.startswith(prefix):
            path = uri[len(prefix):]
            if not hasattr(self, "_basefilemap"):
                self._basefilemap = {}
                mapfile = self.store.path("uri", "generated", ".map")
                with codecs.open(mapfile, encoding="utf-8") as fp:
                    for line in fp:
                        uriseg, basefile = line.split("\t")
                        self._basefilemap[uriseg] = basefile.strip()

            if path in self._basefilemap:
                return self._basefilemap[path]
            else:
                # this will happen for older cases for which we don't
                # have any files. We could invent URI-redived
                # basefiles for these, and gain a sort of skeleton
                # entry for those, which we could use to track
                # eg. frequently referenced older cases.
                self.log.warning("%s: Could not find corresponding basefile" % uri)
                return None
        else:
            pass # The URI didn't start with our expected prefix, it's not a Rattsfall URI
            
    # FIXME: store.list_basefiles_for("parse") must be fixed to handle two
    # different suffixes. Maybe store.downloaded_path() as well, so that
    # it returns .docx if a .docx file indeed exists, and .doc otherwise.
    # But this case (where documents can be in two (or more) formats depending
    # on age isn't uncommon, maybe DocumentStore should support it natively
    # (like with optional suffix parameter to download_path)?

    def download(self, basefile):
        if basefile is not None:
            raise ValueException("DV.download cannot process a basefile parameter")
        # recurse =~ download everything, which we do if force is
        # specified OR if we've never downloaded before
        recurse = False
        # if self.config.lastdownload has not been set, it has only
        # the type value, so self.config.lastdownload will raise
        # AttributeError. Should it return None instead?
        if self.config.force or 'lastdownload' not in self.config:
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
        except errors.MaxDownloadsReached:  # ok we're done!
            pass

    def download_ftp(self, dirname, recurse, user=None, password=None, connection=None):
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
                self.download_ftp(filename, recurse, connection=connection)
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
                    # fix HERE
                    if ('downloadmax' in self.config and
                        self.config.downloadmax and
                        self.downloadcount >= self.config.downloadmax):
                        raise errors.MaxDownloadsReached()
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
                raise errors.ParseError("Cannot find value for month in %s (looked in %s" % (basefile, prev_path))
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
                fp = codecs.open(self.store.intermediate_path(basefile), "w", encoding="utf-8")
                fp.write('<body%s>' % xmlns)
                if filetype != "docx":
                    fp.write("\n")
                if coll == "HDO" and not avd_p:
                    avd_p = find_month_in_previous(basefile)
                if avd_p:
                    fp.write(str(avd_p))
            if fp:
                fp.write(str(p))
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
    labels = {'Rubrik': DCTERMS.description,
              'Domstol': DCTERMS['creator'],  # konvertera till auktoritetspost
              'Målnummer': RPUBL['malnummer'],
              'Domsnummer': RPUBL['domsnummer'],
              'Diarienummer': RPUBL['diarienummer'],
              'Avdelning': RPUBL['domstolsavdelning'],
              'Referat': DCTERMS['identifier'],
              'Avgörandedatum': RPUBL['avgorandedatum'],  # konvertera till xsd:date
              }

    # Metadata som kan innehålla noll eller flera poster.
    # Litteratur/sökord har ingen motsvarighet i RPUBL-vokabulären
    multilabels = {'Lagrum': RPUBL['lagrum'],
                   'Rättsfall': RPUBL['rattsfallshanvisning'],
                   # dcterms:references vore bättre, men sådana ska inte ha literalvärden
                   'Litteratur': DCTERMS['relation'],
                   'Sökord': DCTERMS['subject']
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
                          self.ns['rinfoex'].patchdescription,
                          Literal(patchdesc)))
        doc.body = self.format_body(rawbody, doc.basefile)
        self.parse_entry_update(doc)
        return True


    def parse_entry_title(self, doc):
        # FIXME: The primary use for entry.title is to generate
        # feeds. Should we construct a feed-friendly title here
        # (rpubl:referatrubrik is often too wordy, dctemr:identifier +
        # dcterms:subject might be a better choice -- also notisfall
        # does not have any rpubl:referatrubrik)
        title = doc.meta.value(URIRef(doc.uri), self.ns['rpubl'].referatrubrik)
        if title:
            return str(title)

    # smth like this
    def sanitize_body(self, rawbody):
        for section in rawbody:
            # are all sections strings? or what can they be?
            if section.endswith(".II"):
                yield one
                yield two
            else:
                yield section
                
        
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
            # keep this in sync w extract_notis
            re_notisstart = re.compile("(?:Den (?P<avgdatum>\d+):[ae].\s+|)(?P<ordinal>\d+)\.[ \xa0]*\((?P<malnr>\w[ \xa0]\d+-\d+)\)", flags=re.UNICODE)
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
            # the avgdatum regex attempts to include valid dates, eg
            # not "2770-71-12"
            re_avgdatum = re.compile(r"[AD]:(?P<avgdatum>\d{2,4}\-[01]\d\-\d{2})")
            re_sokord = re.compile("Uppslagsord: (?P<sokord>.*)", flags=re.DOTALL)
            re_lagrum = re.compile("Lagrum: ?(?P<lagrum>.*)", flags=re.DOTALL)
            # headers consists of the first five or six
            # chunks. Doesn't end until "^Not \d+."
            header = []
            done = False
            while not done and iterator:
                # can possibly be "Not 1a." (RÅ 1994 not 1) or "Not. 109." (RÅ 1998 not 109)
                if re.match("Not(is|)\.? \d+[abc]?\.? ", iterator[0].get_text().strip()):
                    done = True
                else:
                    tmp = iterator.pop(0)
                    if tmp.get_text().strip():
                        # REG specialcase 
                        if header and header[-1].get_text() == "Lagrum:":
                            header[-1].append(list(tmp.children)[0])
                        else:
                            header.append(tmp)
            if not done:
                raise errors.ParseError("Cannot find notis number in %s" % basefile)
                                
        if coll == "HDO":
            head['Domstol'] = "Högsta Domstolen"
        elif coll == "HFD":
            head['Domstol'] = "Högsta förvaltningsdomstolen"
        elif coll == "REG":
            head['Domstol'] = "Regeringsrätten"
        else:
            raise errors.ParseError("Unsupported: %s" % coll)
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
                # FIXME: should warn for missing Målnummer iff
                # Domsnummer is not present, and vice versa. But at
                # this point we don't have all fields
                if key not in ('Diarienummer', 'Domsnummer', 'Avdelning', 'Målnummer'): 
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
            # NB: localid needs to be a list
            head["_localid"] = [head["Domsnummer"]]
        else:
            raise errors.ParseError("Required key (Målnummer/Domsnummer) missing")

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
                raise errors.ParseError("Unparseable NJA ref '%s'" % head["Referat"])

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
                raise errors.ParseError("Unparseable ref '%s'" % head["Referat"])

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
                if int(m.group(1) <= '80'): # '80-01-01' => '1980-01-01',
                    year = '19' + m.group(1) 
                else:                     # '79-01-01' => '2079-01-01',
                    year = '20' + m.group(1) 
            else:
                year = m.group(1)
            head["Avgörandedatum"] = "%s-%s-%s" % (year, m.group(2), m.group(3))
        else:
            raise errors.ParseError("Unparseable date %s" % head["Avgörandedatum"])

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
            prefix = self.config.url + self.config.urlpath
            slug = self.slugs[domstol.lower()]
            # FIXME: We should create multiple urls if we have multiple malnummers?
            first_malnr = malnr[0]
            return "%(prefix)s%(slug)s/%(first_malnr)s/%(avg)s" % locals()

        def localize_uri(uri):
            if self.config.url == "https://lagen.nu/":
                sfsprefix = ""
            else:
                sfsprefix = "res/sfs/"
            if "publ/rattsfall" in uri:
                
                return uri.replace("http://rinfo.lagrummet.se/publ/rattsfall/",
                                   self.config.url + self.config.urlpath)
            elif "publ/sfs/" in uri:
                return uri.replace("http://rinfo.lagrummet.se/publ/sfs/",
                                   self.config.url + sfsprefix)
                
        def split_nja(value):
            return [x[:-1] for x in value.split("(")]

        def sokord_uri(value):
            baseuri = self.config.url + "concept/"
            return baseuri + util.ucfirst(value).replace(' ', '_')

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
                domdesc.value(self.ns['dcterms'].title, value, lang="sv")

            elif label == "Domstol":
                domdesc.rel(self.ns['dcterms'].publisher, self.lookup_resource(value))
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
                            if self.config.url == "https://lagen.nu/":  #FIXME!
                                uri = "https://lagen.nu/dataset/" + m.group(1).lower()
                            else:
                                uri = self.config.url + "coll/dv/" + m.group(1).lower()
                            refdesc.rel(self.ns['rpubl'][pred], uri)
                        else:
                            refdesc.value(self.ns['rpubl'][pred], m.group(1))
                refdesc.value(self.ns['dcterms'].identifier, value)

            elif label == "_nja_ordinal":
                refdesc.value(self.ns['dcterms'].bibliographicCitation,
                              value)
                m = re.search(r'\d{4}(?:\:| nr | not )(\d+)', value)
                if m:
                    refdesc.value(self.ns['rpubl'].lopnummer, m.group(1))

                refdesc.rel(self.ns['owl'].sameAs,
                            (self.config.url + self.config.urlpath +
                             "nja/" + value.split(" ")[1]))

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
                    domdesc.value(self.ns['dcterms'].relation, util.normalize_space(i))
            elif label == "Sökord":
                for s in value:
                    domdesc.rel(self.ns['dcterms'].subject, sokord_uri(s))

        # 3. mint some owl:sameAs URIs
        refdesc.rel(self.ns['owl'].sameAs, self.sameas_uri(refuri))
        domdesc.rel(self.ns['owl'].sameAs, self.sameas_uri(domuri))

        # 4. Add some same-for-everyone properties
        refdesc.rel(self.ns['dcterms'].publisher, self.lookup_resource('Domstolsverket'))
        if 'not' in head['Referat']:
            refdesc.rdftype(self.ns['rpubl'].Rattsfallsnotis)
        else:
            refdesc.rdftype(self.ns['rpubl'].Rattsfallsreferat)
        domdesc.rdftype(self.ns['rpubl'].VagledandeDomstolsavgorande)
        refdesc.rel(self.ns['rpubl'].referatAvDomstolsavgorande, domuri)
        refdesc.value(self.ns['prov'].wasGeneratedBy, self.qualified_class_name())
# FIXME: implement this
#        # 5. Create a meaningful identifier for the verdict itself (ie. "Göta hovrätts dom den 42 september 2340 i mål B 1234-42")
#        domdesc.value(self.ns['dcterms'].identifier, dom_to_identifier(head["Domstol"],
#                                                                   head["_localid"],
#                                                                   head["Avgörandedatum"])
#
        
        # 6. assert that we have everything we need
        
        # 7. done!
        return refuri

    def format_body(self, paras, basefile):
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
                self.ref_parser = LegalRef(LegalRef.RATTSFALL,
                                           LegalRef.LAGRUM,
                                           LegalRef.FORARBETEN)
            # You'd think we should initialize citparser with
            # self.config.url as the baseurl parameter. But by using
            # this special value we activate SwedishCitationParser's
            # method of filtering relative SFS links (which shouldn't
            # occur in a court report, and if it does, it's a sign
            # that our parser has missed the root SFS reference).
            baseurl = "http://example.org/sfs/9999:999"
            citparser = SwedishCitationParser(self.ref_parser, self.config.url)
            b = citparser.parse_recursive(b)
            
        # convert the unstructured list of Paragraphs to a
        # hierarchical tree of instances, domslut, domskäl, etc
        try:
            b = self.structure_body(deepcopy(b), basefile)
        except errors.FSMStateError as e:
            self.log.warning("%s: structure_body failed: %s" % (basefile, e))
            self.log.debug("%s: using unstructured body" % basefile)
        return b

    def structure_body(self, paras, basefile):
        return self.get_parser(basefile).parse(paras)

    @staticmethod
    def get_parser(basefile):
        re_courtname = re.compile("^(Högsta domstolen|Hovrätten (över|för) [A-ZÅÄÖa-zåäö ]+|([A-ZÅÄÖ][a-zåäö]+ )(tingsrätt|hovrätt))(|, mark- och miljödomstolen|, Mark- och miljööverdomstolen)$")

#         productions = {'karande': '..',
#                        'court': '..',
#                        'date': '..'}

        # at parse time, initialize matchers
        rx = (
            {'name': 'fr-överkl',
             're': '(?P<karanden>[\w\.\(\)\- ]+) överklagade (beslutet|domen) '
                   'till (?P<court>(Förvaltningsrätten|Länsrätten|Kammarrätten) i \w+(| län)'
                   '(|, migrationsdomstolen|, Migrationsöverdomstolen)|'
                   'Högsta förvaltningsdomstolen)( \((?P<date>\d+-\d+-\d+), '
                   '(?P<constitution>[\w\.\- ,]+)\)|$)',
             'method': 'match',
             'type': ('instans',),
             'court': ('REG', 'HFD', 'MIG')},

            {'name': 'fr-dom',
             're': '(?P<court>(Förvaltningsrätten|'
                   'Länsrätten|Kammarrätten) i \w+(| län)'
                   '(|, migrationsdomstolen|, Migrationsöverdomstolen)|'
                   'Högsta förvaltningsdomstolen) \((?P<date>\d+-\d+-\d+), '
                   '(?P<constitution>[\w\.\- ,]+)\)',
             'method': 'match',
             'type': ('dom',),
             'court': ('REG', 'HFD', 'MIG')},
            
            {'name': 'tr-dom',
             're': '(?P<court>TR:n|Tingsrätten|HovR:n|Hovrätten|Mark- och miljödomstolen) \((?P<constitution>[\w\.\- ,]+)\) (anförde|fastställde|stadfäste|meddelade) (följande i |i beslut i |i |)(dom|beslut) (d\.|d|den) (?P<date>\d+ \w+\.? \d+)',
             'method': 'match',
             'type': ('dom',),
             'court': ('HDO', 'HGO', 'HNN', 'HON', 'HSB', 'HSV', 'HVS')},
            {'name': 'hd-dom',
             're': 'Målet avgjordes efter huvudförhandling (av|i) (?P<court>HD) \((?P<constitution>[\w:\.\- ,]+)\),? som',
             'method': 'match',
             'type': ('dom',),
             'court': ('HDO',)},
            {'name': 'hd-dom2',
             're': '(?P<court>HD) \((?P<constitution>[\w:\.\- ,]+)\) meddelade den (?P<date>\d+ \w+ \d+) följande',
             'method': 'match',
             'type': ('dom',),
             'court': ('HDO',)},
            {'name': 'hd-fastst',
             're': '(?P<court>HD) \((?P<constitution>[\w:\.\- ,]+)\) (beslöt|fattade (slutligt|följande slutliga) beslut)',
             'method': 'match',
             'type': ('dom',),
             'court': ('HDO',)},

            {'name': 'mig-dom',
             're': '(?P<court>Kammarrätten i Stockholm, Migrationsöverdomstolen)  \((?P<date>\d+-\d+-\d+), (?P<constitution>[\w\.\- ,]+)\)',
             'method': 'match',
             'type': ('dom',),
             'court': ('MIG',)},
            {'name': 'miv-forstainstans',
             're': '(?P<court>Migrationsverket) avslog (ansökan|ansökningarna) den (?P<date>\d+ \w+ \d+) och beslutade att',
             'method': 'match',
             'type': ('dom',),
             'court': ('MIG',)},
            {'name': 'miv-forstainstans-2',
             're': '(?P<court>Migrationsverket) avslog den (?P<date>\d+ \w+ \d+) A:s ansökan och beslutade att',
             'method': 'match',
             'type': ('dom',),
             'court': ('MIG',)},
            {'name': 'mig-dom-alt',
             're': 'I sin dom avslog (?P<court>Förvaltningsrätten i Stockholm, migrationsdomstolen) \((?P<date>\d+- ?\d+-\d+), (?P<constitution>[\w\.\- ,]+)\)',
             'method': 'match',
             'type': ('dom',),
             'court': ('MIG',)},
            {'name': 'allm-åkl',
             're': 'Allmän åklagare yrkade (.*)vid (?P<court>(([A-ZÅÄÖ]'
                   '[a-zåäö]+ )+)(TR|tingsrätt))',
             'method': 'match',
             'type': ('instans',),
             'court': ('HDO', 'HGO', 'HNN', 'HON', 'HSB', 'HSV', 'HVS')},
            {'name': 'stämning',
             're': 'stämning å (?P<svarande>.*) vid (?P<court>(([A-ZÅÄÖ]'
                   '[a-zåäö]+ )+)(TR|tingsrätt))',
             'method': 'search',
             'type': ('instans',),
             'court': ('HDO', 'HGO', 'HNN', 'HON', 'HSB', 'HSV', 'HVS')},
            {'name': 'ansökan',
             're': 'ansökte vid (?P<court>(([A-ZÅÄÖ][a-zåäö]+ )+)'
                   '(TR|tingsrätt)) om ',
             'method': 'search',
             'type': ('instans',),
             'court': ('HDO', 'HGO', 'HNN', 'HON', 'HSB', 'HSV', 'HVS')},
            {'name': 'riksåkl',
             're': 'Riksåklagaren väckte i (?P<court>HD|HovR:n (över|för) '
                   '([A-ZÅÄÖ][a-zåäö]+ )+|[A-ZÅÄÖ][a-zåäö]+ HovR) åtal',
                   'method': 'match',
             'type': ('instans',),
             'court': ('HDO', 'HGO', 'HNN', 'HON', 'HSB', 'HSV', 'HVS')},
            {'name': 'tr-överkl',
             're': '(?P<karande>[\w\.\(\)\- ]+) (fullföljde talan|'
                   'överklagade) (|TR:ns dom.*)i (?P<court>HD|(HovR:n|hovrätten) '
                   '(över|för) (Skåne och Blekinge|Västra Sverige|Nedre '
                   'Norrland|Övre Norrland)|(Svea|Göta) (HovR|hovrätt))',
                   'method': 'match',
             'type': ('instans',),
             'court': ('HDO', 'HGO', 'HNN', 'HON', 'HSB', 'HSV', 'HVS')},
            {'name': 'fullfölj-överkl',
             're': '(?P<karanden>[\w\.\(\)\- ]+) fullföljde sin talan$',
             'method': 'match',
             'type': ('instans',)},
            {'name': 'myndighetsansökan',
             're': 'I (ansökan|en ansökan|besvär) hos (?P<court>\w+) '
                   '(om förhandsbesked|yrkade)',
             'method': 'match',
             'type': ('instans',),
             'court': ('REG', 'HFD')},
            {'name': 'myndighetsbeslut',
             're': '(?P<court>\w+) beslutade (därefter |)(den (?P<date>\d+ \w+ \d+)|'
                   '[\w ]+) att',
             'method': 'match',
             'type': ('instans',),
             'court': ('REG', 'HFD', 'MIG')},
            {'name': 'myndighetsbeslut2',
             're': '(?P<court>[\w ]+) (bedömde|vägrade) i (bistånds|)beslut'
                   ' (|den (?P<date>\d+ \w+ \d+))',
             'method': 'match',
             'type': ('instans',),
             'court': ('REG', 'HFD')},
            {'name': 'hd-revision',
             're': '(?P<karanden>[\w\.\(\)\- ]+) sökte revision och yrkade(,'
                   'i första hand,|,|) att (?P<court>HD|)',
             'method': 'match',
             'type': ('instans',),
             'court': ('HDO',)},
            {'name': 'hd-revision2',
             're': '(?P<karanden>[\w\.\(\)\- ]+) sökte revision$',
             'method': 'match',
             'type': ('instans',),
             'court': 'HDO'},
            {'name': 'hd-revision3',
             're': '(?P<karanden>[\w\.\(\)\- ]+) sökte revision och framställde samma yrkanden',
             'method': 'match',
             'type': ('instans',),
             'court': 'HDO'},
            {'name': 'överklag-bifall',
             're': '(?P<karanden>[\w\.\(\)\- ]+) (anförde besvär|'
                   'överklagade) och yrkade bifall till (sin talan i '
                   '(?P<prevcourt>HovR:n|TR:n)|)',
             'method': 'match',
             'type': ('instans',),
             'court': ('HDO', 'HGO', 'HNN', 'HON', 'HSB', 'HSV', 'HVS')},
            {'name': 'överklag-2',
             're': '(?P<karanden>[\w\.\(\)\- ]+) överklagade '
                   '(för egen del |)och yrkade (i själva saken |)att '
                   '(?P<court>HD|HovR:n|kammarrätten|Regeringsrätten|)',
             'method': 'match',
             'type': ('instans',)},
            {'name': 'överklag-3',
             're': '(?P<karanden>[\w\.\(\)\- ]+) överklagade (?P<prevcourt>'
                   '\w+)s (beslut|omprövningsbeslut|dom)( i ersättningsfrågan|) (hos|till) '
                   '(?P<court>[\w\, ]+?)( och|, som|$)',
             'method': 'match',
             'type': ('instans',)},
            {'name': 'överklag-4',
             're': '(?!Även )(?P<karanden>(?!HD fastställer)[\w\.\(\)\- ]+) överklagade ((?P<prevcourt>\w+)s (beslut|dom)|beslutet|domen)( och|$)',
             'method': 'match',
             'type': ('instans',)},
            {'name': 'hd-ansokan',
             're': '(?P<karanden>[\w\.\(\)\- ]+) anhöll i ansökan som inkom '
                   'till (?P<court>HD) d \d+ \w+ \d+',
             'method': 'match',
             'type': ('instans',),
             'court': ('HDO',)},
            {'name': 'hd-skrivelse',
             're': '(?P<karanden>[\w\.\(\)\- ]+) anförde i en till '
                   '(?P<court>HD) den \d+ \w+ \d+ ställd',
             'method': 'match',
             'type': ('instans',),
             'court': ('HDO',)},
            {'name': 'överklag-5',
             're': '(?!Även )(?P<karanden>[\w\.\(\)\- ]+?) överklagade '
                   '(?P<prevcourt>\w+)s (dom|domar)',
             'method': 'match', 
             'type': ('instans',)},
            {'name': 'överklag-6',
             're': '(?P<karanden>[\w\.\(\)\- ]+) överklagade domen till '
                   '(?P<court>\w+)($| och yrkade)',
             'method': 'match',
             'type': ('instans',)},
            {'name': 'myndighetsbeslut3',
             're': 'I sitt beslut den (?P<date>\d+ \w+ \d+) avslog '
                   '(?P<court>\w+)',
             'method': 'match',
             'type': ('instans',),
             'court': ('REG', 'HFD', 'MIG')},
            {'name': 'domskal',
             're': "(Skäl|Domskäl|HovR:ns domskäl|Hovrättens domskäl)(\. |$)",
             'method': 'match',
             'type': ('domskal',)},
            {'name': 'domskal-ref',
             're': "(Tingsrätten|TR[:\.]n|Hovrätten|HD|Högsta förvaltningsdomstolen) \([^)]*\) (meddelade|anförde|fastställde|yttrade)",
             'method': 'match',
             'type': ('domskal',)},
            {'name': 'domskal-dom-fr', # a simplified copy of fr-överkl
             're': '(?P<court>(Förvaltningsrätten|'
                   'Länsrätten|Kammarrätten) i \w+(| län)'
                   '(|, migrationsdomstolen|, Migrationsöverdomstolen)|'
                   'Högsta förvaltningsdomstolen) \((?P<date>\d+-\d+-\d+), '
                   '(?P<constitution>[\w\.\- ,]+)\),? yttrade',
             'method': 'match',
             'type': ('domskal',)},
            {'name': 'domslut-standalone',
             're': '(Domslut|(?P<court>Hovrätten|HD|Högsta förvaltningsdomstolen):?s avgörande)$',
             'method': 'match',
             'type': ('domslut',)},
            {'name': 'domslut-start',
             're': '(?P<court>[\w ]+(domstolen|rätten))s avgörande$',
             'method': 'match',
             'type': ('domslut',)}
        )
        court = basefile.split("/")[0]
        matchers = defaultdict(list)
        matchersname = defaultdict(list)
        for pat in rx:
            if 'court' not in pat or court in pat['court']:
                for t in pat['type']:
                    # print("Adding pattern %s to %s" %  (pat['name'], t))
                    matchers[t].append(getattr(re.compile(pat['re'], re.UNICODE), pat['method']))
                    matchersname[t].append(pat['name'])
            
            
        def is_delmal(parser):
            # should handle "IV" and "I (UM1001-08)"
            strchunk = str(parser.reader.peek())
            if (len(strchunk) < 20 and
                not strchunk.endswith(".") and
                strchunk.split(" ",1)[0] in ("I", "II", "III", "IV")):
                return {'id': strchunk.split(" ",1)[0]}
            else:
                return {}
                
        def is_instans(parser, chunk=None):
            """Determines whether the current position starts a new instans part of the report.

            """
            chunk = parser.reader.peek()
            strchunk = str(chunk)

            res = analyze_instans(strchunk)
            if res:
                # in some referats, two subsequent chunks both matches
                # analyze_instans, even though they refer to the _same_
                # instans. Check to see if that is the case
                
                if (hasattr(parser, 'current_instans') and
                    hasattr(parser.current_instans, 'court') and
                    parser.current_instans.court and 
                    is_equivalent_court(res['court'],
                                        parser.current_instans.court)):
                    return {}
                else:
                    return res
            elif parser._state_stack == ['body']:
                # if we're at root level, *anything* starts a new instans
                return True
            else:
                return {}

        def is_equivalent_court(newcourt, oldcourt):
            # should handle a bunch of cases
            # >>> is_equivalent_court("Göta Hovrätt", "HovR:n")
            # True
            # >>> is_equivalent_court("HD", "Högsta domstolen")
            # True
            # >>> is_equivalent_court("Linkäpings tingsrätt", "HovR:n")
            # False
            newcourt = canonicalize_court(newcourt)
            oldcourt = canonicalize_court(oldcourt)
            if newcourt == oldcourt:
                return True
            else:
                return False

        def canonicalize_court(courtname):
            if isinstance(courtname, bool):
                return courtname # we have no idea which court this
                                 # is, only that it is A court
            else:
                return courtname.replace("HD", "Högsta domstolen").replace("HovR", "Hovrätt")

        def is_heading(parser):
            chunk = parser.reader.peek()
            strchunk = str(chunk)
            if not strchunk.strip():
                return False
            # a heading is reasonably short and does not end with a
            # period (or other sentence ending typography)
            return len(strchunk) < 140 and not (strchunk.endswith(".") or
                                                strchunk.endswith(":") or
                                                strchunk.startswith("”"))
                                            

        def is_betankande(parser):
            strchunk = str(parser.reader.peek())
            return strchunk == "Målet avgjordes efter föredragning."
            
        def is_dom(parser):
            strchunk = str(parser.reader.peek())
            res = analyze_dom(strchunk)
            return res

        def is_domskal(parser):
            strchunk = str(parser.reader.peek())
            res = analyze_domskal(strchunk)
            return res

        def is_domslut(parser):
            strchunk = str(parser.reader.peek())
            return analyze_domslut(strchunk)
            
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

        # Turns out, this is really difficult if you consider
        # abbreviations.  This particular heuristic splits on periods
        # only (Sentences ending with ? or ! are rare in legal text)
        # and only if followed by a capital letter (ie next sentence)
        # or EOF. Does not handle things like "Mr. Smith" but that's
        # also rare in swedish text. However, needs to handle "Linder,
        # Erliksson, referent, och C. Bohlin", so another heuristic is
        # that the sentence before can't end in a single capital
        # letter.
        def split_sentences(text):
            text = util.normalize_space(text)
            text += " "
            return [x.strip() for x in re.split("(?<![A-ZÅÄÖ])\. (?=[A-ZÅÄÖ]|$)", text)]

        def analyze_instans(strchunk):
            res = {}
            # Case 1: Fixed headings indicating new instance
            if re_courtname.match(strchunk):
                res['court'] = strchunk
                res['complete'] = True
                return res
            else:
                # case 2: common wording patterns indicating new
                # instance
                # "H.T. sökte revision och yrkade att HD måtte fastställa" =>
                # <Instans name="HD"><str>H.T. sökte revision och yrkade att <PredicateSubject rel="HD" uri="http://lagen.nu/org/2008/hogsta-domstolen/">HD>/PredicateSubject>
                # <div class="instans" rel="dc:creator" href="..."

                
                # the needed sentence is usually 1st or 2nd
                # (occassionally 3rd), searching more yields risk of
                # false positives.
                
                for sentence in split_sentences(strchunk)[:3]:
                    for (r, rname) in zip(matchers['instans'], matchersname['instans']):
                        m = r(sentence)
                        if m:
                            # print("analyze_instans: Matcher '%s' succeeded on '%s'" % (rname, sentence))
                            mg = m.groupdict()
                            if 'court' in mg and mg['court']:
                                res['court'] = mg['court'].strip()
                            else:
                                res['court'] = True
                            #if 'prevcourt' in mg and mg['prevcourt']:
                            #    res['prevcourt'] = mg['prevcourt'].strip()
                            if 'date' in mg and mg['date']:
                                parse_swed = DV().parse_swedish_date
                                parse_iso = DV().parse_iso_date
                                try:
                                    res['date'] = parse_swed(mg['date'])
                                except ValueError:
                                    res['date'] = parse_iso(mg['date'])
                            return res
            return res

        def analyze_dom(strchunk):
            res = {}
            # special case for "referat" who are nothing but straight verdict documents.
            if strchunk.strip() == "SAKEN":
                return {'court': True}
            # probably only the 1st sentence is interesting
            for sentence in split_sentences(strchunk)[:1]:
                for (r, rname) in zip(matchers['dom'], matchersname['dom']):
                    m = r(sentence)
                    if m:
                        # print("analyze_dom: Matcher '%s' succeeded on '%s': %r" % (rname, sentence,m.groupdict()))
                        mg = m.groupdict()
                        if 'court' in mg and mg['court']:
                            res['court'] = mg['court'].strip()
                        if 'date' in mg and mg['date']:
                            parse_swed = DV().parse_swedish_date
                            parse_iso = DV().parse_iso_date
                            try:
                                res['date'] = parse_swed(mg['date'])
                            except ValueError:
                                try: 
                                    res['date'] = parse_iso(mg['date'])
                                except ValueError:
                                    pass
                                    # or res['date'] = mg['date']??
                                
                        #if 'constitution' in mg:
                        #    res['constitution'] = parse_constitution(mg['constitution'])
                        return res
            return res

        def analyze_domskal(strchunk):
            res = {}
            # only 1st sentence
            for sentence in split_sentences(strchunk)[:1]:
                for (r, rname) in zip(matchers['domskal'], matchersname['domskal']):
                    m = r(sentence)
                    if m:
                        # print("analyze_domskal: Matcher '%s' succeeded on '%s'" % (rname, sentence))
                        res['domskal'] = True
                        return res
            return res
            
        def analyze_domslut(strchunk):
            res = {}
            # only 1st sentence
            for sentence in split_sentences(strchunk)[:1]:
                for (r, rname) in zip(matchers['domslut'], matchersname['domslut']):
                    m = r(sentence)
                    if m:
                        # print("analyze_domslut: Matcher '%s' succeeded on '%s'" % (rname, sentence))
                        mg = m.groupdict()
                        if 'court' in mg and mg['court']:
                            res['court'] = mg['court'].strip()
                        else:
                            res['court'] = True
                        return res
            return res

        def parse_constitution(strchunk):
            res = []
            for thing in strchunk.split(", "):
                if thing in ("ordförande", "referent"):
                    res[-1]['position'] = thing
                elif thing.startswith("ordförande ") or thing.startswith("ordf "):
                    pos, name = thing.split(" ", 1)
                    if name.startswith("t f lagmannen"):
                        title, name = name[:13], name[14:]
                    elif name.startswith("hovrättsrådet"):
                        title, name = name[:13], name[14:]
                    else:
                        title = None
                    r = {'name': name,
                         'position': pos,
                         'title': title}
                    if 'title' not in r:
                        del r['title']
                    res.append(r)
                else:
                    name = thing
                    res.append({'name': name})
            # also filter nulls
            return res

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
            idata = analyze_instans(strchunk)
            # idata may be {} if the special toplevel rule in is_instans applied

            if 'complete' in idata:
                i = Instans(court=strchunk)
                court = strchunk
            elif 'court' in idata and idata['court'] is not True:
                i = Instans([chunk], court=idata['court'])
                court = idata['court']
            else:
                i = Instans([chunk], court=None)
                court = ""

            # FIXME: ugly hack, but is_instans needs access to this
            # object...
            parser.current_instans = i
            res = parser.make_children(i)

            # might need to adjust the court parameter based on better
            # information in the parse tree
            for child in res:
                if isinstance(child, Dom) and hasattr(child, 'court'):
                    # longer courtnames are better
                    if len(str(child.court)) > len(court):
                        i.court = child.court
            return res

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
            # fix date, constitution etc. Note peek() instead of read() --
            # this is so is_domskal can have a chance at the same data
            ddata = analyze_dom(str(parser.reader.peek()))
            d = Dom(avgorandedatum=ddata.get('date'),
                    court=ddata.get('court'),
                    malnr=ddata.get('caseid'))
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
            if not strchunk.strip(): # filter out empty things
                return None
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
                return chunk.split(".", 1)[0]

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
        # "dom" should not really be a commonstate (it should
        # theoretically alwawys be followed by domskal or maybe
        # domslut) but in some cases, the domskal merges with the
        # start of dom in such a way that we can't transition into
        # domskal right away (eg HovR:s dom in HDO/B10-86_1 and prob
        # countless others)
        commonstates = ("body", "delmal", "instans", "dom", "domskal", "domslut", "betankande", "skiljaktig", "tillagg")
        
        p.set_transitions({
            ("body", is_delmal): (make_delmal, "delmal"),
            ("body", is_instans): (make_instans, "instans"),
            ("body", is_endmeta): (make_endmeta, "endmeta"),
            ("delmal", is_instans): (make_instans, "instans"),
            ("delmal", is_delmal): (False, None),
            ("delmal", is_endmeta): (False, None),
            ("instans", is_betankande): (make_betankande, "betankande"),
            ("instans", is_domslut): (make_domslut, "domslut"),
            ("instans", is_dom): (make_dom, "dom"),
            ("instans", is_instans): (False, None),
            ("instans", is_skiljaktig): (make_skiljaktig, "skiljaktig"),
            ("instans", is_tillagg): (make_tillagg, "tillagg"),
            ("instans", is_delmal): (False, None),
            ("instans", is_endmeta): (False, None),
            ("betankande", is_domskal): transition_domskal, # either (make_domskal, "domskal") or (False, None)
            ("betankande", is_domslut): (make_domslut, "domslut"),
            ("betankande", is_dom): (False, None),
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
            ("domslut", is_delmal): (False, None), 
            ("domslut", is_instans): (False, None),
            ("domslut", is_domskal): (False, None),
            ("domslut", is_skiljaktig): (False, None), 
            ("domslut", is_tillagg): (False, None),
            ("domslut", is_endmeta): (False, None),
            ("domslut", is_dom): (False, None), 
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
        return p

    def _simplify_ooxml(self, filename, pretty_print=True):
        # simplify the horrendous mess that is OOXML through simplify-ooxml.xsl
        with open(filename, "rb") as fp:
            data = fp.read()
            # in some rare cases, the value \xc2\x81 (utf-8 for
            # control char) is used where "Å" (\xc3\x85) should be
            # used.
            if b"\xc2\x81" in data:
                self.log.warning("Working around control char x81 in text data")
                data = data.replace(b"\xc2\x81", b"\xc3\x85")
            intree = etree.parse(BytesIO(data))
            # intree = etree.parse(fp)
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
    
    def facets(self):
        # NOTE: it's important that RPUBL.rattsfallspublikation is the
        # first facet (toc_pagesets depend on it)
        def myselector(row, binding, resource_graph=None):
            return (util.uri_leaf(row['rpubl_rattsfallspublikation']),
                    row['rpubl_arsutgava'])

        def mykey(row, binding, resource_graph=None):
            if binding == "main":
                # we'd really like
                # rpubl:VagledandeDomstolsavgorande/rpubl:avgorandedatum,
                # but that requires modifying facet_query
                return row['update']
            else:
                return util.split_numalpha(row['dcterms_identifier'])

        return [Facet(RPUBL.rattsfallspublikation,
                      indexingtype=fulltextindex.Resource(),
                      use_for_toc=True,
                      use_for_feed=True,
                      selector=myselector, # =>  ("ad", "2001"), ("nja", "1981")
                      key=Facet.resourcelabel,
                      identificator=Facet.defaultselector,
                      dimension_type='ref'),
                Facet(RPUBL.referatrubrik,
                      indexingtype=fulltextindex.Text(boost=4),
                      toplevel_only=True,
                      use_for_toc=False),
                Facet(DCTERMS.identifier,
                      use_for_toc=False),
                Facet(RPUBL.arsutgava,
                      indexingtype=fulltextindex.Label(),
                      use_for_toc=False,
                      selector=Facet.defaultselector,
                      key=Facet.defaultselector,
                      dimension_type='value'),
                Facet(RDF.type,
                      use_for_toc=False,
                      use_for_feed=True,
                      dimension_label="main",
                      # key=  # FIXME add useful key method for sorting docs
                      identificator=lambda x, y, z: None)
                ]

    def toc_pagesets(self, data, facets):
        # our primary facet is RPUBL.rattsfallspublikation, but we
        # need to create one pageset for each value thereof.
        pagesetdict = {}
        selector_values = {}
        facet = facets[0]  # should be the RPUBL.rattsfallspublikation one
        for row in data:
            pagesetid = row['rpubl_rattsfallspublikation']
            if pagesetid not in pagesetdict:
                label = Facet.resourcelabel(row, 'rpubl_rattsfallspublikation',
                                            self.commondata)
                pagesetdict[pagesetid] = TocPageset(label=label,
                                                    predicate=pagesetid,
                                                    pages=[])
            selected = row['rpubl_arsutgava']
            selector_values[(pagesetid, selected)] = True
        
        for (pagesetid, value) in sorted(list(selector_values.keys())):
            pageset = pagesetdict[pagesetid]
            pageset.pages.append(TocPage(linktext=value,
                                         title="%s från %s" % (pageset.label, value),
                                         binding=util.uri_leaf(pagesetid),
                                         value=value))
        return list(pagesetdict.values())

    def news_feedsets(self, data, facets):
        # works pretty much the same as toc_pagesets, but returns ONE
        # feedset (not several) that has one feed per publisher
        feeds = {}
        facet = facets[0]  # should be the RPUBL.rattsfallspublikation one
        for row in data:
            feedid = row['rpubl_rattsfallspublikation']
            if feedid not in feeds:
                slug = Facet.term(row, 'rpubl_rattsfallspublikation')
                term = Facet.resourcelabel(row, 'rpubl_rattsfallspublikation',
                                           self.commondata)
                title = facet.label % {'term': term}
                feeds[feedid] = Feed(slug=slug,
                                     title=title,
                                     binding='rpubl_rattsfallspublikation',
                                     value=feedid)
        feeds = sorted(feeds.values(), key=attrgetter('value'))
        return [Feedset(label="Rättsfallspublikation",
                        predicate=facet.rdftype,
                        feeds=feeds),
                Feedset(label="All",
                        feeds=[Feed(slug="main",
                                    title="All documents",
                                    binding=None,
                                    value=None)])]

    def toc_select_for_pages(self, data, pagesets, facets):
        facet = facets[0]
        res = {}
        documents = {}
        for row in data:
            key = facet.selector(row, None)
            if key not in documents:
                documents[key] = []
            documents[key].append(row)
        pagesetdict = {}
        for pageset in pagesets:
            pagesetdict[util.uri_leaf(pageset.predicate)] = pageset
        for (binding, value) in sorted(documents.keys()):
            pageset = pagesetdict[binding]
            s = sorted(documents[(binding, value)], key=repr)
            res[(binding, value)] = [self.toc_item(binding, row)
                                     for row in s]
        return res

    def toc_item(self, binding, row):
        r = [Strong([Link(row['dcterms_identifier'],
                          uri=row['uri'])])]
        if 'rpubl_referatrubrik' in row:
            r.append(row['rpubl_referatrubrik'])
        return r

    def tabs(self):
        return [("Rättsfall", self.dataset_uri())]
