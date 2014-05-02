# -*- coding: utf-8 -*-
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
import sys
if sys.version_info[:2] == (3,2): # remove when py32 support ends
    import uprefix
    uprefix.register_hook()
    from future.builtins import *
    uprefix.unregister_hook()
else:
    from future.builtins import *

from ferenda.compat import unittest

import shutil
from ferenda import TextReader, util
from ferenda.testutil import RepoTester, file_parametrize

# SUT
from ferenda.sources.legal.se import MyndFskr

class Parse(RepoTester):
    repoclass = MyndFskr
    def setUp(self):
        super(Parse,self).setUp()
        resource_src = "%s/files/myndfskr/resources.xml"%os.path.dirname(__file__)
        resource_dest = self.repo.store.path('resourcelist','intermediate','.rdf')
        util.ensure_dir(resource_dest)
        shutil.copy2(resource_src, resource_dest)
        
    @unittest.skipIf('FERENDA_TEST_NET' not in os.environ,
                     'Not running net tests unless FERENDA_TEST_NET is set')
    def test_download_resource_lists(self):
        graph_path = self.datadir+"/resources.xml"
        graph_path = "resources.xml"
        self.repo.download_resource_lists("http://service.lagrummet.se/var/common",
                                          graph_path)
        self.assertTrue(os.path.exists(graph_path))
        
    
    def parametric_test(self,filename):
        reader = TextReader(filename,encoding='utf-8')
        doc = self.repo.parse_from_textreader(reader,"[basefile]")
        wantfile = filename.replace(".txt", ".n3")
        self.assertEqualGraphs(wantfile, doc.meta)

file_parametrize(Parse, "test/files/myndfskr", ".txt")
