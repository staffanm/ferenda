# -*- coding: utf-8 -*-
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
from builtins import *

from ferenda.sources.legal.se import Propositioner as OrigPropositioner
from ferenda.sources.legal.se.propositioner import PropRegeringen as OrigPropRegeringen
from ferenda.sources.legal.se.propositioner import PropTrips as OrigPropTrips
from ferenda.sources.legal.se.propositioner import PropRiksdagen as OrigPropRiksdagen
from .regeringenlegacy import PropRegeringenLegacy
from . import SameAs


# see motivation in sou.py for these seemingly pointless subclasses
class PropRegeringen(OrigPropRegeringen, SameAs): pass

class PropTrips(OrigPropTrips, SameAs): pass

class PropRiksdagen(OrigPropRiksdagen, SameAs): pass


class Propositioner(OrigPropositioner):
    subrepos = (PropRegeringen, PropRegeringenLegacy, PropTrips, PropRiksdagen)

