# All lagen.nu classes may inherit from this to make sure owl:sameAs statements are created

from ferenda.sources.legal.se import SwedishLegalSource

class LocalURI(SwedishLegalSource):
    def infer_triples(self, d, basefile=None):
        super(self, LocalURI).infer_triples(d,basefile)
        canonicalminter = ...
        sameas = self.canonicalminter(d)
        d.rel(OWL.sameAs, sameas)
        
