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
from lagen.nu import SFS
from lagen.nu.wsgiapp import WSGIApp

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

    def test_sfs_title(self):
        soup = BeautifulSoup(self.get(self.baseurl + "search/?q=personuppgiftslag").text,
                             "lxml")
        # examine if the first hit is the SFS with that exact
        # title. NB: The SFS should rank above prop 1997/98:44 which
        # has the exact same title. We do this by boosting the sfs index.
        hit = soup.find("section", "hit")
        self.assertEqual(hit.b.a.get("href"), "/1998:204")
        

class TestAutocomplete(TestLagen):
    def test_basic_sfs(self):
        res = self.get(self.baseurl + "api/?q=3+§+personuppgiftslag&_ac=true",
                       headers={'Accept': 'application/json'})
        # returns eg [{'url': 'http://localhost:8080/1998:204#P3',
        #              'label': '3 § personuppgiftslagen',
        #              'desc': 'I denna lag används följande '
        #                      'beteckningar med nedan angiven...'},
        #             {'url': 'http://localhost:8080/dom/nja/2015s180',
        #              'desc': 'NJA 2015 s. 180', # NB! Is :identifier not :title
        #              'label': 'Lagring av personuppgifter '
        #                       '(domstols dagboksblad) i dator har'
        #                       ' ansetts inte omfattad av ...'}]
        self.assertEqual('application/json', res.headers['Content-Type'])
        hits = res.json()
        self.assertEqual(hits[0]['url'], self.baseurl + "1998:204#P3")
        self.assertTrue(hits[0]['desc'].startswith("I denna lag"))
        self.assertGreaterEqual(len(hits), 1) # "3 §
                                              # Personuppgiftslagen"
                                              # only matches one thing
                                              # ("personuppgiftslagen
                                              # 3" matches several)

    def test_shortform_sfs(self):
        res = self.get(self.baseurl + "api/?q=TF+2:&_ac=true",
                       headers={'Accept': 'application/json'})
        hits = res.json()
        self.assertEqual(hits[0]['url'], self.baseurl + "1949:105#K2P1")
        self.assertEqual(hits[0]['label'], "2 kap. 1 § Tryckfrihetsförordning (1949:105)")
        self.assertTrue(hits[0]['desc'].startswith("Till främjande av ett fritt meningsutbyte"))

    def test_incomplete_lawname(self):
        res = self.get(self.baseurl + "api/?q=person&_ac=true",
                       headers={'Accept': 'application/json'})
        hits = res.json()
        self.assertEqual(hits[0]['url'], self.baseurl + "1998:204")
        self.assertEqual(hits[0]['label'], "Personuppgiftslag (1998:204)")

        res = self.get(self.baseurl + "api/?q=TRYCK&_ac=true", # check that case insensitivity works
                       headers={'Accept': 'application/json'})
        hits = res.json()
        self.assertEqual(hits[0]['url'], self.baseurl + "1949:105")
        self.assertEqual(hits[0]['label'], "Tryckfrihetsförordning (1949:105)")

    def test_basic_dv(self):
        res = self.get(self.baseurl + "api/?q=NJA+2015+s+1&_ac=true",
                       headers={'Accept': 'application/json'})
        hits = res.json()
        self.assertEqual(hits[0]['url'], self.baseurl + "dom/nja/2015s166") # FIXME: not first hit when tested against full dataset 
        self.assertEqual(hits[0]['label'], "NJA 2015 s. 166")
        self.assertEqual(hits[0]['desc'], "Brott mot tystnadsplikten enligt tryckfrihetsförordningen.")
        
    def test_basic_prop(self):
        res = self.get(self.baseurl + "api/?q=prop+1997&_ac=true",
                       headers={'Accept': 'application/json'})
        hits = res.json()
        self.assertEqual(hits[0]['url'], self.baseurl + "prop/1997/98:44") # FIXME: Not first hit when tested against full dataset 
        self.assertEqual(hits[0]['label'], "Prop. 1997/98:44")
        self.assertEqual(hits[0]['desc'], "Personuppgiftslag")


class TestACExpand(unittest.TestCase):

    def setUp(self):
        self.wsgiapp = WSGIApp(repos=[SFS(datadir="tng.lagen.nu/data")])

    def test_expand_shortname(self):
        self.assertEqual(self.wsgiapp.expand_partial_ref("TF"),
                         "https://lagen.nu/1949:105#K")

    def test_expand_chapters(self):
        self.assertEqual(self.wsgiapp.expand_partial_ref("TF 1"),
                         "https://lagen.nu/1949:105#K1")

    def test_expand_all_sections(self):
        self.assertEqual(self.wsgiapp.expand_partial_ref("TF 1:"),
                         "https://lagen.nu/1949:105#K1P")

    def test_expand_prefixed_sections(self):
        self.assertEqual(self.wsgiapp.expand_partial_ref("TF 1:1"),
                         "https://lagen.nu/1949:105#K1P1")

    def test_chapterless_expand_all_sections(self):
        self.assertEqual(self.wsgiapp.expand_partial_ref("PUL"),
                         "https://lagen.nu/1998:204#P")

    def test_chapterless_expand_prefixed_sections(self):
        self.assertEqual(self.wsgiapp.expand_partial_ref("PUL 3"),
                         "https://lagen.nu/1998:204#P3")
