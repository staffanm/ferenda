# -*- coding: utf-8 -*-
from __future__ import unicode_literals, print_function

from ferenda.sources.legal.se import SOU as OrigSOU

class SOU(OrigSOU):
    def tabs(self, primary=False):
        return []
