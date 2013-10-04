# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import sys, os

from ferenda.compat import unittest
if os.getcwd() not in sys.path: sys.path.insert(0,os.getcwd())

from ferenda.legaluri import construct,parse
from ferenda.testutil import file_parametrize

class Construct(unittest.TestCase):
    def parametric_test(self,filename):
        with open(filename) as fp:
            testdata = fp.read()
        with open(filename.replace(".py",".txt")) as fp:
            testanswer = fp.read().strip()
        
        # All test case writers are honorable, noble and thorough
        # persons, but just in case, let's make eval somewhat safer.
        # FIXME: use ast.literal_eval instead
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


file_parametrize(Construct,"test/files/legaluri",".py")
file_parametrize(Parse,"test/files/legaluri",".txt")
