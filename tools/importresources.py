# -*- coding: utf-8 -*-
# sync data from rdl/resources/base/datasets into what's already
# defined in swedishlegalsource.ttl
from __future__ import unicode_literals

import sys
import os
import codecs
sys.path.append(os.getcwd())
from datetime import datetime

import rdflib
from rdflib.namespace import SKOS, FOAF, OWL, DCTERMS, RDFS
from rdflib.extras.describer import Describer

from ferenda.thirdparty.coin import COIN
from ferenda.sources.legal.se import RPUBL, RINFOEX

from ferenda import util

if sys.version_info < (3,):
    raise RuntimeError("Only works on py3")

TRANS = str.maketrans("åäö ", "aao_")
    
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
            uri = "https://lagen.nu/org/2014/%s" % str(name).lower().translate(TRANS)
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
    import_slugs(basegraph, targetgraph)
    writegraph(targetgraph, dest)


def writegraph(graph, dest, operation="transformed"):
    util.ensure_dir(dest)
    with open(dest, "w") as fp:
        header = "# Automatically %s from sources at %s\n\n" % (
            operation, datetime.now().isoformat())
        fp.write(header)
        fp.write(graph.serialize(format="turtle").decode("utf-8"))
        print("Wrote %s triples to %s" % (len(graph), dest))


def mapspace(base, dest):
    print("Mapping URISpace in %s to %s" % (base, dest))
    graph = load_file(base)
    graph.bind("rinfoex", str(RINFOEX))
    graph.bind("space", "https://lagen.nu/sys/uri/space#", override=True)
    for (s, p, o) in list(graph):
        # remove every triple so that we can add an adjusted version
        graph.remove((s, p, o))
        if s == rdflib.URIRef("http://rinfo.lagrummet.se/sys/uri/space#"):
            # change root <http://rinfo.lagrummet.se/sys/uri/space#> of entire
            # space into eg <https://lagen.nu/sys/uri/space#>
            s = rdflib.URIRef("https://lagen.nu/sys/uri/space#")
        elif s == rdflib.URIRef("http://rinfo.lagrummet.se/sys/uri/space#abbrSlug"):
            s = rdflib.URIRef("https://lagen.nu/sys/uri/space#abbrSlug")
        if p == COIN.base:
            # change coin:base
            o = rdflib.Literal("https://lagen.nu")
        elif (p == RDFS.seeAlso and
              o == rdflib.URIRef("http://rinfo.lagrummet.se/sys/uri/slugs")):
            s = p = o = None
        elif p == COIN.uriTemplate:
            # : coin:template * coin:uriTemplate "/publ/{fs}" => "/{fs}"
            # general case: remove leading /publ/
            o = rdflib.Literal(str(o).replace("/publ/", "/"))
        elif (p == COIN.slugFrom and
              o == rdflib.URIRef("http://rinfo.lagrummet.se/sys/uri/space#abbrSlug")):
            o = rdflib.URIRef("https://lagen.nu/sys/uri/space#abbrSlug")
        elif (p == COIN.spaceReplacement):
            o = rdflib.Literal("")
        if s:
            graph.add((s, p, o))

    # now create ~10 bunch of fine-grained templates that can mint
    # uris for sections, paragraphs etc. This doesn't need to be
    # dynamically generated, we could just add the 100 or so triples
    # neeeded statically. But now we shave the yak this way.
    #
    # "#K{kapnr}",
    # "#K{kapnr}P{parnr}"
    # "#K{kapnr}P{parnr}S{stnr}"
    # "#K{kapnr}P{parnr}S{stnr}N{pnr}"
    # "#P{parnr}"
    # "#P{parnr}S{stnr}"
    # "#P{parnr}S{stnr}N{pnr}"
    # "#S{stnr}"
    # "#S{stnr}N{pnr}"
    desc = Describer(graph, "https://lagen.nu/sys/uri/space#")
    proptuples = [(RPUBL.kapitelnummer, "K"),
                  (RPUBL.paragrafnummer, "P"),
                  (RINFOEX.styckenummer, "S"),
                  (RINFOEX.punktnummer, "N")]
    while len(proptuples) > 1:
        bindings = [RPUBL.forfattningssamling, RPUBL.arsutgava, RPUBL.lopnummer]
        uritemplate = "/{fs}/{arsutgava}:{lopnummer}#"  # add stringmunging
        for p, fragletter in proptuples:
            bindings.append(p)
            with desc.rel(COIN.template):
                uritemplate += fragletter + "{" + util.uri_leaf(p) + "}"
                # print("adding uritemplate %s" % uritemplate)
                desc.value(COIN.uriTemplate, uritemplate)
                add_bindings(desc, bindings,
                             "https://lagen.nu/sys/uri/space#abbrSlug")
        proptuples.pop(0)
        
    writegraph(graph, dest)
    print("Mapped %s triples to URISpace definitions" % len(graph))


def add_canonical_templates(graph):
    desc = Describer(graph, URISPACE)
    proptuples = [(RPUBL.kapitelnummer, "k_"),
                  (RPUBL.paragrafnummer, "p_")]
    while proptuples:
        bindings = [RPUBL.forfattningssamling, RPUBL.arsutgava, RPUBL.lopnummer]
        uritemplate = "/publ/{fs}/{arsutgava}:{lopnummer}#"
        for p, fragletter in proptuples:
            with desc.rel(COIN.template):
                uritemplate += fragletter + "{" + util.uri_leaf(p) + "}-"
                # print("adding uritemplate %s" % uritemplate)
                desc.value(COIN.uriTemplate, uritemplate[:-1])
                bindings.append(p)
                add_bindings(desc, bindings,
                             "http://rinfo.lagrummet.se/sys/uri/space#abbrSlug")
        proptuples.pop(0)

def add_bindings(desc, bindings, slugFrom):
    for b in bindings:
        with desc.rel(COIN.binding):
            desc.rel(COIN.property, b)
            if b == RPUBL.forfattningssamling:
                desc.value(COIN.variable, "fs")
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
                "ferenda/sources/legal/se/res/uri/swedishlegalsource.space.ttl",
                add_canonical_templates)
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
