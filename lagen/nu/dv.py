# -*- coding: utf-8 -*-
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
from builtins import *

from collections import Counter
from operator import attrgetter, index

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

    urispace_segment = "dom"
    
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
        domdesc.rel(DCTERMS.subject, self.keyword_uri(keyword))

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
    
    # Note: we only need to map those rattsfallspublikation labels
    # that differ from the court label (ie not Arbetsdomstolen,
    # Marknadsdomstolen, Miljööverdomstolen etc)

    _rattsfallspublikation_label = {"https://lagen.nu/dataset/hfd": "Högsta förvaltningsdomstolen",
                                    "https://lagen.nu/dataset/nja": "Högsta domstolen",
                                    "https://lagen.nu/dataset/mod": "Mark- och miljööverdomstolen", # FIXME: don't treat MÖD and MMD as identical
                                    "https://lagen.nu/dataset/ra": "Regeringsrätten",
                                    "https://lagen.nu/dataset/rh": "Hovrätterna",
                                    "https://lagen.nu/dataset/rk": "Kammarrätterna"}
    _rattsfallspublikation_order = ("Högsta domstolen", "Hovrätterna",
                                    "Högsta förvaltningsdomstolen", "Regeringsrätten",
                                    "Kammarrätterna", "Arbetsdomstolen", "Marknadsdomstolen", 
                                    "Mark- och miljööverdomstolen", "Migrationsöverdomstolen")
    def toc_pagesets(self, data, facets):
        # our primary facet is RPUBL.rattsfallspublikation, but we
        # need to create one pageset for each value thereof.
        pagesetdict = {}
        selector_values = {}
        facet = facets[0]  # should be the RPUBL.rattsfallspublikation one
        for row in data:
            pagesetid = row['rpubl_rattsfallspublikation']
            if pagesetid not in pagesetdict:
                # Get the preferred court label from our own mapping,
                # fall back to the skos:prefLabel of the publikation
                label = self._rattsfallspublikation_label.get(
                    row['rpubl_rattsfallspublikation'],
                    Facet.resourcelabel(row, 'rpubl_rattsfallspublikation',
                                        self.commondata))
                pagesetdict[pagesetid] = TocPageset(label=label,
                                                    predicate=pagesetid,
                                                    pages=[])
            selected = row['rpubl_arsutgava']
            selector_values[(pagesetid, selected)] = True

        for (pagesetid, value) in sorted(list(selector_values.keys()), reverse=True):
            pageset = pagesetdict[pagesetid]
            pageset.pages.append(TocPage(linktext=value,
                                         title="Rättsfall från %s under %s" % (pageset.label, value),
                                         binding=util.uri_leaf(pagesetid),
                                         value=value))

        # make sure pagesets are returned in the preferred, arbitrary order specified by _rattsfallspublikation_order
        return sorted(list(pagesetdict.values()), key=lambda x: self._rattsfallspublikation_order.index(x.label))

    def toc_select_for_pages(self, data, pagesets, facets):
        def idkey(row):
            k = util.split_numalpha(row['dcterms_identifier'])
            if " not " in row['dcterms_identifier']:
                k[0] = "~" + k[0] # ensure notisfall sorts last
            return k
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
            s = sorted(documents[(binding, value)], key=idkey)
            res[(binding, value)] = [self.toc_item(binding, row)
                                     for row in s]
        return res

    # this is a nicety that limits the sometimes very verbose case reporter titles 
    def toc_item_title(self, row):
        if 'rpubl_referatrubrik' not in row:
            self.log.warning("%s: No referatrubrik" % row['uri'])
            row['rpubl_referatrubrik'] = "(Referatrubrik saknas)"
        if len(row['rpubl_referatrubrik']) > 1000:
            return row['rpubl_referatrubrik'][:1000] + "..."
        return row['rpubl_referatrubrik']

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

    def tabs(self):
        return [("Rättsfall", self.dataset_uri())]

    def frontpage_content_body(self):
        c = Counter([row['rdf_type'] for row in self.faceted_data()])
        c2 = Counter([row['rpubl_rattsfallspublikation'] for row in self.faceted_data()])
        return ("%s rättsfallsreferat och %s notisfall från %s referatsserier" %
                (c['http://rinfo.lagrummet.se/ns/2008/11/rinfo/publ#Rattsfallsreferat'],
                 c['http://rinfo.lagrummet.se/ns/2008/11/rinfo/publ#Rattsfallsnotis'],
                 len(c2)))
