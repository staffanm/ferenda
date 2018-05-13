# maybe this should be a part of SameAs, so that these props are added
# to all lagen.nu-specific resources?

from ferenda import Describer, DocumentEntry
from ferenda.sources.legal.se import RINFOEX

class InferTimes(object):
    def infer_metadata(self, resource, basefile):
        super(InferTimes, self).infer_metadata(resource, basefile)
        desc = Describer(resource.graph, resource.identifier)
        de = DocumentEntry(self.store.documententry_path(basefile))
        if de.orig_updated:
            desc.value(RINFOEX.senastHamtad, de.orig_updated)
        if de.orig_checked:
            desc.value(RINFOEX.senastKontrollerad, de.orig_checked)
        
