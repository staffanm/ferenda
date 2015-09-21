# -*- coding: utf-8 -*-
from __future__ import unicode_literals, print_function

from ferenda import CompositeRepository
from ferenda.sources.legal.se import Ds as OrigDs
from .regeringenlegacy import DsRegeringenLegacy
from . import SameAs

class DsRegeringen(OrigDs, SameAs):
    alias = "dsregeringen"
    pass


class Ds(CompositeRepository):
    alias = "ds"
    subrepos = DsRegeringen, DsRegeringenLegacy
