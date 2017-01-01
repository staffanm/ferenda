# -*- coding: utf-8 -*-
# sync data from rdl/resources/base/datasets into what's already
# defined in swedishlegalsource.ttl
from __future__ import unicode_literals

from datetime import date, datetime
import os
import sys

from rdflib.extras.describer import Describer
from rdflib.namespace import SKOS, FOAF, OWL, DCTERMS, RDFS, RDF
import rdflib

sys.path.append(os.getcwd())
from ferenda import util
from ferenda.sources.legal.se import RPUBL, RINFOEX
from ferenda.thirdparty.coin import COIN

if sys.version_info < (3,):
    raise RuntimeError("Only works on py3")

TRANS = str.maketrans("åäö ", "aao-")
    
URIMAP = {}
URISPACE = rdflib.Namespace("http://rinfo.lagrummet.se/sys/uri/space#")
MAPPEDSPACE = rdflib.Namespace("https://lagen.nu/sys/uri/space#")

def import_org(sourcegraph, targetgraph):
    # print("Adding triples in %s to targetgraph" % filename)

    # iterate through all named things (using foaf:name)
    for (sourceuri, name) in sourcegraph.subject_objects(predicate=FOAF.name):
        targeturi = targetgraph.value(predicate=FOAF.name, object=name)
        if not targeturi: # We didn't have this previously. Need to
                          # make up a URI
            uri = "https://lagen.nu/org/%s/%s" % (date.today().year,
                                                  str(name).lower().translate(TRANS))
            print("  Adding new resource %s" %uri)
            targeturi = rdflib.URIRef(uri)
            
        for (p, o) in sourcegraph.predicate_objects(subject=sourceuri):
            if not targetgraph.value(targeturi, p): # we don't know the value for this pred
                print("    Adding: %s %s %s" % (targeturi, sourcegraph.qname(p), o))
                targetgraph.add((targeturi, p, o))
        # finally add owl:sameAs if not already there
        if sourceuri not in targetgraph.objects(targeturi, OWL.sameAs):
            targetgraph.add((targeturi, OWL.sameAs, sourceuri))
            print("    Asserting org %s owl:sameAs %s " % (targeturi, sourceuri))
        URIMAP[sourceuri] = targeturi


def import_dataset(sourcegraph, targetgraph):
    # print("Adding triples in %s to targetgraph" % filename)
    # sourcegraph = rdflib.Graph()
    # sourcegraph.parse(open(filename), format="n3")
    # iterate through all named things (using skos:prefLabel)
    for (sourceuri, name) in sourcegraph.subject_objects(predicate=SKOS.prefLabel):
        targeturi = targetgraph.value(predicate=SKOS.prefLabel, object=name)
        if not targeturi:
            slug = sourcegraph.value(sourceuri, SKOS.altLabel)
            if not slug:
                print("WARNING: Can't find skos:altLabel for %s, using alternate method" %  sourceuri)
                slug = util.uri_leaf(str(sourceuri))
                      
            uri = "https://lagen.nu/dataset/%s" % str(slug).lower().translate(TRANS)
            print("  Adding new resource %s" %uri)
            targeturi = rdflib.URIRef(uri)
            
        for (p, o) in sourcegraph.predicate_objects(subject=sourceuri):
            if not targetgraph.value(targeturi, p): # we don't know the value for this pred
                if p == DCTERMS.publisher:
                    o = URIMAP[o] 
                print("    Adding: %s %s %s" % (targeturi, sourcegraph.qname(p), o))
                targetgraph.add((targeturi, p, o))
        # finally add owl:sameAs if not already there
        if sourceuri not in targetgraph.objects(targeturi, OWL.sameAs):
            targetgraph.add((targeturi, OWL.sameAs, sourceuri))
            print("    Asserting res %s owl:sameAs %s " % (targeturi, sourceuri))
        URIMAP[sourceuri] = targeturi


def import_slugs(sourcegraph, targetgraph):
    targetgraph.bind("space", MAPPEDSPACE)
    for (sourceuri, abbr) in sourcegraph.subject_objects(predicate=URISPACE.abbrSlug):
        if sourceuri in URIMAP:
            targeturi = URIMAP[sourceuri]
            # print("Mapping %s -> %s for slug %s" % (sourceuri, targeturi, abbr))
            targetgraph.add((targeturi, MAPPEDSPACE.abbrSlug, abbr))
        else:
            print("WARNING: Can't find %s in URIMAP" % sourceuri)
            # This is PROBABLY a rdf class like rpubl:Proposition, we
            # don't have mapped uris for those, just add the triple
            targetgraph.add((sourceuri, MAPPEDSPACE.abbrSlug, abbr))
    for (s, p, o) in targetgraph:
        if p != MAPPEDSPACE.abbrSlug:
            targetgraph.remove((s, p, o))

def load_files(path, graph=None):
    # loads all the n3 files found under path into a graph
    if graph is None:
        graph = rdflib.Graph()
    if os.path.isfile(path):
        return load_file(path, graph)
    elif os.path.isdir(path):
        print("loading all n3 files in %s" % path)
        for f in util.list_dirs(path, suffix=".n3"):
            # FIXME: ugly hack to avoid reading one particular n3 file
            if f.endswith("sources.n3"):
                continue
            load_file(f, graph)
        return graph
    else:
        print("ERROR: can't load %s" % path)

def load_file(path, graph=None, bindings={}):
    if graph is None:
        graph = rdflib.Graph()
    print("    loading %s" % path)
    
    with open(path) as fp:
        data = fp.read()
        graph.parse(data=data, format="n3")
    for prefix, ns in bindings.items():
        graph.bind(prefix, ns)
    return graph


def concatgraph(base, dest, adjustfunc=None):
    print("Concatenating everything in %s to %s" % (base, dest))
    g = rdflib.Graph()
    load_files(base, g)
    if adjustfunc:
        adjustfunc(g)
    writegraph(g, dest, "concatenated")
    print("  Concatenated %s triples to %s" % (len(g), dest))


def mapgraph(base, customresources, dest):
    print("Mapping everything in %s, using %s, to %s" %
          (base, customresources, dest))
    targetgraph = rdflib.Graph()
    targetgraph.parse(open(customresources), format="turtle")
    len_before = len(targetgraph)
    import_org(load_files(base+os.sep+"org"), targetgraph)
    import_dataset(load_files(base+os.sep+"serie"), targetgraph)
    targetgraph.bind("urispace", "http://rinfo.lagrummet.se/sys/uri/space#")
    writegraph(targetgraph, dest)
    len_after = len(targetgraph)
    print("  Added %s triples (%s -> %s)" %
          (len_after-len_before, len_before, len_after))


def mapslugs(base, customresources, dest):
    print("Mapping slugs from %s, using %s, to %s" %
          (base, customresources, dest))
    basegraph = load_file(base)
    targetgraph = load_file(customresources)
    targetgraph.bind("rinfoex", str(RINFOEX))
    import_slugs(basegraph, targetgraph)
    targetgraph.add((RINFOEX.Utskottsbetankande,
                     MAPPEDSPACE.abbrSlug,
                     rdflib.Literal("bet")))
    targetgraph.add((RINFOEX.Riksdagsskrivelse,
                     MAPPEDSPACE.abbrSlug,
                     rdflib.Literal("rskr")))
    writegraph(targetgraph, dest)


def writegraph(graph, dest, operation="transformed"):
    util.ensure_dir(dest)
    if os.path.exists(dest):
        olddata = util.readfile(dest).split("\n\n", 1)[1]
    else:
        olddata = ""

    newdata = graph.serialize(format="turtle").decode("utf-8")
    if newdata != olddata:
        with open(dest, "w") as fp:
            header = "# Automatically %s from sources at %s\n\n" % (
                operation, datetime.now().isoformat())
            fp.write(header)
            fp.write(newdata)
            print("Wrote %s triples to %s" % (len(graph), dest))
    else:
        print("%s is unchanged" % dest)


def mapspace(base, dest):
    print("Mapping URISpace in %s to %s" % (base, dest))
    graph = load_file(base)
    graph.bind("rinfoex", str(RINFOEX))
    graph.bind("space", "https://lagen.nu/sys/uri/space#", override=True)
    desc = Describer(graph, "https://lagen.nu/sys/uri/space#")
    abbrslug = "https://lagen.nu/sys/uri/space#abbrSlug"
    for (s, p, o) in list(graph):
        # remove every triple so that we can add an adjusted version
        graph.remove((s, p, o))
        if s == rdflib.URIRef("http://rinfo.lagrummet.se/sys/uri/space#"):
            # change root <http://rinfo.lagrummet.se/sys/uri/space#> of entire
            # space into eg <https://lagen.nu/sys/uri/space#>
            s = rdflib.URIRef("https://lagen.nu/sys/uri/space#")
        elif s == rdflib.URIRef("http://rinfo.lagrummet.se/sys/uri/space#abbrSlug"):
            s = rdflib.URIRef(abbrslug)
        if p == COIN.base:
            # change coin:base
            o = rdflib.Literal("https://lagen.nu")
        elif (p == RDFS.seeAlso and
              o == rdflib.URIRef("http://rinfo.lagrummet.se/sys/uri/slugs")):
            s = p = o = None
        elif p == COIN.uriTemplate:
            strtemplate = str(o)
            # general case
            strtemplate = strtemplate.replace("/publ/", "/")
            # a couple of special cases
            strtemplate = strtemplate.replace("/ext/eur-lex/", "/ext/celex/")
            strtemplate = strtemplate.replace("/rf/{serie}/{arsutgava}/s_{sidnummer}", "/rf/{serie}/{arsutgava}s{sidnummer}")
            strtemplate = strtemplate.replace("_s_{sidnummer}",
                                              "_s._{sidnummer}")
            strtemplate = strtemplate.replace("/rf/", "/dom/")
            o = rdflib.Literal(strtemplate)
        elif p == COIN.fragmentTemplate and o[1] == "_":
            # "p_{paragrafnummer}" => "P{paragrafnummer}"
            strtemplate = str(o)
            strtemplate = strtemplate[0].upper() + strtemplate[2:]
            o = rdflib.Literal(strtemplate)
        elif (p == COIN.slugFrom and
              o == rdflib.URIRef("http://rinfo.lagrummet.se/sys/uri/space#abbrSlug")):
            o = rdflib.URIRef("https://lagen.nu/sys/uri/space#abbrSlug")
        # Wonder why we removed spaces instead of replacing them w/
        # underscore? It seems like a dumb idea. Actually, it was a
        # good idea that didn't mess up fragment identifiers like
        # "#P1a" (without this it becomes "#P1_a")
        elif p == COIN.spaceReplacement:
            o = rdflib.Literal("")
        elif p == COIN.fragmentSeparator:
            o = rdflib.Literal("")
        # We don't need to add this explicit priority if we have
        # coin.py sort templates by type specificity
        # elif p == COIN.forType and o == RPUBL.Rattsfallsnotis:
        #     graph.add((s, COIN.priority, rdflib.Literal(2)))
        if o == COIN.ToLowerCase:  # yeah we don't want this since our
                                   # CELEX uris contains uppercase
            s = p = o = None
        if s:
            graph.add((s, p, o))

    # locate and remove the bilaga fragmentTemplate
    # 1 find root bnode
    root = graph.value(predicate=COIN.fragmentTemplate,
                       object=rdflib.Literal("bilaga_{repr}"))
    # 2 find binding bnode
    binding = graph.value(subject=root, predicate=COIN.binding)
    # remove it all
    for (p, o) in graph.predicate_objects(binding):
        graph.remove((binding, p, o))
    for (p, o) in graph.predicate_objects(root):
        graph.remove((root, p, o))
    space = graph.value(predicate=COIN.template, object=root)
    graph.remove((space, COIN.template, root))
    
    # add extra stuff
    extra = """
@prefix : <http://rinfo.lagrummet.se/sys/uri/space#> .
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix coin: <http://purl.org/court/def/2009/coin#> .
@prefix rpubl: <http://rinfo.lagrummet.se/ns/2008/11/rinfo/publ#> .
@prefix rinfoex: <http://lagen.nu/terms#> .
@prefix space: <https://lagen.nu/sys/uri/space#> .

space: coin:template [ 
        coin:relFromBase rinfoex:stycke;
        coin:fragmentTemplate "S{styckenummer}";
        coin:binding [ coin:property rinfoex:styckenummer ]
     ], [
        coin:relFromBase rinfoex:moment;
        coin:fragmentTemplate "O{momentnummer}";
        coin:binding [ coin:property rinfoex:momentnummer ]
     ], [
        coin:relFromBase rinfoex:punkt;
        coin:fragmentTemplate "N{punktnummer}";
        coin:binding [ coin:property rinfoex:punktnummer ]
     ], [
        coin:relFromBase rinfoex:subpunkt;
        coin:fragmentTemplate "N{subpunktnummer}";
        coin:binding [ coin:property rinfoex:subpunktnummer ]
     ], [
        coin:relFromBase rinfoex:subsubpunkt;
        coin:fragmentTemplate "N{subsubpunktnummer}";
        coin:binding [ coin:property rinfoex:subsubpunktnummer ]
     ], [
        coin:relFromBase rinfoex:mening;
        coin:fragmentTemplate "M{meningnummer}";
        coin:binding [ coin:property rinfoex:meningnummer ]
     ], [
        coin:relFromBase rinfoex:andringsforfattning;
        coin:fragmentTemplate "L{andringsforfattningnummer}";
        coin:binding [ coin:property rinfoex:andringsforfattningnummer ]
     ], [
        coin:relFromBase rinfoex:rubrik;
        coin:fragmentTemplate "R{rubriknummer}";
        coin:binding [ coin:property rinfoex:rubriknummer ]
     ], [
        coin:relFromBase rinfoex:underavdelning;
        coin:fragmentTemplate "U{underavdelningnummer}";
        coin:binding [ coin:property rinfoex:underavdelningnummer ]
     ], [
        coin:relFromBase rinfoex:avdelning;
        coin:fragmentTemplate "A{avdelningnummer}";
        coin:binding [ coin:property rinfoex:avdelningnummer ]
     ], [
        coin:relFromBase rinfoex:bilaga;
        coin:fragmentTemplate "B{bilaganummer}";
        coin:binding [ coin:property rinfoex:bilaganummer ]
     ], [
         coin:relFromBase rinfoex:sid ;
         coin:fragmentTemplate "sid{sidnummer}" ;
         coin:binding [ coin:property rinfoex:sidnummer ] 
     ], [ 
        coin:relFromBase rinfoex:avsnitt ;
        coin:binding [ coin:property rinfoex:avsnittnummer ] ;
        coin:fragmentTemplate "S{avsnittnummer}" ;
        coin:slugTransform [ coin:apply coin:ToBaseChar ;
                             coin:replace ". -" ]
     ], [
        coin:uriTemplate "/ext/celex/{celexNummer}#{artikelnummer}";
        coin:binding [ coin:property rpubl:celexNummer ],
                     [ coin:property rinfoex:artikelnummer ]
    ], [ 
       coin:uriTemplate "/{arsutgava}:{lopnummer}";
       coin:priority 1;
       coin:binding [ coin:property rpubl:forfattningssamling ;
                      coin:slugFrom space:abbrSlug ;
                      coin:match "sfs" ;
                      coin:variable "fs" ],
                    [ coin:property rpubl:arsutgava ;
                      coin:variable "arsutgava" ],
                    [ coin:property rpubl:lopnummer ;
                      coin:variable "lopnummer" ] ;
    ], [ 
       coin:uriTemplate "/{arsutgava}:{lopnummer}_s._{sidnummer}";
       coin:priority 1;
       coin:binding [ coin:property rpubl:forfattningssamling ;
                      coin:slugFrom space:abbrSlug ;
                      coin:match "sfs" ;
                      coin:variable "fs" ],
                    [ coin:property rpubl:arsutgava ;
                      coin:variable "arsutgava" ],
                    [ coin:property rpubl:lopnummer ;
                      coin:variable "lopnummer" ],
                    [ coin:property rpubl:sidnummer ;
                      coin:variable "sidnummer" ] ;
     ], [ 
        coin:uriTemplate "/{arsutgava}:bih_{bihang}_s._{sidnummer}";
        coin:priority 1;
        coin:binding [ coin:property rpubl:forfattningssamling ;
                       coin:slugFrom space:abbrSlug ;
                       coin:match "sfs" ;
                       coin:variable "fs" ],
                     [ coin:property rpubl:arsutgava ;
                       coin:variable "arsutgava" ],
                     [ coin:property rpubl:bihangsnummer ;
                       coin:variable "bihang" ],
                     [ coin:property rpubl:sidnummer ;
                       coin:variable "sidnummer" ] ;
     ], [ 
        coin:uriTemplate "/{fs}/{arsutgava}:bih_{bihang}";
        coin:priority 1;
        coin:binding [ coin:property rpubl:forfattningssamling ;
                       coin:slugFrom space:abbrSlug ;
                       coin:match "sfs" ;
                       coin:variable "fs" ],
                     [ coin:property rpubl:arsutgava ;
                       coin:variable "arsutgava" ],
                     [ coin:property rpubl:bihangsnummer ;
                       coin:variable "bihang" ] ;
   ].
"""
    graph.parse(data=extra, format="turtle")
    writegraph(graph, dest)
    print("Mapped %s triples to URISpace definitions" % len(graph))


#def add_canonical_templates(graph):
#    desc = Describer(graph, URISPACE)
#    proptuples = [(RPUBL.kapitelnummer, "k_"),
#                  (RPUBL.paragrafnummer, "p_")]
#    while proptuples:
#        bindings = [RPUBL.forfattningssamling, RPUBL.arsutgava, RPUBL.lopnummer]
#        uritemplate = "/publ/{fs}/{arsutgava}:{lopnummer}#"
#        for p, fragletter in proptuples:
#            with desc.rel(COIN.template):
#                uritemplate += fragletter + "{" + util.uri_leaf(p) + "}-"
#                # print("adding uritemplate %s" % uritemplate)
#                desc.value(COIN.uriTemplate, uritemplate[:-1])
#                bindings.append(p)
#                add_bindings(desc, bindings,
#                             "http://rinfo.lagrummet.se/sys/uri/space#abbrSlug")
#        proptuples.pop(0)

def add_finegrained(desc, template, abbrslug):
    # now create ~10 bunch of fine-grained templates for each
    # fs-template that can mint uris for sections, paragraphs
    # etc. 
    # "#K{kapnr}",
    # "#K{kapnr}P{parnr}"
    # "#K{kapnr}P{parnr}S{stnr}"
    # "#K{kapnr}P{parnr}S{stnr}N{pnr}"
    # "#P{parnr}"
    # "#P{parnr}S{stnr}"
    # "#P{parnr}S{stnr}N{pnr}"
    # "#S{stnr}"
    # "#S{stnr}N{pnr}"
    proptuples = [(RPUBL.kapitelnummer, "K"),
                  (RPUBL.paragrafnummer, "P"),
                  (RINFOEX.styckenummer, "S"),
                  (RINFOEX.punktnummer, "N")]
    while len(proptuples) > 1:
        bindings = [RPUBL.forfattningssamling, RPUBL.arsutgava, RPUBL.lopnummer]
        uritemplate = template + "#"
        for p, fragletter in proptuples:
            bindings.append(p)
            with desc.rel(COIN.template):
                uritemplate += fragletter + "{" + util.uri_leaf(p) + "}"
                # print("adding uritemplate %s" % uritemplate)
                desc.value(COIN.uriTemplate, uritemplate)
                add_bindings(desc, bindings, abbrslug)
                             
        proptuples.pop(0)
    

def add_bindings(desc, bindings, slugFrom):
    for b in bindings:
        with desc.rel(COIN.binding):
            desc.rel(COIN.property, b)
            if b == RPUBL.forfattningssamling:
                desc.value(COIN.variable, "fs")
                desc.rel(COIN.slugFrom, slugFrom)
            elif b == RDF.type:
                desc.value(COIN.variable, "rtype")
                desc.rel(COIN.slugFrom, slugFrom)
            else:
                desc.value(COIN.variable, util.uri_leaf(b))

    

def main():
    concatgraph("../rdl/resources/base/datasets",
                "ferenda/sources/legal/se/res/extra/swedishlegalsource.ttl")
    concatgraph("../rdl/resources/base/sys/uri/slugs.n3",
                "ferenda/sources/legal/se/res/uri/swedishlegalsource.slugs.ttl")
    # NB: we might need to add a few templates dynamically to this one
    # (like mapspace does):
    concatgraph("../rdl/resources/base/sys/uri/space.n3",
                "ferenda/sources/legal/se/res/uri/swedishlegalsource.space.ttl")
    mapgraph("../rdl/resources/base/datasets",
             "lagen/nu/res/extra/swedishlegalsource.ttl",
             "lagen/nu/res/extra/swedishlegalsource.ttl")
    mapslugs("../rdl/resources/base/sys/uri/slugs.n3",
             "lagen/nu/res/extra/swedishlegalsource.ttl",
             "lagen/nu/res/uri/swedishlegalsource.slugs.ttl")
    mapspace("../rdl/resources/base/sys/uri/space.n3",
             "lagen/nu/res/uri/swedishlegalsource.space.ttl")

if __name__ == '__main__':
    main()
