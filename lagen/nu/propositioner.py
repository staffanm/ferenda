# -*- coding: utf-8 -*-
from __future__ import unicode_literals, print_function

from ferenda.sources.legal.se import Propositioner as OrigPropositioner

class Propositioner(OrigPropositioner):
    def tabs(self, primary=False):
        return []
