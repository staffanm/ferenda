# -*- coding: utf-8 -*-
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
from builtins import *

from ferenda.sources.legal.se import JO as OrigJO
from . import SameAs

# This subclass is just so that the ResourceLoader picks up resources
# from lagen/nu/res
class JO(OrigJO, SameAs):
    pass
