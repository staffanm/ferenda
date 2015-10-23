# -*- coding: utf-8 -*-
from __future__ import unicode_literals, print_function

from ferenda import CompositeRepository
from ferenda.sources.legal.se import Ds as OrigDs
from ferenda.sources.legal.se import SwedishLegalSource
from .regeringenlegacy import DsRegeringenLegacy
from . import SameAs


class DsRegeringen(OrigDs, SameAs):
    alias = "dsregeringen"
    pass


# We inherit from SwedishLegalSource to get at the custom tabs()
# implementation (that respects config.tabs)
class Ds(CompositeRepository, SwedishLegalSource):
    alias = "ds"
    subrepos = DsRegeringen, DsRegeringenLegacy
