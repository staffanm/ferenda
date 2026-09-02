"""The one SFS paragraf-anchor grammar every source mints against."""

from ferenda.forarbete.kommentar import paragraf_fragment
from ferenda.lib.lagrum import english_pinpoint_spans
from ferenda.lib.sfs_anchor import paragraf_anchor, unique_paragraf_anchor
from ferenda.sfs.correspond import _anchor
from ferenda.wiki.parse import heading_fragment


def test_the_anchor_grammar():
    assert paragraf_anchor("4", "6") == "K4P6"
    assert paragraf_anchor(None, "5b") == "P5b"
    assert paragraf_anchor("", "5") == "P5"
    assert paragraf_anchor("5", "2", "3") == "K5P2S3"
    assert paragraf_anchor(5, 2) == "K5P2"          # numbers, not only strings


def test_whitespace_is_removed_from_both_ordinals():
    # a lettered chapter is printed "4 a kap." and a lettered § "7 a §"; the
    # anchor the citation engine mints for either carries no space, so the
    # provision side must not either (föreskrift minted "K4 aP1" and was
    # unreachable)
    assert paragraf_anchor("4 a", "1") == "K4aP1"
    assert paragraf_anchor("1", "7 a") == "K1P7a"


def test_unique_anchor_breaks_a_clash_and_records_it():
    seen = set()
    assert unique_paragraf_anchor(None, "3", seen) == "P3"
    assert unique_paragraf_anchor(None, "3", seen) == "P3-2"
    assert unique_paragraf_anchor(None, "3", seen) == "P3-3"
    assert seen == {"P3", "P3-2", "P3-3"}


def test_every_caller_mints_the_same_string():
    # the SFS renumbering layer, the författningskommentar extractor, the
    # wiki commentary headings and the English-text pinpoint matcher
    assert _anchor("4", "6") == "K4P6"
    assert _anchor("12") == "K12"                   # a whole chapter
    assert paragraf_fragment("1", "7 a") == "K1P7a"
    assert paragraf_fragment(None, "3") == "P3"
    assert paragraf_fragment("1", None) is None
    assert heading_fragment("1 kap. 1 c §") == "K1P1c"
    assert heading_fragment("21 kap 1 § 2 st") == "K21P1S2"
    assert heading_fragment("1 §") == "P1"
    assert english_pinpoint_spans(
        "Miljöbalken (SFS 1998:808), Chapter 5, Section 2 para. 3",
        [(25, "https://lagen.nu/1998:808")]) == [
            (28, 56, "https://lagen.nu/1998:808#K5P2S3")]
