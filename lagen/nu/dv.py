# -*- coding: utf-8 -*-
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
from builtins import *

from collections import Counter
from operator import attrgetter

from rdflib import RDF, URIRef, BNode, Graph
from rdflib.namespace import DCTERMS, OWL, RDFS
from cached_property import cached_property

from ferenda import Facet, Describer, TocPageset, TocPage, Feed, Feedset
from ferenda import fulltextindex, util
from ferenda.elements import Link
from ferenda.elements.html import Strong
from ferenda.sources.legal.se import DV as OrigDV
from ferenda.sources.legal.se import RPUBL
from . import SameAs


class DV(OrigDV, SameAs):

    @cached_property
    def commondata(self):
        # for parsing, our .commondata needs to access named laws
        # defined in extra/sfs.ttl. Make sure these are loaded even
        # though we don't inherit from SFS.
        cd = super(DV, self).commondata
        path = "extra/sfs.ttl"
        if self.resourceloader.exists(path):
            with self.resourceloader.open(path) as fp:
                cd.parse(data=fp.read(), format="turtle")
        return cd
        
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
        refuri_sameas = coin_uri(resource)
        resource.graph.add((URIRef(refuri), OWL.sameAs, URIRef(refuri_sameas)))
        # NB: In theory, we have all the data we need to generate a
        # canonical URI for the dom. In practice, this data does not
        # meet requirements of our URISpace templates in certain cases
        # (all MD verdicts use rpubl:domsnummer instead of
        # rpubl:malnummer, which is what the template expects. The
        # superclass' definition of polish_metadata gets around this
        # by creating a minimal graph from the plain dict in head and
        # feeds that to coin_uri. So we do the same here, instead of
        # the very simple:
        #
        #    domuri_sameas = coin_uri(resource.value(RPUBL.referatAvDomstolsavgorande))
        #
        # (also, this version handles the uncommon but valid case
        # where one referat concerns multiple dom:s)
        domuri = resource.value(RPUBL.referatAvDomstolsavgorande).identifier 
        for malnummer in head['_localid']:
            bnodetmp = BNode()
            gtmp = Graph()
            gtmp.bind("rpubl", RPUBL)
            gtmp.bind("dcterms", DCTERMS)
            dtmp = Describer(gtmp, bnodetmp)
            dtmp.rdftype(RPUBL.VagledandeDomstolsavgorande)
            dtmp.value(RPUBL.malnummer, malnummer)
            dtmp.value(RPUBL.avgorandedatum, head['Avgörandedatum'])
            dtmp.rel(DCTERMS.publisher, self.lookup_resource(head["Domstol"]))
            rtmp = dtmp.graph.resource(bnodetmp)
            domuri_sameas = coin_uri(rtmp)
            resource.graph.add((URIRef(domuri), OWL.sameAs, URIRef(domuri_sameas)))
        return resource

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
                      identificator=lambda x, y, z: None),
                self.labelfacet
                ]

    def _relate_fulltext_value(self, facet, resource, desc):
        def rootlabel(desc):
            return desc.getvalue(DCTERMS.identifier)
        if facet.rdftype == RDFS.label:
            if "#" in resource.get("about"):
                rooturi = resource.get("about").split("#")[0]
                oldabout = desc._current()
                desc.about(rooturi)
                v = rootlabel(desc)
                desc.about(oldabout)
                if desc.getvalue(DCTERMS.creator):
                    court = desc.getvalue(DCTERMS.creator)
                else:
                    court = resource.get("about").split("#")[1]
                v = "%s (%s)" % (v, court)
            else:
                v = rootlabel(desc)
            # print("%s -> %s" % (resource.get("about"), v))
            return None, v
        else:
            return super(DV, self)._relate_fulltext_value(facet, resource, desc
)
    
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
            r.append(": ")
            r.append(row['rpubl_referatrubrik'])
        return r

    def tabs(self):
        return [("Domar", self.dataset_uri())]

    def frontpage_content_body(self):
        c = Counter([row['rdf_type'] for row in self.faceted_data()])
        c2 = Counter([row['rpubl_rattsfallspublikation'] for row in self.faceted_data()])
        return ("%s rättsfallsreferat och %s notisfall från %s referatsserier" %
                (c['http://rinfo.lagrummet.se/ns/2008/11/rinfo/publ#Rattsfallsreferat'],
                 c['http://rinfo.lagrummet.se/ns/2008/11/rinfo/publ#Rattsfallsnotis'],
                 len(c2)))
