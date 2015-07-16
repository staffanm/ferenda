# -*- coding: utf-8 -*-
from __future__ import unicode_literals, print_function

from ferenda.sources.legal.se import SOU as OrigSOU
from . import SameAs

class SOU(OrigSOU, SameAs):
    pass

