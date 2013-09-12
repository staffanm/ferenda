#!/usr/bin/env python
# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import sys
import os
import datetime

import six

from ferenda.compat import unittest
from ferenda.compat import Mock, patch

from ferenda.testutil import RepoTester, parametrize_repotester
from ferenda.sources.general import Keyword, Skeleton, MediaWiki
from ferenda.sources.tech import RFC, W3Standards, PEP
from ferenda.sources.legal.eu import EurlexCaselaw, EurlexTreaties
from ferenda.sources.legal.se import ARN, Direktiv, Ds, DV, JK, JO, Kommitte, MyndFskr, Propositioner, Regeringen, Riksdagen, SFS, SOU, SwedishLegalSource
from ferenda.sources.legal.se.propositioner import PropPolo

class TestSwedishLegalSource(unittest.TestCase):
    def test_parse_swedish_date(self):
        repo = SwedishLegalSource()
        self.assertEqual(repo.parse_swedish_date("3 februari 2010"), datetime.date(2010,2,3))


for cls in (Keyword, Skeleton, MediaWiki,
            RFC, W3Standards, PEP,
            EurlexCaselaw, EurlexTreaties,
            ARN, Direktiv, Ds, DV, JK, JO, Kommitte, MyndFskr, Propositioner, Regeringen, Riksdagen, SFS, SOU, SwedishLegalSource,
            PropPolo):
    d = {'repoclass': cls,
         'docroot': os.path.dirname(__file__)+"/files/repo/" + cls.alias}
    name = 'Test'+cls.__name__
    if six.PY2:
        name = name.encode()
    testcls = type(name, (RepoTester,), d)
    globals()[name] = testcls
    parametrize_repotester(testcls)
        
