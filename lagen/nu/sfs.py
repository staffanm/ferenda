# -*- coding: utf-8 -*-
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
from builtins import *

import os
import re
import shutil
from datetime import datetime
from urllib.parse import quote, unquote

from html import unescape # on py2, use from HTMLParser import HTMLParser; unescape = HTMLParser().unescape
from rdflib import URIRef
from rdflib.namespace import DCTERMS, OWL, RDF, RDFS
from werkzeug.routing import Rule, BaseConverter

from ferenda.sources.legal.se import RPUBL, RINFOEX
from ferenda.sources.legal.se.swedishlegalsource import SwedishLegalHandler
from ferenda import decorators, util
from ferenda import TextReader, DocumentEntry, Describer, RequestHandler
from ferenda.sources.legal.se import SFS as OrigSFS
from ferenda.sources.legal.se.sfs import SFSHandler as OrigSFSHandler
from ferenda.sources.legal.se.elements import (Kapitel, Paragraf, Rubrik,
                                               Stycke, Listelement,
                                               Overgangsbestammelse, Bilaga,
                                               Avdelning, Underavdelning)
from . import SameAs


# class SFSHandler(RequestHandler):
class SFSHandler(OrigSFSHandler):
    # FIXME: write a nice set of rules here. the difficult thing will
    # be to only match SFS basefiles, but /<int>:<rest> ought to do it
    # maybe
    

    @property
    def doc_roots(self):
        return [""]
    

    def supports(self, environ):
        if environ['PATH_INFO'].startswith("/dataset/"):
            return super(SFSHandler, self).supports(environ)
        return re.match("/\d{4}\:", environ['PATH_INFO'])

    def _params(self, uri):
        m = re.search("(?P<basefile>\d{4}:(bih._?|)\d+(_?s\._?\d+|_\d|)+)/konsolidering/(?P<version>\d{4}:\d+)$", uri)
        if m:
            basefile = m.group('basefile').replace("_", " ")
            version = m.group('version')
            return basefile, version
        else:
            return None, None

    def path(self, uri):
        basefile, version = self._params(uri)
        if basefile and version:
            return self.repo.store.generated_path(basefile, version)
        else:
            return super(SFSHandler, self).path(uri)

    def params_from_uri(self, uri):
        assert False, "You should remove this and rely on the werkzeug routing rule"
        basefile, version = self._params(uri)
        if version:
            return {'version': version}
        else:
            return super(SFSHandler, self).params_from_uri(uri)
    
class SFS(OrigSFS, SameAs):
    requesthandler_class = SFSHandler
    
    def basefile_from_uri(self, uri):
        # this is a special version of
        # ferenda.sources.legal.se.SFS.basefile_from_uri that can
        # handle URIs with basefile directly under root, eg
        # <http://example.org/1992:123>
        if (uri.startswith(self.urispace_base) and
            re.match("\d{4}\:", uri[len(self.urispace_base)+1:])):
            basefile = uri[len(self.urispace_base)+1:]
            # remove any possible "/konsolidering/2015:123" trailing
            # info (unless the trailing info is /data, which is
            # specially handled by RequestHandler.lookup_resource
            if not basefile.endswith(("/data", "/data.rdf", "/data.ttl", "/data.nt")):
                basefile = basefile.split("/")[0]
            if "#" in basefile:
                basefile = basefile.split("#", 1)[0]
            elif basefile.endswith((".rdf", ".xhtml", ".json", ".nt", ".ttl")):
                basefile = basefile.rsplit(".", 1)[0]
            return basefile
        else:
            return super(SFS, self).basefile_from_uri(uri)

    # consider moving facets() and tabs() from OrigSFS to this
    ordinalpredicates = {
        Kapitel: "rpubl:kapitelnummer",
        Paragraf: "rpubl:paragrafnummer",
        Rubrik: "rinfoex:rubriknummer",
        Stycke: "rinfoex:styckenummer",
        Listelement: "rinfoex:punktnummer",
        Overgangsbestammelse: "rinfoex:andringsforfattningnummer",
        Bilaga: "rinfoex:bilaganummer",
        Avdelning: "rinfoex:avdelningnummer",
        Underavdelning: "rinfoex:underavdelningnummer"
    }

    def _makeimages(self):
        # FIXME: make sure a suitable font exists
        font = "Helvetica"

        def makeimage(basename, label):
            filename = "res/img/sfs/%s.png" % basename
            if not os.path.exists(filename):
                util.ensure_dir(filename)
                self.log.info("Creating img %s with label %s" %
                              (filename, label))
                cmd = 'convert -background transparent -fill gray50 -font %s -pointsize 10 -size 44x14 -gravity East label:"%s " %s' % (font, label, filename)
                util.runcmd(cmd)
            return filename
        ret = []
        for i in range(1, 150):
            for j in ('', 'a', 'b'):
                ret.append(makeimage("K%d%s" % (i, j), "%d%s kap." % (i, j)))
        for i in range(1, 100):
            ret.append(makeimage("S%d" % i, "%d st." % i))
        return ret

    def infer_metadata(self, resource, basefile):
        # remove the bogus dcterms:issued thing that we only added to
        # aid URI generation. NB: This is removed in the superclass'
        # postprocess_doc as well, because for this lagen.nu-derived
        # class it needs to be done at this point, but for use of the
        # superclass directly, it needs to be done at some point.
        for o in resource.objects(DCTERMS.issued):
            if not o.datatype:
                resource.remove(DCTERMS.issued, o)
        sameas_uri = self.sameas_minter.space.coin_uri(resource)
        resource.add(OWL.sameAs, URIRef(sameas_uri))
        resource.graph.add((URIRef(self.canonical_uri(basefile, True)),
                            OWL.sameAs, resource.identifier))
        # then find each rpubl:konsolideringsunderlag, and create
        # owl:sameas for them as well
        for subresource in resource.objects(RPUBL.konsolideringsunderlag):
            # sometimes there'll be a rpubl:konsolideringsunderlag to
            # a resource URI but no actual data about that
            # resource. This seems to happen if SFST is updated but
            # SFSR is not. In those cases we can't generate a
            # owl:sameAs URI since we have no other data about the
            # resource.
            if subresource.value(RDF.type):
                uri = self.sameas_minter.space.coin_uri(subresource)
                subresource.add(OWL.sameAs, URIRef(uri))
        desc = Describer(resource.graph, resource.identifier)
        de = DocumentEntry(self.store.documententry_path(basefile))
        if de.orig_updated:
            desc.value(RINFOEX.senastHamtad, de.orig_updated)
        if de.orig_checked:
            desc.value(RINFOEX.senastKontrollerad, de.orig_checked)
        rooturi = URIRef(desc.getrel(RPUBL.konsoliderar))

        v = self.commondata.value(rooturi, DCTERMS.alternate, any=True)
        if v:
            desc.value(DCTERMS.alternate, v)
        v = self.commondata.value(rooturi, RDFS.label, any=True)
        if v:
            # don't include labels if they're essentially the same as
            # dcterms:title (legalref needs it to be able to parse
            # refs to laws that typically don't include SFS numbers,
            # so that's why they're in sfs.ttl
            basetitle = str(resource.value(DCTERMS.title)).rsplit(" (")[0]
            if not v.startswith(basetitle.lower()):
                desc.value(RDFS.label, util.ucfirst(v))

    def tabs(self):
        if self.config.tabs:
            return [("Lagar", self.dataset_uri())]
        else:
            return []

    def frontpage_content_body(self):
        # it'd be nice if we could specify "X lagar, Y förordningar
        # och Z andra författningar" but the rdf:type of all documents
        # are rpubl:KonsolideradGrundforfattning. Maybe if we tweak
        # the facets we could do better
        return "%s författningar" % len(set([row['uri'] for row in self.faceted_data()]))


    templ = ['(?P<type>sfs[tr])/(?P<byear>\d+)/(?P<bnum>[^\-]+).html',
             '(?P<type>sfs[tr])/(?P<byear>\d+)/(?P<bnum>[^\-]+)-(?P<vfirst>first-version).html',
             '(?P<type>sfs[tr])/(?P<byear>\d+)/(?P<bnum>[^\-]+)-(?P<vyear>\d{4})-(?P<vnum>[^\-]+).html',
             '(?P<type>sfs[tr])/(?P<byear>\d+)/(?P<bnum>[^\-]+)-(?P<vyear>\d{4})-(?P<vnum>[^\-]+)-(?P<vcheck>checksum-[a-f0-9]+).html']
    @decorators.action
    def importarchive(self, archivedir, overwrite=False):
        """Imports downloaded data from an archive from legacy lagen.nu data.

        In particular, creates proper archive storage for older
        versions of each text.

        """

        def valid(f):
            size = os.path.getsize(f)
            if size == 0:
                return False
            with open(f, mode="rb") as fp:
                fp.seek(size-20)
                end_bytes = fp.read()
            end = end_bytes.decode(errors="ignore")
            return '</html>' in end

        def find_version(f):
            # need to look at the file to find out its version
            encoding = self._sniff_encoding(f)
            raw = open(f, 'rb').read(8000)
            text = unescape(raw.decode(encoding, errors="replace"))
            reader = TextReader(string=text)
            updated_to = self._find_uppdaterad_tom(basefile,
                                                   reader=reader)
            return updated_to
            
        current = archived = skipped = invalid = 0
        spares = {}
        recent_versions = {}  # records the current version of every
                              # basefile for which we have any archive
                              # file
        for f in util.list_dirs(archivedir, ".html"):
            if "downloaded/sfst" not in f:
                continue
            if os.path.getsize(f) == 0:
                continue
            for regex in self.templ:
                m = re.search(regex, f)
                if not m:
                    continue
                basefile = self.sanitize_basefile("%s:%s" % (m.group("byear"), m.group("bnum")))
                
                if "vyear" in m.groupdict():  # this file is marked as
                                              # an archival version
                    expected_version = self.sanitize_basefile("%s:%s" % (m.group("vyear"), m.group("vnum")))
                elif "vfirst" in m.groupdict(): 
                    expected_version = basefile
                else:
                    # if neither vyear or vfirst is in the filename,
                    # this is the very first version we have saved. It
                    # might be the first version, or it could be the
                    # first version that we were able to download. We
                    # just go with something and don't worry if it
                    # turns out to be wrong.
                    expected_version = basefile

                if os.path.getsize(f) == 0:
                    # we can't get any useful info from this file, but
                    # we can use it to trigger a selection of a spare,
                    # if available
                    this_version = expected_version
                else:
                    this_version = find_version(f)
                    if this_version != expected_version:
                        self.log.warning("%s@%s: Expected %s to be version %s" % (basefile, this_version, f, expected_version))
                    try:
                        sanitized_this_version = self.sanitize_basefile(this_version)
                    except:
                        self.log.error("%s@%s: Couldn't sanitize version found in %s" % (basefile, this_version, f))
                        break 
                    if this_version != sanitized_this_version:
                        self.log.warning("%s@%s: Version in %s sanitizes to %s" % (basefile, this_version, f, sanitized_this_version))
                        this_version = sanitized_this_version

                if "vcheck" in m.groupdict():
                    # these checksum variants should be older variants
                    # of a version we already have -- but in case the
                    # non-checksum version is empty or corrupted, we
                    # ought to use the best available checksum version
                    if valid(f):
                        spare_version = find_version(f)
                        spares[(basefile, spare_version)] = f
                    break

                if basefile not in recent_versions:
                    mainline = self.store.downloaded_path(basefile)
                    if os.path.exists(mainline):
                        recent_versions[basefile] = find_version(mainline)
                    else:
                        self.log.warning("%s@%s: archive file %s has no corresponding file in mainline (expected %s)" % (basefile, this_version, f, mainline))
                        current += 1
                        # but we'll create an archived version anyway, not one in mainline
                        recent_versions[basefile] = None
                if this_version == recent_versions[basefile]:
                    self.log.debug("%s@%s: file %s has same version as mainline" %
                                   (basefile, this_version, f))
                    break
                if valid(f):
                    source = f
                elif (basefile, this_version) in spares:
                    source = spares[(basefile, this_version)]
                    self.log.warning("%s@%s: using spare %s instead of invalid file %s" %
                                     (basefile, this_version, f, source))
                else:
                    self.log.error("%s@%s: file %s is invalid, and no spare is available" % 
                        (basefile, this_version, f))
                    invalid += 1
                    break
                dest = self.store.downloaded_path(basefile, version=this_version)
                if os.path.exists(dest) and not overwrite:
                    self.log.debug("%s@%s: Not extracting %s as %s already exists" %
                                  (basefile, this_version, f, dest))
                    skipped += 1
                else:
                    self.log.info("%s@%s: extracting %s to %s" %
                                  (basefile, this_version, f, dest))
                    util.ensure_dir(dest)
                    shutil.copy2(f, dest)
                    archived += 1
                break
            else:
                self.log.warning("Couldn't process %s" % f)
        self.log.info("Extracted %s current versions and %s archived versions (skipped %s files that already existed, and couldn't handle %s invalid versions)"
                      % (current, archived, skipped, invalid))

    @decorators.action
    def correct_archive_versions(self, archivedir=None):
        if not archivedir:
            archivedir = self.store.datadir
        store = DocumentStore(archivedir)
        # enumerate all basefiles for action parse
            # enumerate all basefile versions (maybe there's an iterator that does both?)
                # (if version is single digit)
                # check what version it probably is (_find_uppdaterad_tom)
                # stash rename the downloaded version to its correct version
                # for action in ('distill', 'entries', ...)
                     # stash rename that as well
        # execute all renames
        pass
