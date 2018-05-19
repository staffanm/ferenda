# -*- coding: utf-8 -*-
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
from builtins import *

import os
import re
import shutil
from datetime import datetime
from urllib.parse import quote, unquote
from wsgiref.util import request_uri

from rdflib import URIRef
from rdflib.namespace import DCTERMS, OWL, RDF
from ferenda.sources.legal.se import RPUBL, RINFOEX
from ferenda.sources.legal.se.swedishlegalsource import SwedishLegalHandler

from ferenda import decorators, util
from ferenda import TextReader, DocumentEntry, Describer, RequestHandler
from ferenda.sources.legal.se import SFS as OrigSFS
from ferenda.sources.legal.se import SFS as OrigSFS
from ferenda.sources.legal.se.elements import (Kapitel, Paragraf, Rubrik,
                                               Stycke, Listelement,
                                               Overgangsbestammelse, Bilaga,
                                               Avdelning, Underavdelning)
from . import SameAs

# class SFSHandler(RequestHandler):
class SFSHandler(SwedishLegalHandler):
    def supports(self, environ):
        if environ['PATH_INFO'].startswith("/dataset/"):
            return super(SFSHandler, self).supports(environ)
        return re.match("/\d{4}\:", environ['PATH_INFO'])

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
                cmd = 'convert -background transparent -fill Grey -font %s -pointsize 10 -size 44x14 -gravity East label:"%s " %s' % (font, label, filename)
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
        v = self.commondata.value(resource.identifier,
                                  DCTERMS.alternate, any=True)
        if v:
            desc.value(DCTERMS.alternate, v)

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


