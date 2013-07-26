#!/usr/bin/env python
# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import sys, os
if sys.version_info < (2,7,0):
    import unittest2 as unittest
else:
    import unittest
from ferenda.manager import setup_logger; setup_logger('CRITICAL')
from ferenda.testutil import RepoTester, parametrize_repotester
from ferenda.sources.tech import W3Standards

class TestW3C(RepoTester):
    repoclass = W3Standards
    docroot = os.path.dirname(__file__)+"/files/repo/w3c"

parametrize_repotester(TestW3C)

