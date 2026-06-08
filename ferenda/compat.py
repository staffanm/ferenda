import unittest.mock as unittest_mock
from unittest import mock as unittest_unittest_mock
import unittest


class Mock(unittest_mock.Mock):
    pass


class patch(unittest_mock.patch):
    pass
