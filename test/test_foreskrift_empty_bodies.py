"""Short notices must retain the prose after their decision date."""

from pathlib import Path

from ferenda.foreskrift import parse as fp
from ferenda.foreskrift.model import Block
from ferenda.lib.lagrum import sfs_parser
from ferenda.lib.text import node_text


def test_decision_date_only_ends_the_masthead_when_no_prose_follows():
    for ending in (".", ";", ""):
        blocks = [Block("stycke", "beslutade den 22 februari 2010" + ending, 1),
                  Block("stycke", "Dessa föreskrifter gäller ordningsbot.", 1)]
        assert fp._body_start(blocks) == (1, True)
    blocks = [Block("stycke", "Riksåklagaren beslutade den 22 februari 2010 "
                    "om ändring i föreskrifterna.", 1)]
    assert fp._body_start(blocks) == (0, True)
    assert fp._ingress_start(blocks, 0) == 0


def test_a_publication_date_after_the_decision_date_stays_in_the_masthead():
    # PDF extraction can append the publication-date column after the
    # decision date. Its month name is not substantive notice text.
    blocks = [Block("stycke", "Utkom från trycket beslutade den 31 januari 2022. "
                    "den 4 februari 2022", 1),
              Block("stycke", "Dessa föreskrifter gäller ordningsbot.", 1)]
    assert fp._body_start(blocks) == (1, True)


def test_aafs_2010_1_retains_the_notice_body():
    # Official PDF downloaded 2026-10-17:
    # https://www.aklagare.se/globalassets/dokument/
    # afs-forfattningssamling-alla-utom-konsoliderade/afs-2010-01.pdf
    # Both body sentences reflow into one block. Dropping the whole decision
    # block used to publish an empty structure, despite the PDF having text.
    pdf = Path(__file__).parent / "files/foreskrift/aafs-2010-1.pdf"
    body, metadata, notes = fp.parse_pdf(
        pdf, "ÅFS 2010:1", sfs_parser("foreskrift", fp.PARSE_TYPES), fs="aafs")
    assert len(body) == 1
    assert body[0]["type"] == "stycke"
    assert node_text(body[0]) == (
        "Riksåklagaren beslutade den 22 februari 2010 om ändring i Riksåklagarens "
        "föreskrifter (1999:178) om ordningsbot för vissa brott. Författningen som "
        "träder i kraft den 31 mars 2010 har kungjorts i Svensk Författningssamling "
        "(2010:114).")
    assert metadata["beslutsdatum"] == "2010-02-22"
    assert metadata["ikrafttradandedatum"] == "2010-03-31"
    assert notes == []


def test_a_decision_date_in_the_first_provision_does_not_hide_the_ingress():
    blocks = [Block("rubrik", "Myndighetens föreskrifter om stöd;", 1),
              Block("stycke", "Myndigheten föreskriver följande med stöd av lagen.", 1),
              Block("paragraf", "1 § De föreskrifter myndigheten beslutade den "
                    "22 februari 2010 ska tillämpas.", 1, num="1")]
    assert fp._body_start(blocks) == (2, True)
    assert fp._ingress_start(blocks, 2) == 1
