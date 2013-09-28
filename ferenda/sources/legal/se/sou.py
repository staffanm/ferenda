# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import re

from . import Regeringen, RPUBL

# are there other sources? www.sou.gov.se directs here,
# anyway. Possibly
# https://www.riksdagen.se/Webbnav/index.aspx?nid=3282, but it's
# unsure whether they have any more information, or they just import
# from regeringen.se (the data quality suggests some sort of auto
# import). Some initial comparisons cannot find data that riksdagen.se
# has that regeringen.se doesn't


class SOU(Regeringen):
    alias = "sou"
    re_basefile_strict = re.compile(r'SOU (\d{4}:\d+)')
    re_basefile_lax = re.compile(r'(?:SOU|) ?(\d{4}:\d+)', re.IGNORECASE)
    rdf_type = RPUBL.Utredning
    document_type = Regeringen.SOU
