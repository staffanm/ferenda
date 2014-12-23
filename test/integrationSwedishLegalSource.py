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
        self.assertEqual(repo.parse_swedish_date("15 sept 1980"),
                         datetime.date(1980, 9, 15))
        self.assertEqual(repo.parse_swedish_date("8 dec. 1997"),
                         datetime.date(1997, 12, 8))

    def test_parse_iso_date(self):
        repo = SwedishLegalSource()
        self.assertEqual(repo.parse_iso_date("2010-02-03"),
                         datetime.date(2010, 2, 3))
        # handle spurious spaces
        self.assertEqual(repo.parse_iso_date("2010- 02 -03"),
                         datetime.date(2010, 2, 3))
