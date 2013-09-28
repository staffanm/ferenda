# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import re


from . import Regeringen, RPUBL

# See SOU.py for discussion about possible other sources


class Ds(Regeringen):
    alias = "ds"
    re_basefile_strict = re.compile(r'Ds (\d{4}:\d+)')
    re_basefile_lax = re.compile(r'(?:Ds|) ?(\d{4}:\d+)', re.IGNORECASE)
    rdf_type = RPUBL.Utredning
    document_type = Regeringen.DS
