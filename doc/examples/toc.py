# -*- coding: utf-8 -*-
from __future__ import unicode_literals

class TocExample(DocumentRepository):
# begin facets
    def facets(self):
        from ferenda import Facet
        return [Facet(self.ns['dcterms'].issued),
                Facet(self.ns['dcterms'].identifier)]
# end facets

# begin item
    def toc_item(self, binding, row):
        # note: look at binding to determine which pageset is being
        # constructed in case you want to present documents in
        # different ways depending on that.
        from ferenda.elements import Link
        return [row['identifier'] + ": ",
                Link(row['title'], 
                     uri=row['uri'])]
# end item

d = TocExample()
d.facets()
d.faceted_data()
d.toc_item(None, {'identifier':'x',
                  'title':'y',
                  'uri':'z'})

return_value = True
