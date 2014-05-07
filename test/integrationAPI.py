# -*- coding: utf-8 -*-
from __future__ import unicode_literals

# sys
import json
import os
import sys
import shutil

# thirdparty
# none yet

# mine
from testWSGI import WSGI  # provides the nice call_wsgi func
from ferenda import DocumentRepository
from ferenda import util

class BasicAPI(WSGI):
    # note: self.repo already contains a initialized DocumentRepository
    repos = [DocumentRepository()]
    
    def put_files_in_place(self):
        # create three basic documents (at parsed and distilled)
        #
        # each document should have a dct:title, a dct:issued and a dct:publisher, which has a URI
        #
        # basefile	dct:title	dct:issued	dct:publisher
        # 123/a             "Example"           2014-01-04      <http://example.org/publisher/A>
        # 123/b            "Example 2"           2013-09-23      <http://example.org/publisher/B>
        # 123/c         "Of needles and haystacks"      2014-05-06      <http://example.org/publisher/B>
        for i in ('a','b','c'):
            self.ttl_to_rdf_xml("test/files/base/distilled/123/%s.ttl" % i,
                                self.repo.store.distilled_path("123/%s" % i))
            util.ensure_dir(self.repo.store.parsed_path("123/%s" % i))
            shutil.copy2("test/files/base/parsed/123/%s.xhtml" % i,
                                self.repo.store.parsed_path("123/%s" % i))
    # it's possible that json_context, var_terms and var_common should
    # be created by makeresources and served through wsgi_static (if
    # we can get conneg right)
    def test_json_context(self):
        self.env['PATH_INFO'] = "/json-ld/context.json"
        got = json.loads(self.call_wsgi(self.env))
        want = {'@context': [
            {"rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
             "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
             "owl": "http://www.w3.org/2002/07/owl#",
             "skos": "http://www.w3.org/2004/02/skos/core#",
             "xsd": "http://www.w3.org/2001/XMLSchema#",
             "xhv": "http://www.w3.org/1999/xhtml/vocab#"},
            {"Property": "rdf:Property",
             "Class": "owl:Class",
             "ObjectProperty": "owl:ObjectProperty",
             "DatatypeProperty": "owl:DatatypeProperty",
             "inverseOf": "owl:inverseOf",
             "label": "rdfs:label",
             "comment": "rdfs:comment",
             "seeAlso": "rdfs:seeAlso",
             "Concept": "skos:Concept",
             "prefLabel": "skos:prefLabel",
             "altLabel": "skos:altLabel"}
            # ... and a lot of other dicts as well
            ]}
        self.assertEqual(want, got)
        
    def test_var_terms(self):
        self.env['PATH_INFO'] = "/var/terms"
        self.env['HTTP_ACCEPT'] = 'application/json'
        # ignore the status and headers elements of the result tuple,
        # only use the content part
        got = json.loads(self.call_wsgi(self.env)[2].decode("utf-8"))
        want = {"@context": "/json-ld/context.json", # might be different
                "iri": "http://localhost:8080/var/terms", # also might be diff
                "topic": [
                    {"iri": "http://purl.org/dc/terms/title", # unless qualified in context.json
                     "label": "Title",
                     "comment": "A name given to the resource.",
                     "type": "Property"},
                    {"iri": "http://purl.org/dc/terms/issued",
                     "label": "Date issued",
                     "comment": "Date of formal issuance (e.g., publication) of the resource.",
                     "type": "Property"},
                    {"iri": "http://purl.org/dc/terms/publisher",
                     "label": "Publisher",
                     "comment": "An entity responsible for making the resource available.",
                     "type": "Property"},
                    ]
                }
        # NB: It might be useful to ALSO provide a RDF Graph version
        # of 'want', and then having the 'got' equivalent being
        # computed using rdflib.Graph().parse(format='json-ld',
        # context=self.call_wsgi("/json-ld/context.json")). In that
        # case, ensuring that the got graph contains *at least*
        # everything in the want graph gets easier.
        self.assertEqual(want, got)

    def test_var_common(self):
        self.env['PATH_INFO'] = "/var/common"
        self.env['HTTP_ACCEPT'] = 'application/json'
        got = json.loads(self.call_wsgi(self.env))
        want = {"@context": "/json-ld/context.json", # might be different
                "iri": "http://localhost:8080/var/terms", # also might be diff
                "topic": [
                    {"iri": "http://example.org/publisher/A",
                     "name": "Publishing company A",
                     "type": "Organization",
                     
                     
                     "label": "Title",
                     "comment": "A name given to the resource.",
                     "type": "Property"},
                    {"iri": "http://purl.org/dc/terms/issued",
                     "label": "Date issued",
                     "comment": "Date of formal issuance (e.g., publication) of the resource.",
                     "type": "Property"},
                    {"iri": "http://purl.org/dc/terms/publisher",
                     "label": "Publisher",
                     "comment": "An entity responsible for making the resource available.",
                     "type": "Property"},
                    ]
                }
        self.assertEqual(want,got)

    def test_stats(self):
        self.env['PATH_INFO'] = "/-/rpubl;stats"
        self.env['HTTP_ACCEPT'] = 'application/json'
        got = json.loads(self.call_wsgi(self.env))
        want = {}
        self.assertEqual(want, got)

    def test_fulltext_query(self):
        self.env['PATH_INFO'] = "/-/publ?q=r%C3%A4tt*"
        self.env['HTTP_ACCEPT'] = 'application/json'
        got = json.loads(self.call_wsgi(self.env))
        want = {}
        self.assertEqual(want, got)

    def test_faceted_query(self):
        self.env['PATH_INFO'] = "/-/publ?publisher.iri=*%2Fregeringskansliet"
        self.env['HTTP_ACCEPT'] = 'application/json'
        got = json.loads(self.call_wsgi(self.env))
        want = {}
        self.assertEqual(want, got)

    def test_complex_query(self):
        self.env['PATH_INFO'] = "/-/publ?q=r%C3%A4tt*&publisher.iri=*%2Fregeringskansliet"
        self.env['HTTP_ACCEPT'] = 'application/json'
        got = json.loads(self.call_wsgi(self.env))
        want = {}
        self.assertEqual(want, got)
