# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from ferenda import CompositeRepository


class FacadeSource(CompositeRepository):
    tablabel = "FacadeSource"  # subclasses override this

    def facet_query(self, context):
        pass

    def facets(self):
        pass

    def tabs(self):
        subtabs = [self.get_instance(c).tabs() for c in self.subrepos]
        return [(self.tablabel, None, subtabs)]
