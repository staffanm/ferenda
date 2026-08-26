"""The shared slug helper (`lib/util.text_slug`) -- NFKD fold + separator +
truncation contract, shared by `forarbete/download.title_slug` and
`feeds._slug`. Locks the behaviour that replaced two private copies + a lossy
hand-rolled fold map (rule:lock-in-with-fixture)."""

from ferenda.lib import util
from ferenda.lib.util import text_slug


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

# ---- dating a citation when the document's own date is imprecise -------------

def test_approximate_date_places_a_span_at_its_middle():
    """A partial date becomes the middle of the span it can mean, because that
    is the choice that minimises how far off it can be: a year read as 01-01
    puts every document written in it before a law that took effect that
    January, and 12-31 puts them all after."""
    assert util.approximate_date("2004-05-17") == "2004-05-17"   # already a day
    assert util.approximate_date("2004-04") == "2004-04-15"      # mid-month
    assert util.approximate_date("2004") == "2004-07-01"         # mid-year


def test_approximate_date_reads_a_riksmote_as_the_turn_of_the_year():
    """A riksmöte runs from one autumn into the next summer, so its middle is
    the turn of the year -- and the second year is the first plus one, read off
    the start rather than the two-digit suffix so 1999/2000 works too."""
    assert util.approximate_date("2004/05") == "2005-01-01"
    assert util.approximate_date("1999/2000") == "2000-01-01"
    assert util.approximate_date("2004/2005") == "2005-01-01"


def test_approximate_date_declines_what_names_no_time():
    for value in ("", None, "prop.", "2004-4", "n/a"):
        assert util.approximate_date(value) is None
