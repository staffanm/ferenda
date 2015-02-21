# -*- coding: utf-8 -*-
from __future__ import unicode_literals, print_function

from ferenda.sources.legal.se import Ds as OrigDs

class Ds(OrigDs):
    def tabs(self, primary=False):
        return []
