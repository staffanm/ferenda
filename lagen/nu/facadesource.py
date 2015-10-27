# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from ferenda import CompositeRepository


class FacadeSource(CompositeRepository):
    tablabel = "FacadeSource"  # subclasses override this

    def facet_query(self, context):
        pass

    def facets(self):
        pass

    @classmethod
    def parse_all_setup(cls, config):
        return False
    
    def facet_query(self, context):
        # Override the standard query in order to ignore the default
        # context (provided by .dataset_uri()) since we're going to
        # look at other docrepos' data
        #
        # 1 collect all possible types (from subrepos -- or from class
        #   property list?)
        # 2 collect all possible optional properties (from class
        #   property list -- or from subrepo facets?)
        #   (collecting subrepo facets means having subrepo instances
        #   -- but inheriting from CompositeRepository should make
        #   that easy)
        # 
        g = self.make_graph()
        predicates = set()
        bindings = set()
        rdftypes = set()
        for cls in self.subrepos:
            inst = self.get_instance(cls)
            for f in inst.facets():
                predicates.add(f.rdftype)
                bindings.add(f.dimension_label if f.dimension_label else g.qname(f.rdftype).replace(":", "_"))
            predicates.update([f.rdftype for f in inst.facets()])
            if isinstance(inst.rdftype, URIRef):
                rdftypes.add(inst.rdf_type)
            else:
                rdftypes.update(inst.rdf_type)

            namespaces.update([ns for ns in inst.ns.values() if [
                f for f in predicates +
                rdftypes if f.startswith(ns)]])
            if RDF not in namespaces:
                namespaces.append(RDF)

        selectbindings = " ".join(["?" + b for b in bindings])
        types = "(" + "|".join([g.qname(x) for x in rdftypes]) + ")"
        types = g.qname(rdftypes[0])
        if len(rdftypes) == 1:
            whereclause = "?uri rdf:type %s" % types
            filterclause = ""
        else:
            whereclause = "?uri rdf:type ?type"
            filterclause = "    FILTER (?type in (%s)) ." % ", ".join(
                [g.qname(x) for x in rdftypes])

        optclauses = "".join(
            ["    OPTIONAL { ?uri %s ?%s . }\n" % (g.qname(p), b) for p, b in zip(predicates, bindings)])[:-1]

        # FIXME: The above doctest looks like crap since all
        # registered namespaces in the repo is included. Should only
        # include prefixes actually used
        prefixes = "".join(["PREFIX %s: <%s>\n" % (p, u)
                            for p, u in sorted(self.ns.items()) if u in namespaces])

        query = """%(prefixes)s
SELECT DISTINCT ?uri %(selectbindings)s
%(from_graph)s
WHERE {
    %(whereclause)s .
%(optclauses)s
%(filterclause)s
}""" % locals()
        return query


    def tabs(self):
        subtabs = [self.get_instance(c).tabs() for c in self.subrepos]
        return [(self.tablabel, self.dataset_uri(), subtabs)]
