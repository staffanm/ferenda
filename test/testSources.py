#!/usr/bin/env python
# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import sys, os
if sys.version_info < (2,7,0):
    import unittest2 as unittest
else:
    import unittest
# if os.getcwd() not in sys.path: sys.path.insert(0,os.getcwd())

import datetime

try:
    # assume we're on py3.3 and fall back if not
    from unittest.mock import Mock, patch
except ImportError:
    from mock import Mock, patch

from ferenda.sources.general import Keyword, Skeleton, Wiki
from ferenda.sources.tech import RFC, W3Standards, PEP
from ferenda.sources.legal.eu import EurlexCaselaw, EurlexTreaties
from ferenda.sources.legal.se import ARN, Direktiv, Ds, DV, JK, JO, Kommitte, MyndFskr, Propositioner, Regeringen, Riksdagen, SFS, SKVFS, SOU, SwedishLegalSource

class TestSwedishLegalSource(unittest.TestCase):
    def test_parse_swedish_date(self):
        repo = SwedishLegalSource()
        self.assertEqual(repo.parse_swedish_date("3 februari 2010"), datetime.date(2010,2,3))
            
