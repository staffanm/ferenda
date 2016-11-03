# -*- coding: utf-8 -*-
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
from builtins import *

from rdflib.namespace import SKOS, DCTERMS

from ferenda import CompositeRepository, CompositeStore, Facet
from ferenda.sources.legal.se import Ds as OrigDs
from ferenda.sources.legal.se import (SwedishLegalSource, SwedishLegalStore,
                                      RPUBL)
from .regeringenlegacy import DsRegeringenLegacy
from . import SameAs

class DsRegeringen(OrigDs, SameAs):
    alias = "dsregeringen"
    pass

# inherit list_basefiles_for from CompositeStore, basefile_to_pathfrag
# from SwedishLegalStore)
class DsStore(CompositeStore, SwedishLegalStore):
    pass

# We inherit from SwedishLegalSource to get at the custom tabs()
# implementation (that respects config.tabs)
class Ds(CompositeRepository, SwedishLegalSource):
    rdf_type = RPUBL.Utredningsbetankande
    alias = "ds"
    subrepos = DsRegeringen, DsRegeringenLegacy
    urispace_segment = "utr/ds"
    documentstore_class = DsStore
    xslt_template = "xsl/forarbete.xsl"
    sparql_annotations = "sparql/describe-with-subdocs.rq"

    # NB: The same logic as in
    # ferenda.sources.legal.se.{Regeringen,Riksdagen}.metadata_from_basefile
    def metadata_from_basefile(self, basefile):
        a = super(Ds, self).metadata_from_basefile(basefile)
        a["rpubl:arsutgava"], a["rpubl:lopnummer"] = basefile.split(":", 1)
        a["rpubl:utrSerie"] = self.lookup_resource("Ds", SKOS.altLabel)
        return a

    def facets(self):
        return super(Ds, self).facets() + [Facet(DCTERMS.title,
                                                       toplevel_only=False)]
