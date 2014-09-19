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
# subrepos, normally used through a container CompositeRepository
from ferenda.sources.legal.se.propositioner import PropRegeringen
from ferenda.sources.legal.se.direktiv import DirTrips

for cls in (Keyword, Skeleton, MediaWiki,
            RFC, W3Standards, PEP,
            EurlexCaselaw, EurlexTreaties,
            ARN, Direktiv, Ds, DV, JK, JO, Kommitte, MyndFskr, Propositioner, Regeringen, Riksdagen, SFS, SOU, SwedishLegalSource,
            PropRegeringen, DirTrips):
    # Create a new class, based on RepoTester, on the fly.
    d = {'repoclass': cls,
         'docroot': os.path.dirname(__file__)+"/files/repo/" + cls.alias}
    name = 'Test'+cls.__name__
    if six.PY2:
        name = name.encode()
    testcls = type(name, (RepoTester,), d)
    # testcls.filename_to_basefile = lambda x, y: "2"
    globals()[name] = testcls
    parametrize_repotester(testcls)
        
