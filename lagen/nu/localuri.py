# -*- coding: utf-8 -*-
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
from builtins import *

# All lagen.nu classes may inherit from this to make sure owl:sameAs
# statements are created. FIXME: This isn't used, or even finished...

from ferenda.sources.legal.se import SwedishLegalSource

class LocalURI(SwedishLegalSource):
    def infer_triples(self, d, basefile=None):
        super(LocalURI, self).infer_triples(d,basefile)
        canonicalminter = ...
        sameas = self.canonicalminter(d)
        d.rel(OWL.sameAs, sameas)
