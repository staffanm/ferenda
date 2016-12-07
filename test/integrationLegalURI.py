# -*- coding: utf-8 -*-
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
from builtins import *

import sys
import os
import codecs

import lxml
import rdflib

from ferenda.compat import unittest
from ferenda.sources.legal.se.legaluri import (construct, parse,
                                               coinstruct_from_graph)
from ferenda.testutil import file_parametrize, parametrize
from ferenda.thirdparty.coin import URIMinter


class Construct(unittest.TestCase):

    def parametric_test(self,filename):
        with open(filename) as fp:
            testdata = fp.read()
        with open(filename.replace(".py",".txt")) as fp:
            testanswer = fp.read().strip()
        # All test case writers are honorable, noble and thorough
        # persons, but just in case, let's make eval somewhat safer.
        # FIXME: use JSON instead
        testdata = testdata.strip().replace("\r\n", " ")
        d = eval(testdata,{"__builtins__":None},globals())
        uri = construct(d)
        self.assertEqual(uri,testanswer)

#class Parse(unittest.TestCase):
#    def parametric_test(self,filename):
#        with open(filename) as fp:
#            uri = fp.read().strip()
#        with open(filename.replace(".txt",".py")) as fp:
#            parts_repr = " ".join(fp.read().split())
#        parts = eval(parts_repr,{"__builtins__":None},globals())
#        self.assertEqual(parse(uri),parts)


class Coinstruct(unittest.TestCase):
    atomfile = "test/files/legaluri/publ.atom"
    spacefile = "ferenda/sources/legal/se/res/uri/swedishlegalsource.space.ttl"
    slugsfile = "ferenda/sources/legal/se/res/uri/swedishlegalsource.slugs.ttl"

    @classmethod
    def setUpClass(cls):
        with codecs.open(cls.spacefile, encoding="utf-8") as space:
            with codecs.open(cls.slugsfile, encoding="utf-8") as slugs:
                cfg = rdflib.Graph().parse(space,
                                    format="turtle").parse(slugs,
                                                           format="turtle")
        COIN = rdflib.Namespace("http://purl.org/court/def/2009/coin#")
        # select correct URI for the URISpace definition by
        # finding a single coin:URISpace object
        spaceuri = cfg.value(predicate=rdflib.RDF.type, object=COIN.URISpace)
        cls.minter = URIMinter(cfg, spaceuri)

    def coin_test(self, uri, resourcegraph):
        # get the bnode
        rg = resourcegraph
        subjects = set(rg.subjects())
        for subject in subjects:
            # see if this subject is the leaf of a tree, ie it does
            # not have any objects that are themselves subjects in
            # this particular graph
            if not any([list(rg.predicates(subject, x)) for x in subjects]):
                rootnode = subject
                break
            
        coined_uri = coinstruct_from_graph(resourcegraph, rootnode, self.minter)
        self.assertEqual(uri, coined_uri)


def tests_from_atom(cls, atomfile, base):
    atom = lxml.etree.parse(atomfile).getroot()
    for entry in atom.findall("{http://www.w3.org/2005/Atom}entry"):
        uri = entry.find("{http://www.w3.org/2005/Atom}id").text
        content = entry.find("{http://www.w3.org/2005/Atom}content")
        content.tag = '{http://www.w3.org/1999/02/22-rdf-syntax-ns#}RDF'
        resource_graph = rdflib.Graph().parse(data=lxml.etree.tostring(content))
        name = "test_"+uri.replace(base, "").replace("/", "_").replace(":", "_")
        parametrize(cls, cls.coin_test, name, (uri, resource_graph))

class DefaultCoinstruct(Coinstruct): pass

class CustomCoinstruct(Coinstruct):
    atomfile = "test/files/legaluri/lagen.nu.atom"
    spacefile = "lagen/nu/res/uri/swedishlegalsource.space.ttl"
    slugsfile = "lagen/nu/res/uri/swedishlegalsource.slugs.ttl"
    

file_parametrize(Construct,"test/files/legaluri",".py")
tests_from_atom(CustomCoinstruct, CustomCoinstruct.atomfile, 
               "https://lagen.nu/")
tests_from_atom(DefaultCoinstruct, DefaultCoinstruct.atomfile,
                "http://rinfo.lagrummet.se/publ/")
