"""lib/pinpoint's anchor vocabulary: the unit a fragment belongs to, and the
reader-facing label both the graph explorer and the search pins print."""

from accommodanda.lib.pinpoint import (
    citation,
    citation_label,
    is_change_marker,
    pinpoint_label,
    short_name,
    unit_anchor,
)


def test_unit_anchor_covers_the_three_fragment_grammars():
    # Swedish K/P: the § is the unit; stycke/punkt/mening tails fold in,
    # mom (O) stays -- it is a citable unit of its own
    assert unit_anchor("K2P16S5") == "K2P16"
    assert unit_anchor("K4P7") == "K4P7"
    assert unit_anchor("P5N3") == "P5"
    assert unit_anchor("P5O2") == "P5O2"
    # CoE: the article is the unit; a repeated article keeps its instance,
    # a lettered point (A3Lh) folds in, a bis-article's letter (A15A) stays
    assert unit_anchor("A6P1") == "A6"
    assert unit_anchor("A3Lh") == "A3"
    assert unit_anchor("A8P1La") == "A8"
    assert unit_anchor("A15A") == "A15A"
    assert unit_anchor("A15AP2") == "A15A"
    assert unit_anchor("A5-2") == "A5-2"
    # EU: dot-joined; a point or stycke folds into its paragraph
    assert unit_anchor("6.1.c") == "6.1"
    assert unit_anchor("9.2.S2") == "9.2"
    assert unit_anchor("32") == "32"
    # a fragment with no finer typed tail is its own unit
    assert unit_anchor("sid39") == "sid39"
    assert unit_anchor("L1988:942") == "L1988:942"


def test_change_markers_are_recognised_not_relabelled():
    assert is_change_marker("L1988:942")
    assert is_change_marker("L2010_1:15")
    assert not is_change_marker("K4P7")
    assert not is_change_marker("")
    # the reader form of a change marker is empty -- there is nothing to say
    assert pinpoint_label("L1988:942") == ""


def test_pinpoint_labels_match_the_pages():
    assert pinpoint_label("K4P7") == "4 kap. 7 §"
    assert pinpoint_label("A6") == "artikel 6"
    assert pinpoint_label("9.2.S2") == "artikel 9.2 andra stycket"


def test_short_name_reads_back_the_acronym_a_label_was_composed_with():
    # the inverse of labels._named, which writes "label (ABBR)"
    assert short_name("Europakonventionen (EKMR)") == "EKMR"
    assert short_name("tredje Genèvekonventionen (GK III)") == "GK III"
    # an ordinary parenthetical is part of the name, not an acronym
    assert short_name("räntelagen") == "räntelagen"
    assert short_name("lag om ändring i brottsbalken (1962:700)") \
        == "lag om ändring i brottsbalken (1962:700)"
    assert short_name(None) == ""


def test_a_citation_names_the_document_as_well_as_the_place():
    # "A6" alone tells a reader nothing; this is the form they can look up
    assert citation_label("EKMR", "A6") == "Artikel 6 EKMR"
    assert citation_label("räntelagen", "P6") == "6 § räntelagen"
    assert citation_label("regeringsformen", "K8P7") == "8 kap. 7 § regeringsformen"
    # the EU anchor is typed by shape -- via human_fragment the stycke tail of
    # "9.2.S2" would have read as a Swedish "2 st"
    assert citation_label("dataskyddsförordningen", "9.2.S2") \
        == "Artikel 9.2 andra stycket dataskyddsförordningen"
    # no fragment: the document itself; no name: the pinpoint alone
    assert citation_label("räntelagen", "") == "Räntelagen"
    assert citation_label("", "A6") == "Artikel 6"


def test_citation_falls_back_to_the_address_it_cannot_name():
    assert citation("https://lagen.nu/1975:635#P6", "räntelagen") \
        == "6 § räntelagen"
    # a document the catalog cannot name descriptively shows its own path --
    # the reader is given an address and can see that it is one
    assert citation("https://lagen.nu/ext/coe/005#A6", None) \
        == "Artikel 6 ext/coe/005"
    assert citation("https://lagen.nu/ext/coe/005#A6", "Europakonventionen") \
        == "Artikel 6 Europakonventionen"
