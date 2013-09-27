class TocExample(DocumentRepository):
# begin predicates
    def toc_predicates(self):
        return [self.ns['dct'].issued,
                self.ns['dct'].identifier]
# end predicates

# begin criteria
    def toc_criteria(self):
        return [TocCriteria(binding='title', # variable binding, see next step
                            label='Sorted by publication date',
                            pagetitle='Documents published in %(select)s',
                            selector=lambda x: x['issued'][:4],
                            key=lambda x: x['issued']),
                TocCriteria(binding='identifier', 
                            label='Sorted by identifier',
                            pagetitle='Documents starting with "%(select)s"',
                            selector=lambda x: x['identifier'][0],
                            key=lambda x: x['identifier'].lower())]
# end criteria

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
d.toc_predicates()
d.toc_criteria()
d.toc_item(None, {'identifier':'x',
                  'title':'y',
                  'uri':'z'})

return_value = True
