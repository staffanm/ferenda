# -*- coding: utf-8 -*-
from __future__ import unicode_literals, print_function

from rdflib import RDF
from rdflib.namespace import DCTERMS

from ferenda import Facet
from ferenda import fulltextindex
from ferenda.sources.legal.se import DV as OrigDV
from ferenda.sources.legal.se import RPUBL

class DV(OrigDV):
    
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
                      # dimension_label="main", # FIXME:
                      # dimension_label must be calculated as rdf_type
                      # or else the data from faceted_data() won't be
                      # usable by wsgi.stats
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
        return [("Domar", self.dataset_uri())]
