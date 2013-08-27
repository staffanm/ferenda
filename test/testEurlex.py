#!/usr/bin/env python
# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import sys, os

from ferenda.manager import setup_logger; setup_logger('CRITICAL')

from ferenda.testutil import RepoTester, parametrize_repotester

from ferenda.sources.legal.eu import EurlexTreaties, EurlexCaselaw


class Treaties(RepoTester):
    repoclass = EurlexTreaties
    docroot = os.path.dirname(__file__)+"/files/repo/eut"

parametrize_repotester(Treaties)
