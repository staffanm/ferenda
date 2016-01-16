# -*- coding: utf-8 -*-
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
from builtins import *

from ferenda.sources.legal.se import Direktiv as OrigDirektiv
from ferenda.sources.legal.se.direktiv import DirTrips as OrigDirTrips
from ferenda.sources.legal.se.direktiv import DirAsp as OrigDirAsp
from ferenda.sources.legal.se.direktiv import DirRegeringen as OrigDirRegeringen
from . import SameAs


class DirTrips(OrigDirTrips, SameAs):
    pass


class DirAsp(OrigDirAsp, SameAs):
    pass


class DirRegeringen(OrigDirRegeringen, SameAs):
    pass


class Direktiv(OrigDirektiv):
    subrepos = DirRegeringen, DirAsp, DirTrips
    extrabase = SameAs
    
