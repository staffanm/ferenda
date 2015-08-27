# -*- coding: utf-8 -*-
from __future__ import unicode_literals, print_function

from ferenda.sources.legal.se import Propositioner as OrigPropositioner
from ferenda.sources.legal.se.propositioner import PropRegeringen as OrigPropRegeringen
from . import SameAs


class PropRegeringen(OrigPropRegeringen, SameAs):
    pass


class Propositioner(OrigPropositioner):
    extrabases = (SameAs,)
