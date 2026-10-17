"""A repeal date belongs to its own sentence, not preceding body citations."""

from pathlib import Path

import pytest

from ferenda.foreskrift import parse as fp
from ferenda.lib.lagrum import sfs_parser

FILES = Path(__file__).parent / "files/foreskrift"
PRIOR_CITATION = (
    "Bestämmelser finns i Europaparlamentets och rådets direktiv av den "
    "15 maj 2014 om marknader för finansiella instrument. ")


@pytest.mark.parametrize(("clause", "expected"), [
    ("Dessa föreskrifter träder i kraft den 3 januari 2018, då "
     "Finansinspektionens föreskrifter (FFFS 2007:16) om värdepappersrörelse "
     "ska upphöra att gälla.", "2018-01-03"),
    ("Finansinspektionens föreskrifter (FFFS 2007:16) om värdepappersrörelse "
     "ska upphöra att gälla den 1 mars 2021.", "2021-03-01"),
    ("Finansinspektionen beslutar att Finansinspektionens föreskrifter "
     "(FFFS 2007:16) om värdepappersrörelse ska upphöra att gälla "
     "vid utgången av november 2022.", "2022-11-30"),
    ("Finansinspektionens föreskrifter (FFFS 2007:16) om värdepappersrörelse "
     "upphör att gälla vid utgången av 2023.", "2023-12-31"),
    ("Finansinspektionens föreskrifter (FFFS 2007:16) om värdepappersrörelse "
     "ska upphöra att gälla.", None),
])
def test_a_backward_repeal_does_not_take_a_preceding_citation_date(clause, expected):
    metadata = fp.extract_metadata(
        PRIOR_CITATION + clause, "", sfs_parser("foreskrift", fp.PARSE_TYPES), fs="fffs")
    assert metadata["upphaver"] == [["https://lagen.nu/fffs/2007:16", expected]]


def test_an_undated_repeal_keeps_the_entry_into_force_date_available():
    text = (PRIOR_CITATION + "Finansinspektionens föreskrifter (FFFS 2007:16) "
            "om värdepappersrörelse ska upphöra att gälla. Dessa föreskrifter "
            "träder i kraft den 3 januari 2018.")
    metadata = fp.extract_metadata(
        text, "", sfs_parser("foreskrift", fp.PARSE_TYPES), fs="fffs")
    assert metadata["ikrafttradandedatum"] == "2018-01-03"
    assert metadata["upphaver"] == [["https://lagen.nu/fffs/2007:16", "2018-01-03"]]


def test_a_repeal_list_keeps_its_announced_date():
    text = (PRIOR_CITATION + "Följande föreskrifter ska upphöra att gälla vid "
            "utgången av februari 2024:\n"
            "1. Finansinspektionens föreskrifter (FFFS 2007:16) om värdepappersrörelse.\n"
            "2. Finansinspektionens föreskrifter (FFFS 2011:37) om rapportering.\n\n")
    metadata = fp.extract_metadata(
        text, "", sfs_parser("foreskrift", fp.PARSE_TYPES), fs="fffs")
    assert metadata["upphaver"] == [
        ["https://lagen.nu/fffs/2007:16", "2024-02-29"],
        ["https://lagen.nu/fffs/2011:37", "2024-02-29"]]


def test_fffs_2017_2_dates_the_repeal_from_its_closing_clause():
    # Official as-published PDF, fetched 2026-10-17:
    # https://www.fi.se/contentassets/5898ce29dec5450aaee888eee84f2dd1/fs1702.pdf
    # The previous 2,500-character backward match included an EU citation
    # dated 15 May 2014. The document's own closing clause says 3 January 2018.
    body, metadata, _notes = fp.parse_pdf(
        FILES / "fffs-2017-2.pdf", "FFFS 2017:2",
        sfs_parser("foreskrift", fp.PARSE_TYPES), fs="fffs")
    assert body
    assert metadata["ikrafttradandedatum"] == "2018-01-03"
    assert metadata["upphaver"] == [["https://lagen.nu/fffs/2007:16", "2018-01-03"]]
