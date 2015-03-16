# -*- coding: utf-8 -*-
# sync data from rdl/resources/base/datasets into what's already
# defined in swedishlegalsource.ttl
from __future__ import unicode_literals

import sys
import os
sys.path.append(os.getcwd())
from datetime import datetime

import rdflib
from rdflib.namespace import SKOS, FOAF, OWL, DCTERMS

from ferenda import util

if sys.version_info < (3,):
    raise RuntimeError("Only works on py3")

TRANS = str.maketrans("åäö ", "aao_")
    
URIMAP = {}


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


def load_n3(path, graph=None):
    # loads all the n3 files directly with in a given path (does not
    # recurse) into graph.
    if graph is None:
        graph = rdflib.Graph()
    print("loading all n3 files in %s" % path)
    for f in os.listdir(path):
        if f.endswith("n3"):
            print("    loading %s" % f)
            graph.parse(open(path+os.sep+f), format="n3")
    return graph

def concatgraph(base, dest):
    g = rdflib.Graph()
    load_n3(base+os.sep+"org", g)
    load_n3(base+os.sep+"serie", g)
    with open(dest, "wb") as fp:
        header = "# Automatically concatenated from sources at %s\n\n" % datetime.now().isoformat()
        fp.write(header.encode("utf-8"))
        g.serialize(fp, format="turtle")
    print("Concatenated %s triples" % (len(g)))

def mapgraph(base, customresources, dest):
    targetgraph = rdflib.Graph()
    targetgraph.parse(open(customresources), format="turtle")
    len_before = len(targetgraph)
    import_org(load_n3(base+os.sep+"org"), targetgraph)
    import_dataset(load_n3(base+os.sep+"serie"), targetgraph) 
    with open(dest, "wb") as fp:
        header = "# Automatically transformed from sources at %s\n\n" % datetime.now().isoformat()
        fp.write(header.encode("utf-8"))
        targetgraph.serialize(fp, format="turtle")
    len_after = len(targetgraph)
    print("Added %s triples (%s -> %s)" % (len_after-len_before, len_before, len_after))

def main():
    concatgraph("../rdl/resources/base/datasets",
                "ferenda/res/extra/swedishlegalsource.auto.ttl")
    mapgraph("../rdl/resources/base/datasets",
             "lagen/nu/extra/swedishlegalsource.ttl",
             "lagen/nu/extra/swedishlegalsource.auto.ttl")

if __name__ == '__main__':
    main()
