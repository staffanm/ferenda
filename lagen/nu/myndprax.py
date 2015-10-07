# -*- coding: utf-8 -*-
from __future__ import unicode_literals

# from ferenda.sources.legal.se import SwedishLegalSource, RPUBL
from .facadesource import FacadeSource
from . import ARN, JO, JK


class MyndPrax(FacadeSource):
    """Wrapper repo like Forarbeten, but for ARN/JO/JK"""
    alias = "myndprax"
    subrepos = ARN, JO, JK

    tablabel = "Myndighetspraxis"
    
