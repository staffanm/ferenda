# This fixture does a bunch of real HTTP request against a selected server (

from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
from builtins import *

# sys
import os
import unittest
import codecs
import re


# 3rdparty
import requests
from bs4 import BeautifulSoup
from rdflib import Graph, URIRef
from rdflib.namespace import DCTERMS

# own


class TestLagen(unittest.TestCase):

    baseurl = os.environ.get("FERENDA_TESTURL", "http://localhost:8080/")

    def assert_status(self, url, code):
        res = requests.get(url, headers={'Accept': 'text/html'})
        self.assertEqual(res.status_code, code)
        return res
    
    def assert200(self, url):
        return self.assert_status(url, 200)

    def assert404(self, url):
        return self.assert_status(url, 404)

    def get(self, url, **kwargs):
        if 'headers' not in kwargs:
            kwargs['headers']={'Accept': 'text/html'}
        return requests.get(url, **kwargs)
    

class TestPaths(TestLagen):

    def test_frontpage(self):
        self.assert200(self.baseurl)

    def test_nonexist(self):
        self.assert404(self.baseurl + "this-resource-does-not-exist")

    def test_specific_sfs(self):
        self.assert200(self.baseurl + "1998:204")

    def test_specific_dv(self):
        self.assert200(self.baseurl + "dom/nja/2015s180") # basefile HDO/Ö6229-14

    def test_specific_keyword(self):
        self.assert200(self.baseurl + "begrepp/Personuppgift")
        
    def test_specific_keyword_tricky(self):
        self.assert200(self.baseurl + "begrepp/Sekundär_sekretessbestämmelse")


class TestPatching(TestLagen):

    def test_file_has_been_patched(self):
        needle = codecs.encode("Fjrebgrp", encoding="rot13")# rot13 of a sensitive name
        res = self.get(self.baseurl + "dom/nja/2002s35")    # case containing sensitive info
        res.raise_for_status()                              # req succeded
        self.assertEqual(-1, res.text.find(needle))         # sensitive name is removed
        self.assertTrue(res.text.index("alert alert-warning patchdescription")) # patching is advertised

class TestAnnotations(TestLagen):

    def test_inbound_links(self):
        res = self.get(self.baseurl + "1998:204/data",
                       headers={'Accept': 'application/rdf+xml'})
        graph = Graph().parse(data=res.text, format="xml")
        resource = graph.resource(URIRef("https://lagen.nu/1998:204"))
        self.assertEqual(str(resource.value(DCTERMS.title)), "Personuppgiftslag (1998:204)")
        # TODO: assert something about inbound relations (PUF, DIFS,
        # prop 2005/06:44, some legal case)


class TestSearch(TestLagen):

    def totalhits(self, soup):
        return int(soup.find("h1").text.split()[0])
        
    def test_basic_search(self):
        # assert that left nav contains a number of results with at least x hits
        res = self.get(self.baseurl + "search/?q=personuppgift")
        soup = BeautifulSoup(res.text, "lxml")
        self.assertGreaterEqual(self.totalhits(soup), 14)
        nav = soup.find("nav", id="toc")
        for repo, minhits in (("dv", 3),
                              ("prop", 3),
                              ("myndfs", 2),
                              ("sou", 2),
                              ("ds", 1),
                              ("mediawiki", 1),
                              ("sfs", 1),
                              ("static", 1)):
            link = nav.find("a", href=re.compile("type=%s$" % repo))
            self.assertIsNotNone(link, "Found no nav link of type=%s" % repo)
            hits = int(link.parent.span.text) # a <span class="badge pull-right">42</span>
            self.assertGreaterEqual(hits, minhits, "Expected more hits for %s" % repo)

    def test_faceted_search(self):
        totalhits = self.totalhits(BeautifulSoup(self.get(
            self.baseurl + "search/?q=personuppgift").text, "lxml"))
        soup = BeautifulSoup(self.get(self.baseurl + "search/?q=personuppgift&repo=dv").text,
                             "lxml")
        self.assertLess(self.totalhits(soup), totalhits)
        # go on and test that the facets in the navbar is as they should


class TestAutocomplete(TestLagen):
    def test_basic_sfs(self):
        res = self.get(self.baseurl + "search/?q=3+§+personuppgiftslag",
                       headers={'Accept': 'application/json'})

