# -*- coding: utf-8 -*-
from __future__ import unicode_literals, print_function

from ferenda.sources.legal.se import Direktiv as OrigDirektiv

class Direktiv(OrigDirektiv):
    def tabs(self, primary=False):
        return []
