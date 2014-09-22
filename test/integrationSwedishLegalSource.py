import unittest
import datetime

from ferenda.sources.legal.se import SwedishLegalSource


class TestSwedishLegalSource(unittest.TestCase):
    def test_parse_swedish_date(self):
        repo = SwedishLegalSource()
        self.assertEqual(repo.parse_swedish_date("3 februari 2010"),
                         datetime.date(2010, 2, 3))
        self.assertEqual(repo.parse_swedish_date("15 sept 1980"),
                         datetime.date(1980, 9, 15))
