#!/usr/bin/env python
# -*- coding: iso-8859-1 -*-
import re
from . import Regeringen

# See SOU.py for discussion about possible other sources


class Ds(Regeringen):
    module_dir = "ds"
    re_basefile_strict = re.compile(r'Ds (\d{4}:\d+)')
    re_basefile_lax = re.compile(r'(?:Ds|) ?(\d{4}:\d+)', re.IGNORECASE)

    def __init__(self, options):
        super(Ds, self).__init__(options)
        self.document_type = self.DS
