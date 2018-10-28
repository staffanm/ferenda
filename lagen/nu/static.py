from ferenda import Facet
from ferenda import fulltextindex
from ferenda.sources.general import Static as BaseStatic
from ferenda.sources.legal.se import SwedishLegalSource
from rdflib import Namespace
from rdflib.namespace import DCTERMS, RDFS, RDF
PROV = Namespace("http://www.w3.org/ns/prov#")


# interit from SwedishLegalSource to get custom relate() impl (that indexes label, creator and issued)
class Static(BaseStatic, SwedishLegalSource):

    required_predicates = [RDF.type, DCTERMS.title, PROV.wasGeneratedBy]
    urispace_segment = "om"
    
    def canonical_uri(self, basefile, version=None):
        return "https://lagen.nu/om/%s" % basefile
    
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

    def footer(self):
        res = super(Static, self).footer()
        res.append(("Hostas av Kodapan", "http://kodapan.se/"))
        return res
