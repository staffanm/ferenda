# -*- coding: utf-8 -*-
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
from builtins import *

from collections import Counter

from rdflib import RDF

from ferenda import util, fulltextindex
from ferenda import Facet, TocPageset, TocPage
from ferenda.elements import Link
from ferenda.sources.legal.se import SwedishLegalSource, RPUBL
from lagen.nu import Propositioner, SOU, Ds, Direktiv
from .facadesource import FacadeSource

class Forarbeten(FacadeSource, SwedishLegalSource):
    """This is a sort of a wrapper repo to provide useful tabs/tocs for set
    of related docrepos ("preparatory works")"""

    alias = "forarbeten"
    tablabel = "Förarbeten"
    subrepos = Propositioner, SOU, Ds, Direktiv

    namespaces = ['rdf', 'rdfs', 'xsd', 'dcterms', 'skos', 'foaf',
                  'xhv', 'xsi', 'owl', 'prov', 'bibo',
                  ('rpubl', 'http://rinfo.lagrummet.se/ns/2008/11/rinfo/publ#'),
                  ('rinfoex', 'http://lagen.nu/terms#')]

    # NB: The same logic as in
    # ferenda.sources.legal.se.{Regeringen,Riksdagen}.metadata_from_basefile
    def metadata_from_basefile(self, basefile):
        a = super(Forarbeten, self).metadata_from_basefile(basefile)
        a["rpubl:arsutgava"], a["rpubl:lopnummer"] = basefile.split(":", 1)
        return a
    
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
    FILTER (?type in (rpubl:Kommittedirektiv, rpubl:Utredningsbetankande, rpubl:Proposition)) .
} """

    def facets(self):
        labels = {'dir': 'Komittédirektiv',
                  'sou': 'SOU',
                  'ds': 'Ds',
                  'prop': 'Propositioner'}

        # rdf:type rpubl:Kommittedirektiv => "Kommittédirektiv"
        # rdf:type rpubl:Utredningsbetankande, rpubl:utrSerie .*sou => "SOU"
        # rdf:type rpubl:Utredningsbetankande, rpubl:utrSerie .*ds => "Ds"
        # rdf:type rpubl:Proposition => "Propositioner"
        def select(row, binding, extra):
            return labels[ident(row, binding, extra)]

        # This is a selector that can CLEARLY not run on arbitrary rows
        def ident(row, binding, extra):
            rdftype = row[binding]
            if rdftype == str(self.ns['rpubl'].Utredningsbetankande):
                if row['rpubl_utrSerie']:
                    leaf = util.uri_leaf(row['rpubl_utrSerie'])
                    if leaf.startswith("ds"):
                        return "ds"
                    elif leaf.startswith("sou"):
                        return "sou"
                    else:
                        assert leaf in ("sou", "ds"), "leaf was %s, unsure whether this is a SOU or a Ds." % leaf
                else:
                    self.log.error("Row for %s is rpubl:Utredning but lacks rpubl:utrSerie" % row['uri'])
            elif rdftype == str(self.ns['rpubl'].Kommittedirektiv):
                return "dir"
            elif rdftype == str(self.ns['rpubl'].Proposition):
                return "prop"
            else:
                pass
                # self.log.error("Row for %s has unrecognized type %s" % (row['uri'], row['rdf_type']))
        return [Facet(RDF.type,
                      selector=select,
                      identificator=ident),
                Facet(RPUBL.arsutgava,
                      indexingtype=fulltextindex.Label(),
                      selector_descending=True
                      )]

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
            try:
                selected = facets[1].selector(row, 'rpubl_arsutgava', None)
                selector_values[(pagesetid, selected)] = True
            except KeyError as e:
                self.log.error("Unable to sect from %r: %s" % row, e)
        for (pagesetid, value) in sorted(list(selector_values.keys()), reverse=True):
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

    def frontpage_content_body(self):
        # we could either count the number of items
        # self.store.list_basefiles_for("_postgenerate") returns or
        # count the number of unique docs in faceted_data. The latter
        # is prob more correct.
        c = Counter([row['rdf_type'] for row in self.faceted_data()])
        return ("%s propositioner, %s utredningsbetänkanden och %s kommittédirektiv" % (
            c['http://rinfo.lagrummet.se/ns/2008/11/rinfo/publ#Proposition'],
            c['http://rinfo.lagrummet.se/ns/2008/11/rinfo/publ#Utredningsbetankande'],
            c['http://rinfo.lagrummet.se/ns/2008/11/rinfo/publ#Kommittedirektiv']))
    
