# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import os,sys
from ferenda.compat import unittest

from ferenda.manager import setup_logger; setup_logger('CRITICAL')

import shutil
from ferenda import TextReader, util
from ferenda.testutil import RepoTester, file_parametrize

# SUT
from ferenda.sources.legal.se import MyndFskr

class Parse(RepoTester):
    repoclass = MyndFskr

    def parametric_test(self,filename):
        self.repo.config.url = "http://rinfo.lagrummet.se/publ/"
        self.repo.config.urlpath = ""
        reader = TextReader(filename,encoding='utf-8')
        doc = self.repo.make_document("[basefile]")
        self.repo.parse_metadata_from_textreader(reader, doc)
        wantfile = filename.replace(".txt", ".n3")
        self.assertEqualGraphs(wantfile, doc.meta, exact=False)

file_parametrize(Parse, "test/files/myndfskr", ".txt")
