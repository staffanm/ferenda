# a simple tool to update the schema definitions at ferenda/res/vocab
# from their canonical places. This often includes transforming from
# their source formats (like RDF/OWL or RDFa) into Turtle (which is
# the serialization format we use) and possibly filtering out data
# which is not useful to us.

from datetime import datetime

import rdflib

def import_schema_org(g, url, prefix):
    # from http://schema.org/docs/datamodel.html:
    # The canonical machine representation of schema.org is in RDFa:
    # [http://schema.org/docs/schema_org_rdfa.html]
    g.parse(url, format="rdfa")
    g.bind(prefix, rdflib.URIRef("http://schema.org/"))

def import_prov(g, url, prefix):
    g.parse(url)
    g.bind(prefix, rdflib.URIRef("http://www.w3.org/ns/prov#"))
    for (s,p,o) in g:
        if not str(s).startswith("http://www.w3.org/ns/prov#"):
            g.remove((s,p,o))

def import_rpubl(g, url, prefix):
    g.parse(url, format="n3")
    g.bind(prefix, rdflib.URIRef("http://rinfo.lagrummet.se/ns/2008/11/rinfo/publ#"))
    
def import_generic(g, url, prefix):
    # assume that the vocabulary is published at the canonical address
    # under where all terms are defined
    g.parse(url)
    g.bind(prefix, rdflib.URIRef(url))
    for (s,p,o) in g:
        # and remove any statements about terms not in the vocabulary
        if not str(s).startswith(url):
            g.remove((s,p,o))
    
def main():
    for prefix, func, url in (
            ("schema", import_schema_org, "http://schema.org/docs/schema_org_rdfa.html"),
            ("dct", import_generic, "http://purl.org/dc/terms/"),
            ("dc", import_generic, "http://purl.org/dc/elements/1.1/"),
            ("bibo", import_generic, "http://purl.org/ontology/bibo/"),
            ("foaf", import_generic, "http://xmlns.com/foaf/0.1/"),
            ("prov", import_prov, "http://www.w3.org/ns/prov-o"),
            ("rpubl", import_rpubl, "https://raw.githubusercontent.com/rinfo/rdl/develop/resources/base/model/rinfo_publ.n3")):
        g = rdflib.Graph()
        print("importing %s (%s)" % (prefix, func.__name__))
        func(g, url, prefix)
        with open("ferenda/res/vocab/%s.ttl" % prefix, "wb") as fp:
            header = "# Automatically transformed from canonical source (%s) at %s\n\n" % (url, datetime.now().isoformat())
            fp.write(header.encode("utf-8"))
            g.serialize(fp, format="turtle")
            print("imported %s (%s statements)" % (prefix, len(g)))

main()
