# -*- coding: utf-8 -*-
from __future__ import unicode_literals
"""This module constructs URIs for a document based on the properties
of that document. Alternatively, given a URI for a document, parse the
different properties for the document"""

# system libs
import re

# 3rdparty libs

from rdflib import Literal
from rdflib import Namespace
from rdflib import URIRef
from rdflib import RDF
from rdflib import Graph
from rdflib import BNode


# my own libraries
from ferenda.sources.legal.se.legalref import LegalRef
from ferenda import util

RPUBL = Namespace('http://rinfo.lagrummet.se/ns/2008/11/rinfo/publ#')
RINFOEX = Namespace("http://lagen.nu/terms#")
DCT = Namespace(util.ns['dct'])

# Maps keys used by the internal dictionaries that LegalRef
# constructs, which in turn are modelled after production rule names
# in the EBNF grammar.
predicate = {"type": RDF.type,
             "publikation": RPUBL["rattsfallspublikation"],
             "artal": RPUBL["artal"],
             "lopnummer": RPUBL["publikationsordinal"],
             "sidnummer": RPUBL["sidnummer"],
             "law": RPUBL["fsNummer"],
             "chapter": RINFOEX["kapitelnummer"],
             "section": RINFOEX["paragrafnummer"],
             "piece": RINFOEX["styckenummer"],
             "item": RINFOEX["punktnummer"],
             "myndighet": DCT["creator"],
             "dnr": RPUBL["diarienummer"]}

dictkey = dict([[v, k] for k, v in list(predicate.items())])

types = {LegalRef.RATTSFALL: RPUBL["Rattsfallsreferat"],
         LegalRef.LAGRUM: RPUBL["KonsolideradGrundforfattning"],
         LegalRef.MYNDIGHETSBESLUT: RPUBL["Myndighetsavgorande"]}

dicttypes = dict([[v, k] for k, v in list(types.items())])

patterns = {LegalRef.RATTSFALL:
            re.compile(
                "http://rinfo.lagrummet.se/publ/rattsfall/(?P<publikation>\w+)/(?P<artal>\d+)(s(?P<sidnummer>\d+)|((:| nr | ref )(?P<lopnummer>\d+)))").match,
            LegalRef.MYNDIGHETSBESLUT:
            re.compile(
                "http://rinfo.lagrummet.se/publ/beslut/(?P<myndighet>\w+)/(?P<dnr>.*)").match,
            LegalRef.LAGRUM:
            re.compile(
                "http://rinfo.lagrummet.se/publ/sfs/(?P<law>\d{4}:\w+)#?(K(?P<chapter>[0-9a-z]+))?(P(?P<section>[0-9a-z]+))?(S(?P<piece>[0-9a-z]+))?(N(?P<item>[0-9a-z]+))?").match
            }


# The dictionary should be a number of properties of the document we
# wish to construct the URI for, e.g:
# {"type": LegalRef.RATTSFALL,
#  "publikation": "nja",
#  "artal": "2004"
#  "sidnr": "43"}
#
# The output is a URI string like 'http://rinfo.lagrummet.se/publ/rattsfall/nja/2004s43'
def construct(dictionary):
    # Step 1: massage the data to a rdflib graph
    graph = Graph()
    bnode = BNode()
    for key in dictionary:
        if key == "type":
            graph.add((bnode, RDF.type, URIRef(types[dictionary[key]])))
        else:
            graph.add((bnode, predicate[key], Literal(dictionary[key])))
    # print graph.serialize(format="nt")
    return construct_from_graph(graph)


def _first_obj(graph, subject, predicate):
    l = list(graph.objects(subject, predicate))
    if not l:
        #raise ValueError("No objects with predicate %s found in the graph" % predicate)
        return None
    else:
        return l[0]


def construct_from_graph(graph):
    # assume every triple in the graph has the same bnode as subject
    bnode = list(graph)[0][0]
    assert(isinstance(bnode, BNode))

    # maybe we should just move the triples into a dict keyed on predicate?
    rdftype = _first_obj(graph, bnode, RDF.type)
    if rdftype == RPUBL["Rattsfallsreferat"]:
        publ = _first_obj(graph, bnode, RPUBL["rattsfallspublikation"])
        if str(publ) == "nja":
            uripart = "%s/%ss%s" % (publ,
                                    _first_obj(graph, bnode, RPUBL["artal"]),
                                    _first_obj(graph, bnode, RPUBL["sidnummer"]))
        else:
            uripart = "%s/%s:%s" % (publ,
                                    _first_obj(graph, bnode, RPUBL["artal"]),
                                    _first_obj(graph, bnode, RPUBL["publikationsordinal"]))

        return "http://rinfo.lagrummet.se/publ/rattsfall/%s" % uripart
    elif rdftype == RPUBL["KonsolideradGrundforfattning"]:
        # print graph.serialize(format="n3")
        attributeorder = [RINFOEX["kapitelnummer"],
                          RINFOEX["paragrafnummer"],
                          RINFOEX["styckenummer"],
                          RINFOEX["punktnummer"]]
        signs = {RINFOEX["kapitelnummer"]: 'K',
                 RINFOEX["paragrafnummer"]: 'P',
                 RINFOEX["styckenummer"]: 'S',
                 RINFOEX["punktnummer"]: 'N'}
        urifragment = _first_obj(graph, bnode, RPUBL["fsNummer"])
        for key in attributeorder:
            if _first_obj(graph, bnode, key):
                if "#" not in urifragment:
                    urifragment += "#"
                urifragment += signs[key] + _first_obj(graph, bnode, key)
        return "http://rinfo.lagrummet.se/publ/sfs/%s" % urifragment

    elif rdftype == RPUBL["Myndighetsavgorande"]:
        return "http://rinfo.lagrummet.se/publ/beslut/%s/%s" % \
               (_first_obj(graph, bnode, DCT["creator"]),
                _first_obj(graph, bnode, RPUBL["diarienummer"]))

    else:
        raise ValueError("Don't know how to construct a uri for %s" % rdftype)


def parse(uri):
    graph = parse_to_graph(uri)
    dictionary = {}
    for (subj, pred, obj) in graph:
        if pred == RDF.type:
            dictionary["type"] = dicttypes[obj]
        else:
            dictionary[dictkey[pred]] = str(obj)

    return dictionary


def parse_to_graph(uri):
    dictionary = None
    for (pid, pattern) in list(patterns.items()):
        m = pattern(uri)
        if m:
            dictionary = m.groupdict()
            dictionary["type"] = pid
            break

    if not dictionary:
        raise ValueError("Can't parse URI %s" % uri)

    graph = Graph()
    for key, value in list(util.ns.items()):
        graph.bind(key, Namespace(value))
    bnode = BNode()
    for key in dictionary:
        if dictionary[key] is None:
            continue
        if key.startswith("_"):
            continue
        if key == "type":
            graph.add((bnode, RDF.type, URIRef(types[dictionary[key]])))
        else:
            graph.add((bnode, predicate[key], Literal(dictionary[key])))

    return graph
