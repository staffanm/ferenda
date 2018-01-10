# -*- coding: utf-8 -*-
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
from builtins import *

import os
import sys

from ferenda.testutil import RepoTester, parametrize_repotester
from ferenda.sources.general import Keyword, Skeleton  # MediaWiki

# test cases right now expects to see literals language-typed as @sv,
# therefore we use the derived Lagen.nu-specific subclass.
from lagen.nu import LNMediaWiki as MediaWiki

from ferenda.sources.tech import RFC, W3Standards, PEP
from ferenda.sources.legal.eu import EURLexCaselaw, EURLexTreaties, EURLexActs
from ferenda.sources.legal.se import (Direktiv, JK, Kommitte, MyndFskrBase,
                                      Propositioner, Regeringen, Riksdagen,
                                      SwedishLegalSource)

from lagen.nu import ARN, DV, SFS, SOU, Ds, JO

# subrepos, normally used through a container CompositeRepository
from lagen.nu.direktiv import DirRegeringen, DirTrips
from lagen.nu.propositioner import PropRegeringen, PropRiksdagen, PropTrips
from lagen.nu.sou import SOURegeringen, SOUKB
from lagen.nu.ds import DsRegeringen

for cls in (Keyword, Skeleton, MediaWiki, RFC, W3Standards, PEP,
            EURLexCaselaw, EURLexTreaties, EURLexActs, ARN, Direktiv, Ds, DV, JK,
            JO, Kommitte, MyndFskrBase, Propositioner, Regeringen,
            Riksdagen, SwedishLegalSource, PropRegeringen,
            PropRiksdagen, PropTrips, DirTrips, DirRegeringen,
            DsRegeringen, SOURegeringen, SOUKB, SFS):
    # Create a new class, based on RepoTester, on the fly.
    d = {'repoclass': cls,
         'docroot': os.path.dirname(__file__)+"/files/repo/" + cls.alias,
         'repoconfig': {'compress': 'bz2'}
    }
    name = 'Test'+cls.__name__
    if sys.version_info[0] < 3:
        name = name.encode()
    testcls = type(name, (RepoTester,), d)
    globals()[name] = testcls
    parametrize_repotester(testcls)
        
