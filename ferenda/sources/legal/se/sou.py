#!/usr/bin/env python
# -*- coding: iso-8859-1 -*-
import re
from . import Regeringen

# are there other sources? www.sou.gov.se directs here,
# anyway. Possibly
# https://www.riksdagen.se/Webbnav/index.aspx?nid=3282, but it's
# unsure whether they have any more information, or they just import
# from regeringen.se (the data quality suggests some sort of auto
# import). Some initial comparisons cannot find data that riksdagen.se
# has that regeringen.se doesn't


class SOU(Regeringen):
    module_dir = "sou"
    re_basefile_strict = re.compile(r'SOU (\d{4}:\d+)')
    re_basefile_lax = re.compile(r'(?:SOU|) ?(\d{4}:\d+)', re.IGNORECASE)

    def __init__(self, options):
        super(SOU, self).__init__(options)
        self.document_type = self.SOU
