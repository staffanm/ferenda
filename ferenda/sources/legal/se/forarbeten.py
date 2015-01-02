# -*- coding: utf-8 -*-

# this is a sort of a wrapper repo to provide useful tabs/tocs for set
# of related docrepos ("preparatory works")

from . import SwedishLegalSource, RPUBL

class Forarbeten(SwedishLegalSource):
    alias = "forarbeten"

    rdf_type = (RPUBL.Direktiv, RPUBL.Utredning, RPUBL.Proposition)

    def tabs(self):
        return [("Förarbeten", self.dataset_uri())]

    def faceted_data(self):
        from pudb import set_trace; set_trace()
        res = super(Forarbeten, self).faceted_data()
        return res

    def facet_query(self, context):
        # Override the standard query in order to ignore the default
        # context (provided by .dataset_uri()) since we're going to
        # look at  other docrepos' data
        return """PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX dcterms: <http://purl.org/dc/terms/>
PREFIX rpubl: <http://rinfo.lagrummet.se/ns/2008/11/rinfo/publ#>

SELECT DISTINCT ?uri ?rdf_type ?dcterms_title ?dcterms_issued
WHERE {
    ?uri rdf:type ?type .
    OPTIONAL { ?uri rdf:type ?rdf_type . }
    OPTIONAL { ?uri dcterms:title ?dcterms_title . }
    OPTIONAL { ?uri dcterms:identifier ?dcterms_identifier . }
    OPTIONAL { ?uri rpubl:utrSerie ?rpubl_utrSerie . }
    OPTIONAL { ?uri dcterms:issued ?dcterms_issued . }
    FILTER (?type in (rpubl:Direktiv, rpubl:Utredningsbetankande, rpubl:Proposition)) .
} """

    def facets(self):
        res = super(Forarbeten, self).facets()
        return res
