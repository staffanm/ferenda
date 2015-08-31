# -*- coding: utf-8 -*-
from __future__ import unicode_literals, print_function

from ferenda.sources.legal.se import SOU as OrigSOU  # a compositerepo
from ferenda.sources.legal.se.sou import SOURegeringen, SOUKB
from .regeringenlegacy import SOURegeringenLegacy
from . import SameAs


class SOU(OrigSOU):
    subrepos = SOURegeringen, SOURegeringenLegacy, SOUKB
    extrabases = SameAs,
    
