# -*- coding: utf-8 -*-
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
from builtins import *

import re

from rdflib.namespace import SKOS

from . import Regeringen, RPUBL

# See SOU.py for discussion about possible other sources


class Ds(Regeringen):
    alias = "ds"
    re_basefile_strict = re.compile(r'Ds (\d{4}:\d+)')
    re_basefile_lax = re.compile(r'(?:Ds|) ?(\d{4}:\d+)', re.IGNORECASE)
    re_urlbasefile_strict = re.compile("departementsserien-och-promemorior/\d+/\d+/[a-z]*\.?-?(\d{4})(\d+)-?/$")
    re_urlbasefile_lax = re.compile("departementsserien-och-promemorior/\d+/\d+/.*?(\d{4})_?(\d+)")
    rdf_type = RPUBL.Utredningsbetankande
    document_type = Regeringen.DS
    urispace_segment = "utr/ds"

    # NB: The same logic as in
    # ferenda.sources.legal.se.{Regeringen,Riksdagen}.metadata_from_basefile
    def metadata_from_basefile(self, basefile):
        a = super(Ds, self).metadata_from_basefile(basefile)
        a["rpubl:arsutgava"], a["rpubl:lopnummer"] = basefile.split(":", 1)
        a["rpubl:utrSerie"] = self.lookup_resource("Ds", SKOS.altLabel)
        return a
