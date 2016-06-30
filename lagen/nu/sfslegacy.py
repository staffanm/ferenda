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
from rdflib.namespace import DCTERMS, SKOS
from lxml import etree
import bs4
import requests
import requests.exceptions
from layeredconfig import LayeredConfig
from cached_property import cached_property

# my own libraries
from ferenda import DocumentEntry, TripleStore
from ferenda import TextReader, Facet
from ferenda.sources.legal.se import legaluri
from ferenda import util
from ferenda.errors import FerendaException, DocumentRemovedError, ParseError
from ferenda.sources.legal.se.legalref import LegalRef, LinkSubject
from ferenda.sources.legal.se import SwedishCitationParser, RPUBL, SwedishLegalStore
from ferenda.sources.legal.se.elements import *
from . import SameAs

from ferenda.sources.legal.se.sfs import IckeSFS, UpphavdForfattning, InteUppdateradSFS, InteExisterandeSFS, SFSDocumentStore

class SFS(DocumentRepository, SameAs):

    """Handles consolidated (codified) versions of statutes from SFS
    (Svensk författningssamling) stored in the legacy file formats, no
    longer available for download.

    """
    alias = "sfs.legacy"
    rdf_type = RPUBL.KonsolideradGrundforfattning
    parse_types = LegalRef.LAGRUM, LegalRef.EULAGSTIFTNING
    parse_allow_relative = True
    source_encoding = "windows-1252"
    xslt_template = "xsl/sfs.xsl"

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
        return opts

    def download(self, basefile=None):
        return False

    def _find_uppdaterad_tom(self, sfsnr, filename=None, reader=None):
        if not reader:
            reader = TextReader(filename, encoding=self.source_encoding)
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

    def _checksum(self, filename):
        """MD5-checksumman för den angivna filen"""
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
        uri = self.minter.space.coin_uri(resource)
        # create eg "https://lagen.nu/sfs/2013:460/konsolidering" if
        # konsolidering = True instead of a issued date.
        # FIXME: This should be done in CoIN entirely
        if konsolidering is True:
            uri = uri.rsplit("/", 1)[0]
        # FIXME: temporary code we use while we get basefile_from_uri to work
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
        # "1874:26 s.11" -> <https://lagen.nu/sfs/1874:26s.11> -> "1874:26 s.11"
        basefile = basefile.replace("s.", " s.")
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
        # Check to see if this might not be a proper SFS at all
        # (from time to time, other agencies publish their stuff
        # in SFS - this seems to be handled by giving those
        # documents a SFS nummer on the form "N1992:31". Filter
        # these out.
        if basefile.startswith('N'):
            raise IckeSFS("%s is not a regular SFS" % basefile)
        filename = self.store.downloaded_path(basefile)
        try:
            t = TextReader(filename, encoding=self.source_encoding)
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
                    raise UpphavdForfattning("%s is an expired SFS" % basefile,
                                             dummyfile=self.store.parsed_path(basefile))
                t.seek(0)
            except IOError:
                t.seek(0)
        t.cuepast('<pre>')
        # remove &auml; et al
        try:
            # this is the preferred way from py34 onwards. FIXME: Move
            # this to ferenda.compat
            import html
            txt = html.unescape(t.readto('</pre>'))
        except ImportError:
            # this is the old way.
            hp = HTMLParser()
            txt = hp.unescape(t.readto('</pre>'))
        if '\r\n' not in txt:
            txt = txt.replace('\n', '\r\n')
        re_tags = re.compile("</?\w{1,3}>")
        txt = re_tags.sub('', txt)
        # add ending CRLF aids with producing better diffs
        txt += "\r\n"
        util.writefile(self.store.intermediate_path(basefile), txt,
                       encoding=self.source_encoding)
        return codecs.open(self.store.intermediate_path(basefile),
                           encoding=self.source_encoding)

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
        with codecs.open(filename, encoding=self.source_encoding) as rfp:
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
            textheader = textheader.decode(self.source_encoding)
        idx = textheader.index("\r\n" * 4)
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
        g = self.make_graph()  # used for qname lookup only
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
                                                         % basefile,
                                                         dummyfile=self.store.parsed_path(basefile))
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
                                qname = g.qname(node.predicate)
                                d[docuri][qname] = node.uri
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
                        cg = Graph()
                        cg.add((b, RPUBL.celexNummer, Literal(celex)))
                        celexuri = self.minter.space.coin_uri(cg.resource(b))
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
                                "%s is expired (time-limited) SFS" % basefile,
                                dummyfile=self.store.parsed_path(basefile))
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
                dat = datetime.strptime(val[:10], '%Y-%m-%d')
                d["rpubl:upphavandedatum"] = val[:10]
                if not self.config.keepexpired and dat < datetime.today():
                    raise UpphavdForfattning("%s is an expired SFS" % basefile,
                                             dummyfile=self.store.parsed_path(basefile))

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
        for k in attribs:
            if isinstance(attribs[k], dict):
                attribs[k] = self.sanitize_metadata(attribs[k], basefile)
            elif k in ("dcterms:creator", "rpubl:departement"):
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

        # move some data from the big document graph to a series of
        # small graphs, one for each change act.
        trash = set()
        for res in sorted(doc.meta.resource(doc.uri).objects(RPUBL.konsolideringsunderlag)):
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
                if p.identifier in (RPUBL.forarbete, RPUBL.genomforDirektiv):
                    if p.identifier == RPUBL.forarbete:
                        triple = (o, DCTERMS.identifier,
                                  doc.meta.value(o, DCTERMS.identifier))
                    elif p.identifier == RPUBL.genomforDirektiv:
                        triple = (o, RPUBL.celexNummer,
                                  doc.meta.value(o, RPUBL.celexNummer))
                    graph.add(triple)
                    trash.add(triple)
            rp = Registerpost(uri=res.identifier, meta=graph, id=identifier)
            reg.append(rp)
            if res.identifier in obs:
                rp.append(obs[uri])
        for triple in trash:
            doc.meta.remove(triple)
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
            bodystring = bodystring.decode(self.source_encoding)
        reader = TextReader(string=bodystring, linesep=TextReader.DOS)
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
    re_SearchSfsId = re.compile(r'\((\d{4}:\d+)\)').search
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
        return make_parser(sanitized, basefile, self.log, self.trace)

    def visitor_functions(self, basefile):
        return ((self.set_skipfragments, None),
                (self.construct_id, {'basefile': basefile}),
                (self.find_definitions, False))

    @decorators.action
    def importarchive(self, archivedir):
        """Imports downloaded data from an archive from legacy lagen.nu data.

        In particular, creates proper archive storage for older
        versions of each text.

        """
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

                if "vyear" in m.groupdict():  # this file is marked as
                                              # an archival version
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
                    de.title = "SFS %s" % basefile
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
                util.ensure_dir(dest)
                shutil.copy2(f, dest)
                break
            else:
                self.log.warning("Couldn't process %s" % f)
        self.log.info("Extracted %s current versions and %s archived versions"
                      % (current, archived))

