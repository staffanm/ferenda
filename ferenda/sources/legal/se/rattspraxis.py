# -*- coding: utf-8 -*-
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
from builtins import *
"""Hanterar domslut (detaljer och referat) från Domstolsverket. Data
hämtas från JSON-API:et https://rattspraxis.etjanst.domstol.se/"""

from ferenda import (Document, DocumentStore, Describer, WordReader, FSMParser, Facet)
from . import SwedishLegalSource, SwedishCitationParser, RPUBL

class Rattspraxis(SwedishLegalSource):

    # requesthandler_class = DVHandler
    alias = "rattspraxis"
    downloaded_suffix = ".json"
    rdf_type = (RPUBL.Rattsfallsreferat, RPUBL.Rattsfallsnotis)
    documentstore_class = DocumentStore
    required_predicates = [RDF.type, DCTERMS.identifier, PROV.wasGeneratedBy]
    DCTERMS = Namespace(util.ns['dcterms'])
    sparql_annotations = "sparql/dv-annotations.rq"
    sparql_expect_results = False
    xslt_template = "xsl/dv.xsl"
    iterlinks = False
    start_url = "https://rattspraxis.etjanst.domstol.se/api/v1/sok"
    hits_per_page = 100
    defaultquery = {'antalPerSida': hits_per_page, 
                    'asc': False, 
                    'sidIndex': 0,
                    'sokfras': {'andLista': [], 'notLista': [], 'orLista': []},
                    'sortorder': 'avgorandedatum'
                    }


    def download_get_first_page(self):
        resp = self.session.post(self.start_url, json=self.defaultquery)
        return resp

    @decorators.downloadmax
    def download_get_basefiles(self, source):
        data = source.json()
        page = 1
        while not done:
            for item in data['publiceringslista']:
                basefile = f"{item['domstol']['domstolskod']}/{item['malNummerLista'][0].replace(" ", "")}"
                uri = f"https://rattspraxis.etjanst.domstol.se/api/v1/publiceringar/grupp/{item['gruppKorrelationsnummer']}"
                if item['typ'] == "DOM_ELLER_BESLUT":
                    basefile += "_D"
                elif item['typ'] == "PROVNINGSTILLSTAND":
                    basefile += "_P"
                elif item['typ'] == "REFERAT":
                    basefile += "_R"
                else:
                    self.log.warning(f"unknown type {item['typ']}")
                yield basefile, {'uri': uri}
            done = data["total"] / self.hits_per_page >= page
