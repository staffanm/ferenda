# -*- coding: utf-8 -*-
from __future__ import unicode_literals, print_function

from ferenda.sources.legal.se import SOU as OrigSOU  # a compositerepo
from ferenda.sources.legal.se.sou import SOURegeringen as OrigSOURegeringen
from ferenda.sources.legal.se.sou import SOUKB as OrigSOUKB
from .regeringenlegacy import SOURegeringenLegacy
from . import SameAs


# these two class definitions only exists so that their ResourceLoader
# look under lagen/nu/res instead of ferenda/sources/legal/se/res
#
# however, since we have to do that, the main need for
# CompositeRepository.extrabases goes away...

class SOURegeringen(OrigSOURegeringen, SameAs): pass

class SOUKB(OrigSOUKB, SameAs): pass

class SOU(OrigSOU):
    subrepos = SOURegeringen, SOURegeringenLegacy, SOUKB
    # Since we had to subclass SOURegeringen and SOUKB in order to
    # correctly initialize their ResourceLoader, we have no real need
    # to specify extrabases here...
    # extrabases = SameAs,
