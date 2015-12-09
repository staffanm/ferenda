# -*- coding: utf-8 -*-
from __future__ import unicode_literals, print_function

from rdflib.namespace import SKOS

from ferenda import CompositeRepository
from ferenda.sources.legal.se import Ds as OrigDs
from ferenda.sources.legal.se import SwedishLegalSource, RPUBL
from .regeringenlegacy import DsRegeringenLegacy
from . import SameAs


class DsRegeringen(OrigDs, SameAs):
    alias = "dsregeringen"
    pass


# We inherit from SwedishLegalSource to get at the custom tabs()
# implementation (that respects config.tabs)
class Ds(CompositeRepository, SwedishLegalSource):
    rdf_type = RPUBL.Utredningsbetankande
    alias = "ds"
    subrepos = DsRegeringen, DsRegeringenLegacy
    urispace_segment = "utr/ds"

    # NB: The same logic as in
    # ferenda.sources.legal.se.{Regeringen,Riksdagen}.metadata_from_basefile
    def metadata_from_basefile(self, basefile):
        a = super(Ds, self).metadata_from_basefile(basefile)
        a["rpubl:arsutgava"], a["rpubl:lopnummer"] = basefile.split(":", 1)
        a["rpubl:utrSerie"] = self.lookup_resource("Ds", SKOS.altLabel)
        return a
