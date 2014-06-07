# sync data from rdl/resources/base/datasets into what's already defined in swedishlegalsource.ttl

import sys,os
sys.path.append(os.getcwd())
from datetime import datetime

import rdflib
from rdflib.namespace import SKOS, FOAF, OWL, DCT

from ferenda import util



TRANS = str.maketrans("åäö ", "aao_")
URIMAP = {}

def import_org(filename, targetgraph):
    print("Adding triples in %s to targetgraph" % filename)
    sourcegraph = rdflib.Graph()
    sourcegraph.parse(open(filename), format="n3")
    # iterate through all named things (using foaf:name)
    for (sourceuri, name) in sourcegraph.subject_objects(predicate=FOAF.name):
        targeturi = targetgraph.value(predicate=FOAF.name, object=name)
        if not targeturi: # We didn't have this previously. Need to
                          # make up a URI
            uri = "http://lagen.nu/org/2014/%s" % str(name).lower().translate(TRANS)
            print("  Adding new resource %s" %uri)
            targeturi = rdflib.URIRef(uri)
            
        for (p, o) in sourcegraph.predicate_objects(subject=sourceuri):
            if not targetgraph.value(targeturi, p): # we don't know the value for this pred
                print("    Adding: %s %s %s" % (targeturi, sourcegraph.qname(p), o))
                targetgraph.add((targeturi, p, o))
        # finally add owl:sameAs (should we check to see if it exists?)
        targetgraph.add((targeturi, OWL.sameAs, sourceuri))
        URIMAP[sourceuri] = targeturi
        print("    Asserting owl:sameAs %s " % sourceuri)

def import_dataset(filename, targetgraph):
    print("Adding triples in %s to targetgraph" % filename)
    sourcegraph = rdflib.Graph()
    sourcegraph.parse(open(filename), format="n3")
    # iterate through all named things (using skos:prefLabel)
    for (sourceuri, name) in sourcegraph.subject_objects(predicate=SKOS.prefLabel):
        targeturi = targetgraph.value(predicate=SKOS.prefLabel, object=name)
        if not targeturi:
            slug = sourcegraph.value(sourceuri, SKOS.altLabel)
            if not slug:
                print("WARNING: Can't find skos:altLabel for %s, using alternate method" %  sourceuri)
                slug = util.uri_leaf(str(sourceuri))
                      
            uri = "http://lagen.nu/dataset/%s" % str(slug).lower().translate(TRANS)
            print("  Adding new resource %s" %uri)
            targeturi = rdflib.URIRef(uri)
            
        for (p, o) in sourcegraph.predicate_objects(subject=sourceuri):
            if not targetgraph.value(targeturi, p): # we don't know the value for this pred
                if p == DCT.publisher:
                    o = URIMAP[o] 
                print("    Adding: %s %s %s" % (targeturi, sourcegraph.qname(p), o))
                targetgraph.add((targeturi, p, o))
        # finally add owl:sameAs (should we check to see if it exists?)
        targetgraph.add((targeturi, OWL.sameAs, sourceuri))
        URIMAP[sourceuri] = targeturi
        print("    Asserting owl:sameAs %s " % sourceuri)
    

def main():
    targetgraph = rdflib.Graph()
    targetgraph.parse(open("ferenda/res/extra/swedishlegalsource.ttl"), format="turtle")
    len_before = len(targetgraph)
    import_org("../rdl/resources/base/datasets/org/departement.n3", targetgraph)
    import_org("../rdl/resources/base/datasets/org/domstolar.n3", targetgraph)
    import_org("../rdl/resources/base/datasets/org/lansstyrelser.n3", targetgraph)
    import_org("../rdl/resources/base/datasets/org/myndigheter.n3", targetgraph)
    import_dataset("../rdl/resources/base/datasets/serie/ar.n3", targetgraph) 
    import_dataset("../rdl/resources/base/datasets/serie/fs.n3", targetgraph) 
    import_dataset("../rdl/resources/base/datasets/serie/rf.n3", targetgraph) 
    import_dataset("../rdl/resources/base/datasets/serie/utr.n3", targetgraph) 
    with open("ferenda/res/extra/swedishlegalsource.auto.ttl", "wb") as fp:
        header = "# Automatically transformed from sources at %s\n\n" % datetime.now().isoformat()
        fp.write(header.encode("utf-8"))
        targetgraph.serialize(fp, format="turtle")
    len_after = len(targetgraph)
    print("Added %s triples (%s -> %s)" % (len_after-len_before, len_before, len_after))
main()
