# -*- coding: utf-8 -*-

# this is a sort of a wrapper repo to provide useful tabs/tocs for set
# of related docrepos ("preparatory works")

from . import SwedishLegalSource

class Forarbete(SwedishLegalSource):
    alias = "forarbete"
    
    def tabs(self):
        return [("Förarbeten", self.dataset_uri())]

    def faceted_data(self):
        pass
        # to get a good toc

    def facets(self):
        pass
        # a number of common properties?
