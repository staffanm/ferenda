# -*- coding: utf-8 -*-
from __future__ import unicode_literals, print_function

from rdflib import Graph, Namespace, RDF, URIRef
from rdflib.namespace import OWL

from ferenda import ResourceLoader
from ferenda.thirdparty.coin import URIMinter


class SameAs(object):
    @property
    def sameas_minter(self):
        if not hasattr(self, '_sameas_minter'):
            # print("%s (%s) loading sameas_minter" % (self.alias, id(self)))
            # make a resourceloader that only loads resource from
            # superclasses, not this actual class. This'll make it
            # look in ferenda/sources/legal/se/res, not lagen/nu/res.
            loadpath = ResourceLoader.make_loadpath(self)
            rl = ResourceLoader(*loadpath[1:])
            spacefile = rl.filename("uri/swedishlegalsource.space.ttl")
            slugsfile = self.resourceloader.filename("uri/swedishlegalsource.slugs.ttl")
            # print("sameas: Loading URISpace from %s" % spacefile)
            # print("sameas: Loading Slugs from %s" % slugsfile)
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
                    
            cfg.parse(slugsfile, format="turtle")
            COIN = Namespace("http://purl.org/court/def/2009/coin#")
            # select correct URI for the URISpace definition by
            # finding a single coin:URISpace object
            spaceuri = cfg.value(predicate=RDF.type, object=COIN.URISpace)
            self._sameas_minter = URIMinter(cfg, spaceuri)
            # print("sameas: Created minter at %s" % id(self._sameas_minter))
        return self._sameas_minter

    def infer_metadata(self, resource, basefile):
        sup = super(SameAs, self)
        if hasattr(sup, 'infer_metadata'):
            sup.infer_metadata(resource, basefile)
        try:
            # since the resource is <main> rpubl:konsoliderar <sfs>,
            # and <sfs> is a URIRef not a BNode, this will fail
            sameas_uri = self.sameas_minter.space.coin_uri(resource)
            resource.add(OWL.sameAs, URIRef(sameas_uri))
        except ValueError as e:
            self.log.error("Couldn't mint owl:sameAs: %s" % e)
        return resource
