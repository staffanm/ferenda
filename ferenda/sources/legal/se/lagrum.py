# ersättare för simpleparse-baserade lagrumsparsern 
import os, sys
import logging
from pathlib import Path
from functools import cached_property
import codecs
from functools import lru_cache
from collections import namedtuple

from rdflib import URIRef, RDF, RDFS, Namespace, Literal, Graph, BNode
from lark import Lark, logger, Transformer, Token, Tree
from rdflib.namespace import DCTERMS, SKOS

# from ferenda.sources.legal.se.lagrum import LegalRef
from ferenda import ResourceLoader
from ferenda.thirdparty.coin import URIMinter
from ferenda.elements import Link
from ferenda.sources.legal.se import RPUBL, RINFOEX

Match = namedtuple("Match", "uri, start, end")


class LegalURITransformer(Transformer):

    def __init__(self, logger, allow_relative, baseuri_attributes, minter):
        self.logger = logger
        self.allow_relative = allow_relative
        self.baseuri_attributes = baseuri_attributes
        self.minter = minter
        self._supported_sub_objects = ("sentence", "item",
                                       "itemnumeric", "piece",
                                       "element", "section",
                                       "chapter", "lawref",
                                       "avsnitt", "sida")
        self.links = []

    def section_ref(self, subtrees):
        assert isinstance(subtrees[0], Tree), type(subtrees[0])
        refids = list(subtrees[0].scan_values(lambda x: isinstance(x, Token)))
        # print("Got a section ref %s %s" % (refids[0], subtrees[1]))
        uri = self.sfs_format_uri({'section': refids[0]})
        # print("Got a URI %s" % uri)
        return Match(uri, subtrees[0].meta.start_pos, subtrees[1].end_pos)


    attributemap = {"year": RPUBL.arsutgava,
                    "no": RPUBL.lopnummer,
                    "lawref": RINFOEX.andringsforfattningnummer,
                    "chapter": RPUBL.kapitelnummer,
                    "section": RPUBL.paragrafnummer,
                    "element": RINFOEX.momentnummer,
                    "piece": RINFOEX.styckenummer,
                    "item": RINFOEX.punktnummer,
                    "itemnumeric": RINFOEX.punktnummer,
                    "sentence": RINFOEX.meningnummer,
                    "celex": RPUBL.celexNummer,
                    "artikel": RINFOEX.artikelnummer,
                    "sidnr": RPUBL.sidnummer,
                    "sida": RINFOEX.sidnummer, # yes, RINFOEX (to convert to rinfoex:sid)
                    "type": RDF.type,
                    "lopnr": RPUBL.lopnummer,
                    "notnr": RPUBL.lopnummer,
                    "rattsfallspublikation": RPUBL.rattsfallspublikation,
                    "domstol": RPUBL.rattsfallspublikation,
                    "ar": RPUBL.arsutgava,
                    "avsnitt": RINFOEX.avsnittnummer,
                    "utrSerie": RPUBL.utrSerie,
                    "myndighet": DCTERMS.publisher,
                    "diarienr": RPUBL.diarienummer
                    }

    def attributes_to_uri(self, attributes, rest=()):
        # make attributes into a hashable equivalent of a dict, so we
        # can use lru_cache
        tupleattributes = tuple(sorted(attributes.items()))
        return self.tuple_to_uri(tupleattributes, rest)

    @lru_cache(maxsize=None, typed=True)
    def tuple_to_uri(self, tupleattributes, rest):
        resource = self.attributes_to_resource(tupleattributes, rest)
    
        try:
            uri = self.minter.space.coin_uri(resource)
            # print("COULD mint URI from:\n%s" % resource.graph.serialize())
            return uri
        except ValueError:
            print("Couldn't mint URI from:\n%s" % resource.graph.serialize())
            return "https://example.org/404"
    
    def attributes_to_resource(self, attributes, rest=()):
        attributes = dict(attributes)
        g = Graph()
        g.bind("rpubl", RPUBL)
        g.bind("rinfoex", RINFOEX)
        b = BNode()
        current = b
        # firstly first, clean some degenerate attribute values
        for k in list(attributes.keys()):
            if not isinstance(attributes[k], URIRef):
                v = attributes[k]
                if v is None:
                    del attributes[k]
                    continue
                v = v.replace("\xa0", "") # Non-breakable space
                v = v.replace("\n", "")
                v = v.replace("\r", "")
                attributes[k] = v

        # then, try to create any needed sub-nodes representing
        # fragments of a document, starting with the most fine-grained
        # object. It is this subnode that we'll return in the end
        attributes_to_convert = self._supported_sub_objects

        for k in attributes_to_convert:
            if k in attributes:
                p = self.attributemap[k]
                rel = URIRef(str(p).replace("nummer", ""))
                g.add((current, p, Literal(attributes[k])))
                del attributes[k]
                new = BNode()
                g.add((new, rel, current))
                current = new

        # now, the remaining metadata must be attached to a top-level
        # object (representing a whole document)
        for k, v in attributes.items():
            if k in self.attributemap:
                if not isinstance(v, URIRef):
                    v = Literal(v)
                g.add((current, self.attributemap[k], v))
            else:
                # We know that these attribs do not need to be mapped
                # to RDF predicates (as equivalent information must
                # exist elsewhere)
                if k not in ("shortsection", "shortchapter"):
                    self.log.error("%s: Can't map attribute %s to RDF predicate" % (self.currentbasefile, k))

        # add any extra stuff
        for (p, o) in rest:
            g.add((current, p, o))
        return g.resource(b)

    def sfs_format_uri(self, attributes):
        if 'law' not in attributes and not self.allow_relative:
            return None
        piecemappings = {'första': '1',
                         'andra': '2',
                         'tredje': '3',
                         'fjärde': '4',
                         'femte': '5',
                         'sjätte': '6',
                         'sjunde': '7',
                         'åttonde': '8',
                         'nionde': '9'}
        attributeorder = ['law', 'chapter', 'section', 'element',
                          'piece', 'item', 'itemnumeric', 'sentence']
        # possibly complete attributes with data from
        # baseuri_attributes as needed
        if self.allow_relative:
            specificity = False
            for a in attributeorder:
                if a in attributes:
                    specificity = True  # don't complete further than this
                elif (not specificity) and a in self.baseuri_attributes:
                    attributes[a] = self.baseuri_attributes[a]
        # munge attributes a little further to be able to map to RDF
        if 'lawref' in attributes:
            # remove all other attribs except two
            attributes = {'law': attributes['law'],
                          'lawref': attributes['lawref']}
        if 'item' in attributes and 'piece' not in attributes:
            attributes['piece'] = '1'
        if "law" in attributes:
            attributes["year"], attributes["no"] = attributes["law"].split(":")
            del attributes["law"]
            if "s" in attributes["no"]:
                attributes["no"], attributes["sidnr"] = re.split(r"\s*s\.?\s*", attributes["no"])
        for k in attributes:
            if attributes[k] in piecemappings:
                attributes[k] = piecemappings[attributes[k]]

        # need also to add a rpubl:forfattningssamling triple -- i
        # think this is the place to do it. Problem is how we get
        # access to the URI for SFS -- it can be
        # <https://lagen.nu/dataset/sfs> or
        # <http://rinfo.lagrummet.se/serie/fs/sfs>. The information is
        # available in the config graph, which isn't easily
        # retrievable from self.minter. So we do it the hard way.
        rg = self.minter.space.templates[0].resource.graph
        # get the abbrSlug subproperty. FIXME: do this properly
        abbrSlug = rg.value(predicate=RDF.type, object=RDF.Property)
        fsuri = rg.value(predicate=abbrSlug, object=Literal("sfs"))
        assert fsuri, "Couldn't find URI for forfattningssamling 'sfs'"
        rest = ((RPUBL.forfattningssamling, fsuri),)
        return self.attributes_to_uri(attributes, rest)

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

        self.log.info("LegalRef")

        from lagen.nu import SFS
        self._resourceloader = ResourceLoader(*ResourceLoader.make_loadpath(SFS()))
        # self.predicate = predicate
        self.minter = kwargs['minter']

        # self.metadata_graph = metadata_graph
        # self.allow_relative = allow_relative
        self.baseuri_attributes={}
        self.predicate = None
        self.allow_relative = False
        self.metadata_graph = None
        
        self.namedlaws = {}

        if self.LAGRUM in args:
            grammarpath = self._resourceloader.filename("ebnf/lagrum.lark")
            grammar = Path(grammarpath).read_text(encoding="utf-8")
            
            transformer = LegalURITransformer(logger=self.log, 
                                              allow_relative=self.allow_relative,
                                              baseuri_attributes=self.baseuri_attributes,
                                              minter=self.minter)
            self.parser = Lark(grammar, start="start", parser="lalr", transformer=transformer,
                               propagate_positions=True)
        else:
            self.parser = None
        # FIXME: Do the rest possible args

    def parse(self,
              indata,
              minter,
              metadata_graph=None,
              baseuri_attributes=None,
              predicate=None,
              allow_relative=True):
        if indata == "":
            return [indata]
        if self.parser is None:
            return [indata]
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
        start = 0
        res = []
        match = None
        for match in tree.scan_values(lambda x: isinstance(x, Match)):
            res.append(indata[start:match.start])
            res.append(Link(indata[match.start:match.end], uri=match.uri))
        if match:
            res.append(indata[match.end:])
        return res



    def reset(self):
        pass # FIXME figure out what needs to be resetted


    def get_relations(self, predicate, graph):
        d = {}
        for obj, subj in graph.subject_objects(predicate):
            d[str(subj)] = str(obj)
        return d

    def lagrum(self, subtrees):
        pass




if __name__ == "__main__":
    # logger.setLevel(logging.DEBUG)
    grammar = """
    _unwanted: /a+/
    wanted: "b"
    start: (wanted | _unwanted )+
    """

    grammar = Path("../ferenda/sources/legal/se/res/ebnf/lagrum.lark").read_text(encoding="utf-8")
    configgraph = Graph()
    # FIXME: The configgraph should only be loaded once, but be
    # configurable ie load the correct COIN n3 config
    configgraph.parse("../ferenda/sources/legal/se/res/uri/swedishlegalsource.space.ttl", format="turtle")
    configgraph.parse("../ferenda/sources/legal/se/res/uri/swedishlegalsource.slugs.ttl", format="turtle")
    minter = URIMinter(configgraph,
                        URIRef("http://rinfo.lagrummet.se/sys/uri/space#"))
    transformer = LegalURITransformer(logger, allow_relative=True, 
                                      baseuri_attributes={'law': '9999:999'},
                                      minter=minter)
    parser = Lark(grammar, start="start", debug=True, propagate_positions=True, parser="lalr", transformer=transformer)
    
    text = "14 kr"
    # text = "baba"
    tree = parser.parse(text)
    print(tree.pretty())

    #transformed = LegalURITransformer().transform(tree)
    #start = 0
    #res = []
    #for match in transformed._links:
    #    res.append(text[start:match.end])
    #    res.append(Link(text[match.start, match.emdx.meta.start_pos:x]))




