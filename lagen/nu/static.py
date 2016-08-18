from ferenda import Facet
from ferenda import fulltextindex
from ferenda.sources.general import Static as BaseStatic
from ferenda.sources.legal.se import SwedishLegalSource
from rdflib.namespace import DCTERMS, RDFS

# interit from SwedishLegalSource to get custom relate() impl (that indexes label, creator and issued)
class Static(BaseStatic, SwedishLegalSource):

    def canonical_uri(self, basefile):
        return "https://lagen.nu/%s" % basefile
    
    def facets(self):
        # The facets of a repo control indexing, particularly the
        # synthesized 'label', 'creator' and 'issued'. By only
        # defining a facet for 'label' we avoid having to define
        # issued and creator for static pages. Or maybe we should try
        # to do that?
        return [Facet(RDFS.label,
                      use_for_toc=False,
                      use_for_feed=False,
                      toplevel_only=False,
                      dimension_label="label",
                      dimension_type="value",
                      multiple_values=False,
                      indexingtype=fulltextindex.Label(boost=16))]
        
    def _relate_fulltext_value_rootlabel(self, desc):
        return "Lagen.nu: %s" % desc.getvalue(DCTERMS.title)
