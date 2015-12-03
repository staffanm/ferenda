# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import re

from rdflib.namespace import SKOS

from . import Regeringen, RPUBL

# See SOU.py for discussion about possible other sources


class Ds(Regeringen):
    alias = "ds"
    re_basefile_strict = re.compile(r'Ds (\d{4}:\d+)')
    re_basefile_lax = re.compile(r'(?:Ds|) ?(\d{4}:\d+)', re.IGNORECASE)
    rdf_type = RPUBL.Utredningsbetankande
    document_type = Regeringen.DS

    def canonical_uri(self, basefile):
        year, ordinal = basefile.split(":")
        attrib = {'rpubl:arsutgava': year,
                  'rpubl:lopnummer': ordinal,
                  'rpubl:utrSerie': self.lookup_resource("Ds", SKOS.altLabel),
                  'rdf:type': self.rdf_type}
        resource = self.attributes_to_resource(attrib)
        return self.minter.space.coin_uri(resource) 
