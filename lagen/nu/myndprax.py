# -*- coding: utf-8 -*-
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
from builtins import *

from collections import Counter

from rdflib import URIRef
from rdflib.namespace import DCTERMS, FOAF

from . import ARN, JO, JK
from .facadesource import FacadeSource
from ferenda import Facet, TocPageset, TocPage
from ferenda import util
from ferenda.elements import Link
from ferenda.elements.html import Strong
from ferenda.sources.legal.se import SwedishLegalSource


# we inherit from SwedishLegalSource to pick up proper resources in
# self.commondata (and also the toc_item implementation)
class MyndPrax(FacadeSource, SwedishLegalSource):
    """Wrapper repo like Forarbeten, but for ARN/JO/JK"""
    alias = "myndprax"
    subrepos = ARN, JO, JK

    tablabel = "Praxis"

    def facet_query(self, context):
        # Override the standard query in order to ignore the default
        # context (provided by .dataset_uri()) since we're going to
        # look at  other docrepos' data
        return """PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX dcterms: <http://purl.org/dc/terms/>
PREFIX rpubl: <http://rinfo.lagrummet.se/ns/2008/11/rinfo/publ#>

SELECT DISTINCT ?uri ?dcterms_title ?dcterms_identifier ?dcterms_issued ?dcterms_publisher
WHERE {
    ?uri rdf:type rpubl:VagledandeMyndighetsavgorande .
    OPTIONAL { ?uri dcterms:title ?dcterms_title . }
    OPTIONAL { ?uri dcterms:identifier ?dcterms_identifier . }
    OPTIONAL { ?uri dcterms:issued ?dcterms_issued . }
    OPTIONAL { ?uri dcterms:publisher ?dcterms_publisher . }
} """

    def facets(self):
        resourcecache = {}
        def resourcename(row, binding, resource_graph):
            k = (row[binding], resource_graph.identifier)
            if k not in resourcecache:
                uri = URIRef(row[binding])
                resourcecache[k] = str(resource_graph.value(uri, FOAF.name))
            return resourcecache[k]
        
        return [Facet(DCTERMS.publisher,
                      selector=resourcename,
                      identificator=Facet.resourcelabel),
                Facet(DCTERMS.issued)]

    def toc_pagesets(self, data, facets):
        # FIXME: Main structure of this (create a two-level hierarchy
        # based on two different facets) mirrors the dv.py
        # toc_pagesets and could possibly be abstracted.
        pagesetdict = {}
        selector_values = {}
        for row in data:
            # should use a SKOS.altLabel?
            try:
                pagesetid = facets[0].identificator(row, 'dcterms_publisher',
                                                    self.commondata)
                label = facets[0].selector(row, 'dcterms_publisher',
                                           self.commondata)
                pagesetdict[pagesetid] = TocPageset(label=label,
                                                    predicate=pagesetid,  # ??
                                                    pages=[])
                selected = facets[1].selector(row, 'dcterms_issued', None)
                selector_values[(pagesetid, selected)] = True
            except KeyError as e:
                self.log.error("toc_pagesets: Couldn't process row %s: %s" % (row.get("uri"), e))
        for (pagesetid, value) in sorted(list(selector_values.keys())):
            pageset = pagesetdict[pagesetid]
            pageset.pages.append(
                TocPage(linktext=value,
                        title="%s från %s" % (pageset.label, value),
                        binding=pagesetid,
                        value=value))
        return sorted(pagesetdict.values())

    def toc_select_for_pages(self, data, pagesets, facets):
        def sortkey(doc):
            return util.split_numalpha(doc['dcterms_identifier'])
        # FIXME: Again, this mirrors the dv.py structure
        res = {}
        documents = {}
        for row in data:
            try:
                key = (facets[0].identificator(row, 'dcterms_publisher',
                                               self.commondata),
                       facets[1].selector(row, 'dcterms_issued',
                                          self.commondata))
                if key not in documents:
                    documents[key] = []
                documents[key].append(row)
            except KeyError as e:
                self.log.error("toc_select_for_pages: Couldn't process row %s: %s" % (row.get("uri"), e))
        pagesetdict = {}
        for pageset in pagesets:
            pagesetdict[pageset.predicate] = pageset
        for (binding, value) in sorted(documents.keys()):
            pageset = pagesetdict[binding]
            s = sorted(documents[(binding, value)], key=sortkey)
            res[(binding, value)] = [self.toc_item(binding, row)
                                     for row in s]
        return res

    def frontpage_content_body(self):
        c = Counter([row['dcterms_publisher'] for row in self.faceted_data()])
        return ("%s JO-beslut, %s JK-beslut och %s ARN-beslut" % (
            c['https://lagen.nu/org/2014/riksdagens_ombudsman'],
            c['https://lagen.nu/org/2014/justitiekanslern'],
            c['https://lagen.nu/org/2014/allmanna_reklamationsnamnden']))
