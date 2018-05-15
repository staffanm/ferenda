# -*- coding: utf-8 -*-
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
from builtins import *

from rdflib import Graph, Namespace, RDF, URIRef, Literal
from rdflib.namespace import OWL, DCTERMS
from cached_property import cached_property

from ferenda import util
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
        with open(spacefile) as space:
            cfg = Graph().parse(space, format="turtle")
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
        with open(slugsfile) as slugs:
            cfg.parse(slugs, format="turtle")
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
            # a slight problem when minting sameas uris for
            # :KonsolideradGrundforfattning documents (at least in
            # myndfskr): These RDF resources contain a
            # rpubl:konsoliderar triple that has a URIRef (not a
            # BNode) as object. That URIRef uses a https://lagen.nu/
            # prefix. coin.Template.get_base/guarded_base expects that
            # such URIRefs should start with the base prefix, which
            # for the sameas minter is http://rinfo.lagrummet.se/. So
            # it falls back to trying to recursively mint the URI for
            # the base act, which fails because the RDF resource
            # doesn't contain all needed triples. The way to fix this
            # is probably to identify any rpubl:konsoliderar triples
            # and munge the URIRef before passing it to the sameas
            # minter.
            k = resource.value(RPUBL.konsoliderar)
            temp_issued = None
            if k and str(k.identifier).startswith(self.minter.space.base):
                newuri = str(k.identifier).replace(self.minter.space.base,
                                                   self.sameas_minter.space.base)
                resource.remove(RPUBL.konsoliderar)
                resource.add(RPUBL.konsoliderar, URIRef(newuri))
                if not resource.value(DCTERMS.issued):
                    temp_issued = Literal(self.consolidation_date(basefile))
                    resource.add(DCTERMS.issued, temp_issued)
            sameas_uri = self.sameas_minter.space.coin_uri(resource)
            if temp_issued:
                resource.remove(DCTERMS.issued, temp_issued)
            resource.add(OWL.sameAs, URIRef(sameas_uri))
        except ValueError as e:
            self.log.error("Couldn't mint owl:sameAs: %s" % e)
        return resource

    def keyword_uri(self, keyword):
        baseuri = "https://lagen.nu/begrepp/"
        return baseuri + util.ucfirst(keyword).replace(' ', '_').replace('"', "%22").replace("»", "//")

