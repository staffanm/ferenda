# -*- coding: utf-8 -*-
from __future__ import unicode_literals, print_function

from rdflib import Graph, Namespace, RDF, URIRef
from rdflib.namespace import OWL
from cached_property import cached_property


from ferenda import ResourceLoader
from ferenda.thirdparty.coin import URIMinter
from ferenda.sources.legal.se import RPUBL

class SameAs(object):
    @cached_property
    def sameas_minter(self):
        # make a resourceloader that only loads resource from
        # superclasses, not this actual class. This'll make it
        # look in ferenda/sources/legal/se/res, not lagen/nu/res.
        loadpath = ResourceLoader.make_loadpath(self)
        if "lagen/nu/" in loadpath[0]:
            loadpath = loadpath[1:]
        rl = ResourceLoader(*loadpath)
        spacefile = rl.filename("uri/swedishlegalsource.space.ttl")
        # print("sameas: Loading URISpace from %s" % spacefile)
        self.log.debug("Loading URISpace from %s" % spacefile)
        cfg = Graph().parse(spacefile, format="turtle")
        # slugs contains space:abbrSlug, but space contains
        # urispace:abbrSlug... We do a little translation
        src = URIRef("http://rinfo.lagrummet.se/sys/uri/space#abbrSlug")
        dst = URIRef("https://lagen.nu/sys/uri/space#abbrSlug")
        for (s, p, o) in cfg:
            if o == src:
                # print("Translating %s %s :abbrSlug" % (s.n3(), p.n3()))
                cfg.remove((s, p, o))
                cfg.add((s, p, dst))
            elif s == dst:
                # print("Translating :abbrSlug %s %s" % (p.n3(), o.n3()))
                cfg.remove((s, p, o))
                cfg.add((dst, p, o))
        slugsfile = self.resourceloader.filename("uri/swedishlegalsource.slugs.ttl")
        # self.log.debug("sameas: Loading slugs from %s" % slugsfile)
        cfg.parse(slugsfile, format="turtle")
        COIN = Namespace("http://purl.org/court/def/2009/coin#")
        # select correct URI for the URISpace definition by
        # finding a single coin:URISpace object
        spaceuri = cfg.value(predicate=RDF.type, object=COIN.URISpace)
        return URIMinter(cfg, spaceuri)

    def infer_metadata(self, resource, basefile):
        sup = super(SameAs, self)
        if hasattr(sup, 'infer_metadata'):
            sup.infer_metadata(resource, basefile)
        try:
            sameas_uri = self.sameas_minter.space.coin_uri(resource)
            resource.add(OWL.sameAs, URIRef(sameas_uri))
        except ValueError as e:
            self.log.error("Couldn't mint owl:sameAs: %s" % e)
        return resource
