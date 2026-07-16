"""Format-neutral normalization for POI-extracted Word text."""

from accommodanda.dv.word import _clean


def test_clean_removes_word_field_controls_without_separating_text():
    assert _clean("Lnr:RÅ2000not\x13 \x15\x13 \x1557") == \
        "Lnr:RÅ2000not 57"


def test_clean_removes_stray_c0_control_from_header_value():
    assert _clean("\x01RH 2006:13") == "RH 2006:13"
