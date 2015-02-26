# -*- coding: utf-8 -*-
from __future__ import unicode_literals, print_function

# from ferenda.sources.legal.se import MyndFskr as OrigMyndFskr
from ferenda.sources.legal.se import myndfskr
from ferenda import CompositeRepository, Facet, TocPageset, TocPage
from ferenda import util
from ferenda.elements import Link
from ferenda.sources.legal.se import SwedishLegalSource

from rdflib import RDF
from rdflib.namespace import DCTERMS
from ferenda.sources.legal.se import RPUBL

# class MyndFskr(OrigMyndFskr):
#     pass

# might need to override CompositeStore to provide better
# basefile_to_pathfrag implemnentation

class CompositeMyndFskr(CompositeRepository, SwedishLegalSource):
    alias = "myndfs"
    subrepos = [
        myndfskr.AFS,
        myndfskr.BOLFS,
        myndfskr.DIFS,
        myndfskr.DVFS,
        myndfskr.EIFS,
        myndfskr.ELSAKFS,
        myndfskr.Ehalso,
        myndfskr.FFFS,
        myndfskr.FFS,
        myndfskr.FMI,
        myndfskr.FoHMFS,
        myndfskr.KFMFS,
        myndfskr.KOVFS,
        myndfskr.KVFS,
        myndfskr.LIFS,
        myndfskr.LMFS,
        myndfskr.LVFS,
        myndfskr.MIGRFS,
        myndfskr.MRTVFS,
        myndfskr.MSBFS,
        myndfskr.MYHFS,
        myndfskr.NFS,
        myndfskr.RAFS,
        myndfskr.RGKFS,
        myndfskr.RNFS,
        myndfskr.SJVFS,
        myndfskr.SKVFS,
        myndfskr.SOSFS,
        myndfskr.STAFS,
        myndfskr.STFS,
        myndfskr.SvKFS,
    ]
    rdf_type = (RPUBL.Myndighetsforeskrift, RPUBL.AllmannaRad)
    namespaces = ['rdf', 'rdfs', 'xsd', 'dcterms', 'skos', 'foaf',
                  'xhv', 'xsi', 'owl', 'prov', 'bibo',
                  ('rpubl', 'http://rinfo.lagrummet.se/ns/2008/11/rinfo/publ#'),
                  ('rinfoex', 'http://lagen.nu/terms#')]


    
    def facets(self):
        return [Facet(RPUBL.forfattningssamling,
                      selector=Facet.resourcelabel,
                      identificator=Facet.term,
                      use_for_toc=True),
                Facet(RPUBL.arsutgava,
                      use_for_toc=True),
                Facet(RDF.type, use_for_toc=False),
                Facet(DCTERMS.title, use_for_toc=False),
                Facet(DCTERMS.publisher, use_for_toc=False),
                Facet(DCTERMS.identifier)]

    def toc_pagesets(self, data, facets):
        # FIXME: Main structure of this (create a two-level hierarchy
        # based on two different facets) mirrors the dv.py
        # toc_pagesets and could possibly be abstracted.
        pagesetdict = {}
        selector_values = {}
        for row in data:
            pagesetid = facets[0].identificator(row,
                                                'rpubl_forfattningssamling',
                                                self.commondata)
            label = facets[0].selector(row, 'rpubl_forfattningssamling', self.commondata)
            pagesetdict[pagesetid] = TocPageset(label=label,
                                                predicate=pagesetid,  # ??
                                                pages=[])
            selected = facets[1].selector(row, 'rpubl_arsutgava', self.commondata)
            selector_values[(pagesetid, selected)] = True
        for (pagesetid, value) in sorted(list(selector_values.keys())):
            pageset = pagesetdict[pagesetid]
            pageset.pages.append(TocPage(linktext=value,
                                         title="%s från %s" % (pageset.label, value),
                                         binding=pagesetid,
                                         value=value))
        return pagesetdict.values()

    def toc_select_for_pages(self, data, pagesets, facets):
        def sortkey(doc):
            return util.split_numalpha(doc['dcterms_identifier'])
        # FIXME: Again, this mirrors the dv.py structure
        res = {}
        documents = {}
        for row in data:
            key = (facets[0].identificator(row, 'rpubl_forfattningssamling', self.commondata),
                   facets[1].selector(row, 'rpubl_arsutgava', self.commondata))
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
        # more defensive version of DocumentRepository.toc_item
        label = ""
        if 'dcterms_identifier' in row:
            label = row['dcterms_identifier']
        else:
            self.log.warning("No dcterms:identifier for %s" % row['uri'])
            
        if 'dcterms_title' in row:
            label += ": " + row['dcterms_title']
        else:
            self.log.warning("No dcterms:title for %s" % row['uri'])
            label = "URI: " + row['uri']
        return [Link(label, uri=row['uri'])]

    def tabs(self):
        return [("Myndighetsföreskrifter", self.dataset_uri())]
