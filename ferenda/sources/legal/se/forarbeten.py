# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from rdflib import RDF

from ferenda import util
from ferenda import Facet, TocPageset, TocPage
from ferenda.elements import Link
from . import SwedishLegalSource, RPUBL


class Forarbeten(SwedishLegalSource):
    """This is a sort of a wrapper repo to provide useful tabs/tocs for set
    of related docrepos ("preparatory works")"""

    alias = "forarbeten"

    def tabs(self):
        return [("Förarbeten", self.dataset_uri())]

    def facet_query(self, context):
        # Override the standard query in order to ignore the default
        # context (provided by .dataset_uri()) since we're going to
        # look at  other docrepos' data
        return """PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX dcterms: <http://purl.org/dc/terms/>
PREFIX rpubl: <http://rinfo.lagrummet.se/ns/2008/11/rinfo/publ#>

SELECT DISTINCT ?uri ?rdf_type ?dcterms_title ?dcterms_identifier ?rpubl_utrSerie ?rpubl_arsutgava
WHERE {
    ?uri rdf:type ?type .
    OPTIONAL { ?uri rdf:type ?rdf_type . }
    OPTIONAL { ?uri dcterms:title ?dcterms_title . }
    OPTIONAL { ?uri dcterms:identifier ?dcterms_identifier . }
    OPTIONAL { ?uri rpubl:utrSerie ?rpubl_utrSerie . }
    OPTIONAL { ?uri rpubl:arsutgava ?rpubl_arsutgava . }
    FILTER (?type in (rpubl:Direktiv, rpubl:Utredningsbetankande, rpubl:Proposition)) .
} """

    def facets(self):
        labels = {'dir': 'Komittédirektiv',
                  'sou': 'SOU',
                  'ds': 'Ds',
                  'prop': 'Propositioner'}
        # rdf:type rpubl:Direktiv => "Kommittédirektiv"
        # rdf:type rpubl:Utredningsbetankande, rpubl:utrSerie .*sou => "SOU"
        # rdf:type rpubl:Utredningsbetankande, rpubl:utrSerie .*ds => "Ds"
        # rdf:type rpubl:Proposition => "Propositioner"
        def select(row, binding, extra):
            return labels[ident(row, binding, extra)]

        def ident(row, binding, extra):
            rdftype = row[binding]
            if rdftype == str(self.ns['rpubl'].Utredningsbetankande):
                if row['rpubl_utrSerie']:
                    # return "sou" or "ds", hopefully
                    return util.uri_leaf(row['rpubl_utrSerie'])
                else:
                    self.log.error("Row for %s is rpubl:Utredning but lacks rpubl:utrSerie" % row['uri'])
            elif rdftype == str(self.ns['rpubl'].Direktiv):
                return "dir"
            elif rdftype == str(self.ns['rpubl'].Proposition):
                return "prop"
            else:
                self.log.error("Row for %s has unrecognized type %s" % (row['uri'], row['rdf_type']))
        return [Facet(RDF.type,
                      selector=select,
                      identificator=ident),
                Facet(RPUBL.arsutgava)]

                      
        res = super(Forarbeten, self).facets()
        return res

    def toc_pagesets(self, data, facets):
        # FIXME: Main structure of this (create a two-level hierarchy
        # based on two different facets) mirrors the dv.py
        # toc_pagesets and could possibly be abstracted.
        pagesetdict = {}
        selector_values = {}
        for row in data:
            pagesetid = facets[0].identificator(row, 'rdf_type', None)
            label = facets[0].selector(row, 'rdf_type', None)
            pagesetdict[pagesetid] = TocPageset(label=label,
                                                predicate=pagesetid,  # ??
                                                pages=[])
            selected = facets[1].selector(row, 'rpubl_arsutgava', None)
            selector_values[(pagesetid, selected)] = True
        for (pagesetid, value) in sorted(list(selector_values.keys())):
            pageset = pagesetdict[pagesetid]
            pageset.pages.append(TocPage(linktext=value,
                                         title="%s från %s" % (pageset.label, value),
                                         binding=pagesetid,
                                         value=value))
        sortorder = {'prop': 1,
                     'sou': 2,
                     'ds': 3,
                     'dir': 4}
        return sorted(pagesetdict.values(), key=lambda ps: sortorder[ps.predicate])

    def toc_select_for_pages(self, data, pagesets, facets):
        def sortkey(doc):
            return util.split_numalpha(doc['dcterms_identifier'])
        # FIXME: Again, this mirrors the dv.py structure
        res = {}
        documents = {}
        for row in data:
            key = (facets[0].identificator(row, 'rdf_type', None),
                   facets[1].selector(row, 'rpubl_arsutgava', None))
            if key not in documents:
                documents[key] = []
            documents[key].append(row)
        pagesetdict = {}
        for pageset in pagesets:
            pagesetdict[pageset.predicate] = pageset
        for (binding, value) in sorted(documents.keys()):
            pageset = pagesetdict[binding]
            s = sorted(documents[(binding, value)], key=sortkey)
            res[(binding, value)] = [self.toc_item(binding, row)
                                     for row in s]
        return res

    def toc_item(self, binding, row):
        """Returns a formatted version of row, using Element objects"""
        return [Link(row['dcterms_identifier']+": "+row['dcterms_title'],
                     uri=row['uri'])]
