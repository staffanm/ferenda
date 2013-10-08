from ferenda.compat import unittest
from ferenda import util
import doctest
def load_tests(loader,tests,ignore):
    tests.addTests(doctest.DocTestSuite(util))
    return tests

