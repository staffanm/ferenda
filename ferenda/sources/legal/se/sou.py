# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import re

from ferenda import PDFAnalyzer

from . import Regeringen, RPUBL



# are there other sources? www.sou.gov.se directs here,
# anyway. Possibly
# https://www.riksdagen.se/Webbnav/index.aspx?nid=3282, but it's
# unsure whether they have any more information, or they just import
# from regeringen.se (the data quality suggests some sort of auto
# import). Some initial comparisons cannot find data that riksdagen.se
# has that regeringen.se doesn't

class SOUAnalyzer(PDFAnalyzer):
    # SOU running headers can contain quite a bit of text, 2 % 
    header_significance_threshold = 0.02
    # footers less so (but more than the default .2%), 1 %
    footer_significance_threshold = 0.01
    # the first two pages (3+ if the actual cover is included) are
    # atypical. A proper way of determining this would be to scan for
    # the first page stating "Till statsråded XX" using a title-ish
    # font.
    frontmatter = 2

    # don't count anything in the frontmatter - these margins are all off
    def count_vertical_textbox(self, pagenumber, textbox, counters):
        if pagenumber >= self.frontmatter:
            super(SOUAnalyzer, self).count_vertical_textbox(pagenumber, textbox, counters)

    def count_horizontal_textbox(self, pagenumber, textbox, counters):
        if pagenumber >= self.frontmatter:
            super(SOUAnalyzer, self).count_horizontal_textbox(pagenumber, textbox, counters)
    
class SOU(Regeringen):
    alias = "sou"
    re_basefile_strict = re.compile(r'SOU (\d{4}:\d+)')
    re_basefile_lax = re.compile(r'(?:SOU|) ?(\d{4}:\d+)', re.IGNORECASE)
    rdf_type = RPUBL.Utredningsbetankande
    document_type = Regeringen.SOU
    sparql_annotations = None # don't even bother creating an annotation file
