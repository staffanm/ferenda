# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from ferenda.sources.legal.se import SwedishLegalSource, RPUBL

class MyndPrax(SwedishLegalSource):
    """Wrapper repo like Forarbeten, but for ARN/JO/JK"""
    alias = "myndprax"
    def tabs(self, primary=False):
        return [("Myndighetspraxis", self.dataset_uri())]
