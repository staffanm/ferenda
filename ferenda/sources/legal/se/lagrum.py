# ersättare för simpleparse-baserade lagrumsparsern 
import os, sys
import logging
from pathlib import Path
from functools import cached_property
import codecs


from rdflib import URIRef, RDF, RDFS, Namespace, Literal, Graph, BNode
from lark import Lark, logger, Transformer, Token, Tree

# from ferenda.sources.legal.se.lagrum import LegalRef
from ferenda import ResourceLoader
from ferenda.thirdparty.coin import URIMinter
from ferenda.elements import Link


class LegalURITransformer(Transformer):

    def __init__(self, logger, uriformatter):
        print("o hai")



    def section_ref(self, subtrees):
        assert isinstance(subtrees[0], Tree), type(subtrees[0])
        refids = list(subtrees[0].scan_values(lambda x: isinstance(x, Token)))
        print("Got a section ref %s %s" % (refids[0], subtrees[1]))
        uri = self._legalref.sfs_format_uri({'section': refids[0]})
        print("Got a URI %s" % uri)
        return uri
        return "%s %s" % (refids[0], subtrees[1])

class RefParseError(Exception):
    pass


# This is intended to be somewhat API compatible with ferenda.sources.legal.se.legalref.LegalRef
class LegalRef:
    LAGRUM = 1             # hänvisningar till lagrum i SFS
    KORTLAGRUM = 2         # SFS-hänvisningar på kortform
    ENKLALAGRUM = 12       # Förenklad grammatik för de vanligaste hänvisningsformerna
    FORESKRIFTER = 3       # hänvisningar till myndigheters författningssamlingar
    EULAGSTIFTNING = 4     # EU-fördrag, förordningar och direktiv
    INTLLAGSTIFTNING = 5   # Fördrag, traktat etc
    FORARBETEN = 6         # proppar, betänkanden, etc
    RATTSFALL = 7          # Rättsfall i svenska domstolar
    MYNDIGHETSBESLUT = 8   # Myndighetsbeslut (JO, ARN, DI...)
    EURATTSFALL = 9        # Rättsfall i EU-domstolen/förstainstansrätten
    INTLRATTSFALL = 10     # Europadomstolen
    DOMSTOLSAVGORANDEN = 11# Underliggande beslut i ett rättsfallsreferat


    def __init__(self, *args, **kwargs):
        self.args = args
        if 'logger' in kwargs:
            self.log = kwargs['logger']
        else:
            self.log = logging.getLogger('lr')    

        self.log = logger
        self.log.info("LegalRef")

        from lagen.nu import SFS
        self._resourceloader = ResourceLoader(*ResourceLoader.make_loadpath(SFS()))
        # self.predicate = predicate
        self.minter = kwargs['minter']
        self._supported_sub_objects = ("sentence", "item",
                                       "itemnumeric", "piece",
                                       "element", "section",
                                       "chapter", "lawref",
                                       "avsnitt", "sida")
        # self.metadata_graph = metadata_graph
        # self.allow_relative = allow_relative
        
        self.namedlaws = {}

        if self.LAGRUM in args:
            grammarpath = self._resourceloader.filename("ebnf/lagrum.lark")
            grammar = Path(grammarpath).read_text(encoding="utf-8")
        self.parser = Lark(grammar, start="start", debug=True, propagate_positions=True)

    def reset(self):
        pass # FIXME figure out what needs to be resetted


    def get_relations(self, predicate, graph):
        d = {}
        for obj, subj in graph.subject_objects(predicate):
            d[str(subj)] = str(obj)
        return d

    def lagrum(self, subtrees):
        pass

    def parse(self,
              indata,
              minter,
              metadata_graph=None,
              baseuri_attributes=None,
              predicate=None,
              allow_relative=True):
        if indata == "":
            return indata
        self.baseuri_attributes=baseuri_attributes
        self.predicate = predicate
        self.allow_relative = allow_relative
        self.metadata_graph = metadata_graph
        if ((self.LAGRUM in self.args or
             self.KORTLAGRUM in self.args or
             self.ENKLALAGRUM in self.args) and not self.namedlaws):
                self.namedlaws.update(self.get_relations(RDFS.label,
                                                         self.metadata_graph))
        assert isinstance(indata, str)
        assert isinstance(minter, URIMinter)
        assert isinstance(metadata_graph, Graph)
        tree = self.parser.parse(indata)
        transformed = LegalURITransformer(logger=self.log).transform(tree)
        start = 0
        res = []
        for x in transformed._links:
            res.append(indata[start:x.meta.start_pos])
            res.append(Link(indata[x.meta.start_pos:x]))
        return res


if __name__ == "__main__":
    # logger.setLevel(logging.DEBUG)
    grammar = """
    _unwanted: /a+/
    wanted: "b"
    start: (wanted | _unwanted )+
    """
    grammar = Path("ferenda/sources/legal/se/lagrum.lark").read_text(encoding="utf-8")
    parser = Lark(grammar, start="start", debug=True, propagate_positions=True)
    text = "hänvisning till 4 § som är bra. en annan hänvisning till 5 § blahongalagen."
    # text = "baba"
    tree = parser.parse(text)
    print(tree.pretty())

    transformed = LegalURITransformer().transform(tree)
    start = 0
    res = []
    for x in transformed._links:
        res.append(text[start:x.meta.start_pos])
        res.append(Link(text[x.meta.start_pos:x]))

    print(list(hits))



