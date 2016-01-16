# -*- coding: utf-8 -*-
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
from builtins import *

import os
import json

from ferenda.compat import unittest

from ferenda import URIFormatter
import ferenda.uriformats


class FakeParseResult(dict):

    def __init__(self, *args, **kwargs):
        if 'name' in kwargs:
            self._name = kwargs['name']
            del kwargs['name']
        super(FakeParseResult, self).__init__(*args, **kwargs)

    def getName(self):
        return self._name


class ParametricBase(unittest.TestCase):
    def get_formatter(self):
        return ("Base", ferenda.uriformats.generic)

    def parametric_test(self, filename):
        with open(filename) as fp:
            testdata = fp.read()
        d = json.loads(testdata)
        
        d = FakeParseResult(d, name=self.get_formatter()[0])
        uf = URIFormatter(self.get_formatter())
        uri = uf.format(d)

        resultfile = os.path.splitext(filename)[0] + ".txt"
        if os.path.exists(resultfile):
            with open(resultfile) as fp:
                result = fp.read().strip()
        else:
            print("format() returns: %s" % uri)
            self.fail("%s not found" % resultfile)

        self.assertEqual(uri, result)


class URL(ParametricBase):
    def get_formatter(self):
        return ("url", ferenda.uriformats.url)


class EULaw(ParametricBase):
    def get_formatter(self):
        return ("eulaw", ferenda.uriformats.eulaw)

from ferenda.testutil import file_parametrize
file_parametrize(URL, "test/files/uriformat/url", ".json")
# file_parametrize(EULaw,"test/files/uriformat/eulaw", ".json")
