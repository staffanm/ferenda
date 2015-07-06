# -*- coding: utf-8 -*-
from __future__ import unicode_literals, print_function

from rdflib import RDF, URIRef
from rdflib.namespace import DCTERMS, OWL

from ferenda import Facet, Describer
from ferenda import fulltextindex, util
from ferenda.sources.legal.se import legaluri
from ferenda.sources.legal.se import DV as OrigDV
from ferenda.sources.legal.se import RPUBL
from ferenda.sources.legal.se.legalref import LegalRef
from . import SameAs


class DV(OrigDV, SameAs):

    @property
    def commondata(self):
        # for parsing, our .commondata needs to access named laws
        # defined in extra/sfs.ttl. Make sure these are loaded even
        # though we don't inherit from SFS.
        if not hasattr(self, '_commondata'):
            self._commondata = super(DV, self).commondata
            path = "extra/sfs.ttl"
            if self.resourceloader.exists(path):
                with self.resourceloader.open(path) as fp:         
                    self._commondata.parse(data=fp.read(), format="turtle")
        return self._commondata
        
    def add_keyword_to_metadata(self, domdesc, keyword):

        def sokord_uri(value):
            # FIXME: This should coined by self.minter
            baseuri = "https://lagen.nu/concept/"
            return baseuri + util.ucfirst(value).replace(' ', '_')

        domdesc.rel(DCTERMS.subject, sokord_uri(keyword))

    # override polish_metadata to add some extra owl:sameAs attributes
    def polish_metadata(self, head):

        # where do we get refdesc, domdesc?
        coin_uri = self.sameas_minter.space.coin_uri
        resource = super(DV, self).polish_metadata(head)
        refuri = resource.identifier
        domuri = resource.value(RPUBL.referatAvDomstolsavgorande).identifier
        refuri_sameas = coin_uri(resource)
        domuri_sameas = coin_uri(resource.value(RPUBL.referatAvDomstolsavgorande))
        resource.graph.add((URIRef(refuri), OWL.sameAs, URIRef(refuri_sameas)))
        resource.graph.add((URIRef(domuri), OWL.sameAs, URIRef(domuri_sameas)))
        return resource

#         if '_nja_ordinal' in head:
#             # <sidnummer-based> owl:sameAs <lopnummer based>
#             altattribs = {'type': LegalRef.RATTSFALL,
#                           'rattsfallspublikation': 'nja',
#                           'arsutgava': refdesc.getvalue(RPUBL.arsutgava),
#                           'lopnummer': refdesc.getvalue(RPUBL.lopnummer)}
#             refdesc.rel(OWL.sameAs, legaluri.construct(altattribs))
#         else:
#             # Canonical URIs are based on lopnummer. add a sameas ref
#             # back to the sidnummer based URI
#             altattribs = {'type': LegalRef.RATTSFALL,
#                           'rattsfallspublikation': 'nja',
#                           'arsutgava': refdesc.getvalue(RPUBL.arsutgava),
#                           'lopnummer': refdesc.getvalue(RPUBL.sidnummer)}
#             refdesc.rel(OWL.sameAs, legaluri.construct(altattribs))



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
                      selector=myselector,  # => ("ad","2001"), ("nja","1981")
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
