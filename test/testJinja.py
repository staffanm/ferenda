# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from ferenda.manager import setup_logger; setup_logger('CRITICAL')
from ferenda.testutil import RepoTester, FerendaTestCase
from ferenda.compat import unittest, OrderedDict, Mock, MagicMock, patch, call

# SUT
import ferenda.htmlgenerator.jinja2

class API(unittest.TestCase, FerendaTestCase):
    def test_basic(self):
        # create something at base/parsed/123/a.xhtml
        ferenda.generator.jinja.generate("123/a", self.repo.config)
        # check result
        
class Manager(unittest.TestCase, FerendaTestCase):
    def test_basic(self):
        manager.enable(...)
        # create something at base/parsed/123/a.xhtml
        manager.register_action("jgenerate", ferenda.generator.jinja.generate, "generate")
        manager.run(["base", "jgenerate", "123/a"])
        
