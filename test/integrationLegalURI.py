# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import sys, os

import lxml
import rdflib

from ferenda.compat import unittest
if os.getcwd() not in sys.path: sys.path.insert(0,os.getcwd())

from ferenda.sources.legal.se.legaluri import construct, parse, coinstruct_from_graph
from ferenda.testutil import file_parametrize, parametrize

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

class Parse(unittest.TestCase):
    def parametric_test(self,filename):
        with open(filename) as fp:
            uri = fp.read().strip()
        with open(filename.replace(".txt",".py")) as fp:
            parts_repr = " ".join(fp.read().split())
        parts = eval(parts_repr,{"__builtins__":None},globals())
        self.assertEqual(parse(uri),parts)


class Coinstruct(unittest.TestCase):
    atomfile = "test/files/legaluri/publ.atom"
    space = ["ferenda/res/uri/space.n3", "ferenda/res/uri/slugs.n3"]

    def setUp(self):
        # load space
        pass

    def coin_test(self, uri, resourcegraph):
        # get the bnode
        subjects = set(resourcegraph.subjects())
        self.assertEqual(len(subjects), 1)
        coined_uri = coinstruct_from_graph(resourcegraph, subjects.pop())
        self.assertEqual(uri, coined_uri)

# class CoinstructCanonical(Coinstruct):
#     def parametric_test(self, filename):
#         pass

def tests_from_atom(cls, atomfile, base):
    atom = lxml.etree.parse(atomfile).getroot()
    for entry in atom.findall("{http://www.w3.org/2005/Atom}entry"):
        uri = entry.find("{http://www.w3.org/2005/Atom}id").text
        content = entry.find("{http://www.w3.org/2005/Atom}content")
        content.tag = '{http://www.w3.org/1999/02/22-rdf-syntax-ns#}RDF'
        resource_graph = rdflib.Graph().parse(data=lxml.etree.tostring(content))
        name = "test_"+uri.replace(base, "").replace("/", "_").replace(":", "_")
        parametrize(cls, cls.coin_test, name, (uri, resource_graph))

file_parametrize(Construct,"test/files/legaluri",".py")
file_parametrize(Parse,"test/files/legaluri",".txt")
tests_from_atom(Coinstruct, Coinstruct.atomfile,
                "http://rinfo.lagrummet.se/publ/")
