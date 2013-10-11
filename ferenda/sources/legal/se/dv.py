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

# 3rdparty libs
from rdflib import Namespace, URIRef
import requests
import lxml.html
from bs4 import BeautifulSoup

# my libs
from ferenda import DocumentStore, Describer, WordReader
from ferenda.decorators import managedparsing
from ferenda import util
from ferenda.sources.legal.se.legalref import LegalRef, Link
from ferenda.elements import Body, Paragraph
from . import SwedishLegalSource, RPUBL

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

    def intermediate_path(self, basefile):
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


class DV(SwedishLegalSource):
    alias = "dv"
    downloaded_suffix = ".zip"
    rdf_type = RPUBL.Rattsfallsreferat
    documentstore_class = DVStore
    namespaces = ('rdf',  # always needed
                  'dct',  # title, identifier, etc
                  'xsd',  # datatypes
                  'owl',  # : sameAs
                  ('rpubl', 'http://rinfo.lagrummet.se/ns/2008/11/rinfo/publ#')
                  )
    DCT = Namespace(util.ns['dct'])

    def get_default_options(self):
        opts = super(DV, self).get_default_options()
        opts['ftpuser'] = None
        opts['ftppassword'] = None
        return opts

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
                localpath = self.store.downloaded_path(basefile)
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

                localpath = self.store.downloaded_path(basefile)
                if os.path.exists(localpath) and not self.config.force:
                    pass  # we already got this
                else:
                    absolute_url = urljoin(url, link)
                    self.log.debug('Fetching %s to %s' % (link, localpath))
                    resp = requests.get(absolute_url)
                    with self.store.open_downloaded(basefile, "wb") as fp:
                        fp.write(resp.content)
                    self.process_zipfile(localpath)

    # eg. HDO_T3467-96.doc or HDO_T3467-96_1.doc
    re_malnr = re.compile(r'([^_]*)_([^_\.]*)_?(\d*)(\.docx?)')
    # eg. HDO_T3467-96_BYTUT_2010-03-17.doc or
    #     HDO_T3467-96_BYTUT_2010-03-17_1.doc
    re_bytut_malnr = re.compile(
        r'([^_]*)_([^_\.]*)_BYTUT_\d+-\d+-\d+_?(\d*)(\.docx?)')
    re_tabort_malnr = re.compile(
        r'([^_]*)_([^_\.]*)_TABORT_\d+-\d+-\d+_?(\d*)(\.docx?)')

    def process_zipfile(self, zipfilename):
        removed = replaced = created = untouched = 0
        zipf = zipfile.ZipFile(zipfilename, "r")
        for bname in zipf.namelist():
            if not isinstance(bname, str):  # py2
                # Files in the zip file are encoded using codepage 437
                name = bname.decode('cp437')
            else:
                name = bname
            if "_notis_" in name:
                continue
            name = os.path.split(name)[1]
            if 'BYTUT' in name:
                m = self.re_bytut_malnr.match(name)
            elif 'TABORT' in name:
                m = self.re_tabort_malnr.match(name)
            else:
                m = self.re_malnr.match(name)
            if m:
                (court, malnr, referatnr, suffix) = (
                    m.group(1), m.group(2), m.group(3), m.group(4))
                assert ((suffix == ".doc") or (suffix == ".docx")
                        ), "Unknown suffix %s in %r" % (suffix, name)
                if referatnr:
                    basefile = "%s/%s_%s" % (court, malnr, referatnr)
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
                else:
                    if "BYTUT" in name:
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
                self.log.warning('Kunde inte tolka filnamnet %r i %s' %
                                (name, os.path.relpath(zipfilename)))
        self.log.debug('Processade %s, skapade %s,  bytte ut %s, tog bort %s, lät bli %s filer' %
                       (os.path.relpath(zipfilename), created, replaced, removed, untouched))

    re_NJAref = re.compile(r'(NJA \d{4} s\. \d+) \(alt. (NJA \d{4}:\d+)\)')
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
    slugs = {'Arbetsdomstolen': 'ad',
             'Domstolsverket': 'dv',
             'Göta hovrätt': 'hgo',
             'Högsta domstolen': 'hd',
             'Högsta förvaltningsdomstolen': 'hfd',
             'Hovrätten för Nedre Norrland': 'hnn',
             'Hovrätten för Övre Norrland': 'hon',
             'Hovrätten för Västra Sverige': 'hvs',
             'Hovrätten över Skåne och Blekinge': 'hsb',
             'Justitiekanslern': 'jk',
             'Kammarrätten i Göteborg': 'kgg',
             'Kammarrätten i Jönköping': 'kjo',
             'Kammarrätten i Stockholm': 'kst',
             'Kammarrätten i Sundsvall': 'ksu',
             'Marknadsdomstolen': 'md',
             'Migrationsöverdomstolen': 'mig',
             'Miljööverdomstolen': 'mod',
             'Patentbesvärsrätten': 'pbr',
             'Rättshjälpsnämnden': 'rhn',
             'regr': 'Regeringsrätten',
             'Statens ansvarsnämnd': 'san',
             'Svea hovrätt': 'hsv'}

    @managedparsing
    def parse(self, doc):
        # FIXME: don't create these if they already exists
        self.lagrum_parser = LegalRef(LegalRef.LAGRUM)
        self.rattsfall_parser = LegalRef(LegalRef.RATTSFALL)
        docfile = self.store.downloaded_path(doc.basefile)
        intermediatefile = self.store.intermediate_path(doc.basefile)
        r = WordReader()
        intermediatefile, filetype = r.read(docfile, intermediatefile)
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
        if filetype == "docx":
            rawhead, rawbody = self.parse_ooxml(patchedtext, doc.basefile)
        else:
            rawhead, rawbody = self.parse_antiword_docbook(patchedtext, doc.basefile)
        doc.uri = self.polish_metadata(rawhead, doc)
        if patchdesc:
            doc.meta.add((URIRef(doc.uri),
                          self.ns['ferenda'].patchdescription,
                          patchdesc))
        doc.body = self.format_body(rawbody)  # FIXME: Write a
                                             # FSMParser to detect
                                             # high-level structure of
                                             # the document

    def parse_ooxml(self, text, basefile):
        soup = BeautifulSoup(text)
        for instrtext in soup.find_all("w:instrtext"):
            instrtext.decompose()
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
            node = soup.find(text=re.compile(key + ':'))
            if not node:
                # Sometimes these text fields are broken up
                # (eg "<w:t>Avgörand</w:t>...<w:t>a</w:t>...<w:t>tum</w:t>")
                # Use (ridiculous) fallback method
                nodes = soup.find_all('w:statustext', attrs={'w:val': key})
                if nodes:
                    node = nodes[-1]
                else:
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
            for e in p.findAll("w:t"):
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
            row = soup.find("informaltable").tgroup.tbody.row.findAll('entry')
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

    def polish_metadata(self, head, doc):
        basefile_regex = re.compile('(?P<type>\w+)/(?P<year>\d+)-(?P<ordinal>\d+)')

        def basefile_to_referat(basefile):
            templ = {'ADO': 'AD %(year)s nr %(ordinal)s',
                     'MD': 'MD %(year)s:%(ordinal)s'}
            m = basefile_regex.match(basefile)
            if m:
                return templ[m.group("type")] % (m.groupdict())

        def ref_to_uri(ref):
            # FIXME: We'd like to retire legalref and replace it with
            # pyparsing grammars.
            nodes = self.rattsfall_parser.parse(ref)
            uri = nodes[0].uri
            return localize_uri(uri)

        def dom_to_uri(domstol, malnr, avg):
            baseuri = self.config.url
            slug = self.slugs[domstol]
            return "%(baseuri)sres/dv/%(slug)s/%(malnr)s/%(avg)s" % locals()

        def localize_uri(uri):
            if "publ/rattsfall" in uri:
                return uri.replace("http://rinfo.lagrummet.se/publ/rattsfall",
                                   self.config.url + "res/dv")
            elif "publ/sfs/" in uri:
                return uri.replace("http://rinfo.lagrummet.se/publ/sfs",
                                   self.config.url + "res/sfs")

        def split_nja(value):
            # "NJA 2008 s 567 (NJA 2008:86)"=>("NJA 2008 s 567", "NJA 2008:86")
            return [x[:-1] for x in value.split("(")]

        def sokord_uri(value):
            return self.config.url + "concept/%s" % util.ucfirst(value).replace(' ', '_')

        # 0. create Referat key if not present
        if "Referat" not in head:
            # For some courts (MD, AD, MOD?, MIG?) this is possible
            head["Referat"] = basefile_to_referat(doc.basefile)

        # 1. mint uris and create the two Describers we'll use
        refuri = ref_to_uri(head["Referat"])
        refdesc = Describer(doc.meta, refuri)
        domuri = dom_to_uri(head["Domstol"],
                            head["Målnummer"],
                            head["Avgörandedatum"])
        domdesc = Describer(doc.meta, domuri)

        # 2. convert all strings in head to proper RDF
        for label, value in head.items():
            if label == "Rubrik":
                value = util.normalize_space(value)
                refdesc.value(self.ns['rpubl'].referatrubrik, value, lang="sv")
                domdesc.value(self.ns['dct'].title, value, lang="sv")

            elif label == "Domstol":
                domdesc.rel(self.ns['dct'].publisher, self.lookup_resource(value))
            elif label == "Målnummer":
                domdesc.rel(self.ns['rpubl'].malnummer, value)
            elif label == "Domsnummer":
                domdesc.rel(self.ns['rpubl'].domsnummer, value)
            elif label == "Diarienummer":
                domdesc.rel(self.ns['rpubl'].diarienummer, value)
            elif label == "Avdelning":
                domdesc.rel(self.ns['rpubl'].avdelning, value)
            elif label == "Referat":

                for pred, regex in {'rattsfallspublikation': r'([^ ]+)',
                                    'arsutgava': r'(\d{4})',
                                    'lopnummer': r'\d{4}(?:\:| nr )(\d+)',
                                    'sidnummer': r's.? ?(\d+)'}.items():
                    m = re.search(regex, value)
                    if m:
                        if pred == 'rattsfallspublikation':
                            # "NJA" -> "http://lcaolhost:8000/coll/dv/nja"
                            uri = self.config.url + "coll/dv/" + m.group(1).lower()
                            refdesc.rel(self.ns['rpubl'][pred], uri)
                        else:
                            refdesc.value(self.ns['rpubl'][pred], m.group(1))

                    if value.startswith("NJA"):
                        realvalue, extra = split_nja(value)
                        ordinal = extra.split(" ")[1]
                        refdesc.value(self.ns['dct'].bibliographicCitation,
                                      extra)
                        refdesc.rel(self.ns['owl'].sameAs,
                                    self.config.url + "res/dv/nja/" + ordinal)
                        refdesc.value(self.ns['dct'].identifier, realvalue)
                    else:
                        refdesc.value(self.ns['dct'].identifier, value)

            elif label == "Avgörandedatum":
                with util.c_locale():
                    d = datetime.strptime(value, '%Y-%m-%d')
                domdesc.value(self.ns['rpubl'].avgorandedatum, d)

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
                for s in self.re_delimSplit(value):
                    s = util.normalize_space(s)
                    if not s:
                        continue
                    # terms longer than 72 chars are not legitimate
                    # terms. more likely descriptions. If a term has a - in
                    # it, it's probably a separator between a term and a
                    # description
                    while len(s) >= 72 and " - " in s:
                        h, s = s.split(" - ", 1)
                        domdesc.rel(self.ns['dct'].subject, sokord_uri(h))
                    if len(s) < 72:
                        domdesc.rel(self.ns['dct'].subject, sokord_uri(s))

        # 3. mint some owl:sameAs URIs
        refdesc.rel(self.ns['owl'].sameAs, self.sameas_uri(refuri))
        domdesc.rel(self.ns['owl'].sameAs, self.sameas_uri(domuri))

        # 4. Add some same-for-everyone properties
        refdesc.rel(self.ns['dct'].publisher, self.lookup_resource('Domstolsverket'))
        refdesc.rdftype(self.ns['rpubl'].Rattsfallsreferat)
        domdesc.rdftype(self.ns['rpubl'].VagledandeDomstolsavgorande)
        refdesc.rel(self.ns['rpubl'].referatAvDomstolsavgorande, domuri)
        # 5. assert that we have everything we need

        # 6. done!
        return refuri

    def format_body(self, paras):
        return Body([Paragraph([x]) for x in paras])

    # FIXME: port to list_basefiles_for("parse")
    def ParseAll(self):
        self._do_for_all(intermediate_dir, '.doc', self.Parse)
        self._do_for_all(intermediate_dir, '.docx', self.Parse)

# FIXME: convert to a CONSTRUCT query, save as res/sparql/dv-annotations.rq
# Or maybe the default template should take a list of predicates, defaulting
# to dct:references, but which we could substitute rpubl:rattsfallshanvisning
#    annotation_query = """
# PREFIX dct:<http://purl.org/dc/terms/>
# PREFIX rpub:<http://rinfo.lagrummet.se/ns/2008/11/rinfo/publ#>
#
# SELECT ?uri ?id ?desc
# WHERE {
#      ?uri dct:description ?desc .
#      ?uri dct:identifier ?id .
#      ?uri rpubl:rattsfallshanvisning <%s>
#}
#""" % uri
#

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
