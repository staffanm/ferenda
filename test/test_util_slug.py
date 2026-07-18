"""The shared slug helper (`lib/util.text_slug`) -- NFKD fold + separator +
truncation contract, shared by `forarbete/download.title_slug` and
`feeds._slug`. Locks the behaviour that replaced two private copies + a lossy
hand-rolled fold map (rule:lock-in-with-fixture)."""

from accommodanda.lib.util import text_slug


def test_folds_swedish_diacritics_to_ascii():
    assert text_slug("Ändrade regler för Försäkringskassan") \
        == "andrade-regler-for-forsakringskassan"


def test_separator_is_configurable():
    assert text_slug("Å Ä Ö", sep="_") == "a_a_o"


def test_collapses_runs_and_strips_edges():
    assert text_slug("  Prop. 2024/25:1 — m.m.  ") == "prop-2024-25-1-m-m"


def test_nfkd_removes_invisible_formatting_not_word_break():
    # a soft hyphen (U+00AD) is invisible formatting, so it vanishes rather than
    # becoming a separator -- the strict improvement over the old fold map
    assert text_slug("våldsbrott") == "valdsbrott"
    assert text_slug("vålds­brott") == "valdsbrott"


def test_maxlen_truncates_and_restrips():
    # a cut landing mid-separator must not leave a trailing sep
    assert text_slug("aaaa bbbb cccc", maxlen=5) == "aaaa"
    assert text_slug("aaaa bbbb cccc", maxlen=9) == "aaaa-bbbb"


def test_coerces_non_str():
    assert text_slug(2024, sep="_") == "2024"
