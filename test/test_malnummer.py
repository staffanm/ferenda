"""lib/malnummer: what a Swedish case number looks like in running text, and
the one spelling the index and the citation engine both key on."""

import pytest

from ferenda.lib import datasets, malnummer
from ferenda.lib.malnummer import (
    COURT_LETTERS,
    COURT_PHRASES,
    find,
    normalize,
    query_numbers,
    spans,
)


def test_one_spelling_for_the_three_a_court_prints():
    # 877 of the 24,995 held case numbers join the letter to the serial, and
    # Arbetsdomstolen hyphenates it -- one number, spelled three ways
    assert normalize("B732-08") == "B 732-08"
    assert normalize("B 732-08") == "B 732-08"
    assert normalize("A-232-2013") == "A 232-2013"
    # a number without a court letter is already in its only spelling
    assert normalize("4659-11") == "4659-11"
    # the letter is upper-case whatever the citation typed
    assert normalize("t3-08") == "T 3-08"
    # text around a number is left alone
    assert normalize("i mål T3-08 fann HD") == "i mål T 3-08 fann HD"


def test_find_reads_the_number_out_of_a_citation():
    assert find("Högsta domstolens dom 2009-11-03 T 3-08") == ["T 3-08"]
    assert find("HD:s dom i mål T 3-08") == ["T 3-08"]
    # "mål" and "nr" are not court letters, so the number stands alone
    assert find("Regeringsrättens dom den 8 november 2007, mål nr 623-07") \
        == ["623-07"]
    # a referat may collect several cases decided together
    assert find("målen T 369-91 och T 224-91") == ["T 369-91", "T 224-91"]


def test_find_does_not_read_a_date_as_a_case_number():
    # the reason for the lookahead/lookbehind: "2009-11" stands inside the date
    # 2009-11-03, and a decision cited by date alone names no case number
    assert find("2009-11-03") == []
    assert find("Högsta domstolens dom den 3 november 2009") == []
    assert find("brott 2009") == []
    assert find("prop. 1999/2000:100") == []


def test_court_letters_are_the_ones_the_corpus_prints():
    # the four biggest series, and the multi-letter ones a single-letter
    # pattern would cut in half ("UM 1774-07" -> "M 1774-07", another court)
    for letters in ("B", "Ö", "T", "A", "UM", "PMÖÄ"):
        assert letters in COURT_LETTERS
    assert find("UM 1774-07") == ["UM 1774-07"]
    assert find("PMÖ 5342-19") == ["PMÖ 5342-19"]


SNAPSHOT = {
    "courts": {"HDO": ["Högsta domstolen"], "ADO": ["Arbetsdomstolen"],
               "REGR": ["Regeringsrätten"], "MMOD": ["Mark- och miljööverdomstolen"]},
    "numbers": {
        "T 3-08": [["HDO", "2009-11-03", "dom/nja/2009s672"]],
        # one number, two decisions -- the same series number in another court
        "B 53-11": [["ADO", "2012-02-22", "dom/ad/2012:20"],
                    ["HDO", "2011-04-19", "dom/nja/2011s89"]],
        "623-07": [["REGR", "2007-11-08", "dom/ra/2007/not/163"]],
        "M 971-24": [["MMOD", "2026-04-07", "dom/mmd/M971-24/2026-04-07"]],
        # a number the corpus holds under a court no citation here names
        "17-29": [["REGR", "1994-01-01", "dom/ra/1994/not/1"]],
    }}


@pytest.fixture
def snapshot(monkeypatch):
    # `spans` reads the snapshot through the module-level cache (the file is
    # 1.3 MB and `spans` runs per text node), so the test replaces the cache
    # itself and drops any entry the real file left in it
    malnummer._index.cache_clear()          # drop whatever the real file left
    monkeypatch.setattr(malnummer, "_index", lambda: SNAPSHOT)


def test_spans_resolve_the_citation_forms_a_commentary_uses(snapshot):
    # SvJT 2010 s. 94 names the decision this way, and never by its referat
    assert spans("Högsta domstolens dom 2009-11-03 T 3-08") \
        == [(33, 39, "https://lagen.nu/dom/nja/2009s672")]
    assert spans("HD:s dom i mål T 3-08") \
        == [(15, 21, "https://lagen.nu/dom/nja/2009s672")]
    assert spans("Högsta domstolens dom den 3 november 2009 i mål T 3-08") \
        == [(48, 54, "https://lagen.nu/dom/nja/2009s672")]
    # the court's name in the genitive, and a number with no court letter
    assert spans("Regeringsrättens dom den 8 november 2007, mål nr 623-07") \
        == [(49, 55, "https://lagen.nu/dom/ra/2007/not/163")]
    # Mark- och miljööverdomstolen prints Svea hovrätt's name on its own
    # letterhead, so the phrase reaches its series too
    assert spans("SVEA HOVRÄTT DOM Mål nr M 971-24") \
        == [(24, 32, "https://lagen.nu/dom/mmd/M971-24/2026-04-07")]


def test_the_court_decides_which_decision_a_shared_number_means(snapshot):
    # 298 of the 24,411 held numbers name more than one decision
    assert spans("HD:s dom i mål B 53-11") \
        == [(15, 22, "https://lagen.nu/dom/nja/2011s89")]
    assert spans("AD:s dom i mål B 53-11") \
        == [(15, 22, "https://lagen.nu/dom/ad/2012:20")]


def test_spans_link_nothing_it_cannot_pin_down(snapshot):
    # the corpus holds no tingsrätt decisions, and a tingsrätt case number
    # collides with the ones it does hold -- an unnamed court links nothing
    assert spans("Södertörns tingsrätt mål nr B 4318-18") == []
    assert spans("i mål T 3-08") == []
    # the number is a section range, not a case number: no "mål" in front of it,
    # no court letter in it, no date -- so the held 17-29 is not it
    assert spans("Regeringsrättens praxis om 17-29 §§ i lagen") == []
    # a number the corpus does not hold
    assert spans("HD:s dom i mål T 9999-99") == []


def test_a_printed_date_narrows_but_does_not_veto(snapshot, monkeypatch):
    # measured over 2,000 corpus documents: of 255 citations whose number is held
    # and whose court is named, 12 print a date the held decision does not carry
    # -- the date of an interim beslut in the same case ("HD:s beslut den 3 maj
    # 2021 i mål nr T 6358-20", decided 2021-12-10), or of the föredragning. So a
    # date that matches a candidate decides between candidates, and one that
    # matches none is ignored: the court and the number already agree.
    assert spans("Högsta domstolens dom 2009-11-03 T 3-08") \
        == [(33, 39, "https://lagen.nu/dom/nja/2009s672")]
    assert spans("Högsta domstolens dom 2009-11-04 T 3-08") \
        == [(33, 39, "https://lagen.nu/dom/nja/2009s672")]

    # two decisions of one court under one number: the date is all that tells
    # them apart, and without it the citation stays unlinked
    two = {"courts": SNAPSHOT["courts"],
           "numbers": {"A 232-2013": [["ADO", "2014-03-05", "dom/ad/2014:18"],
                                      ["ADO", "2014-09-10", "dom/ad/2014:60"]]}}
    monkeypatch.setattr(malnummer, "_index", lambda: two)
    assert spans("AD:s dom den 5 mars 2014 i mål A-232-2013") \
        == [(31, 41, "https://lagen.nu/dom/ad/2014:18")]
    assert spans("AD:s dom i mål A-232-2013") == []


def test_the_court_phrases_name_courts_the_corpus_files_under():
    # a typo in the phrase table would silently unlink a whole court, so this
    # reads the snapshot fixture (test/files/dv/casenumbers.json, the corpus's
    # own court table) -- which must be there to read
    held = datasets.load_casenumbers()["courts"]
    assert held, "the casenumbers snapshot names no courts"
    for phrase, codes in COURT_PHRASES.items():
        for code in codes:
            assert code in held, "%s -> %s" % (phrase, code)


def test_a_query_needs_more_than_the_shape_of_a_case_number():
    # the search box has no citation around the number to read, and a letterless
    # pair of numbers is also a section range: the corpus holds decisions
    # numbered 17-18, 17-19 and 18-19, so the shape alone turned "17 kap. 17-18
    # §§" into a hit on a Regeringsrätten decision the query matches nowhere else
    assert find("17 kap. 17-18 §§") == ["17-18"]
    assert query_numbers("17 kap. 17-18 §§") == []
    assert query_numbers("prop. 2009/10:80 s. 45-48") == []
    # the court letter, the word "mål", or the number standing alone all count
    assert query_numbers("T 3-08") == ["T 3-08"]
    assert query_numbers("t3-08") == ["T 3-08"]
    assert query_numbers("mål nr 4659-11") == ["4659-11"]
    assert query_numbers("4659-11") == ["4659-11"]
    assert query_numbers("Högsta domstolens dom 2009-11-03 T 3-08") == ["T 3-08"]


def test_a_second_court_between_the_first_and_the_number_takes_it(monkeypatch):
    # measured false link: the number belongs to the tingsrätt the sentence
    # names, and the corpus holds no tingsrätt decisions -- linking it to the HD
    # case that happens to share the number is exactly what COURT_PHRASES is for
    malnummer._index.cache_clear()
    monkeypatch.setattr(malnummer, "_index", lambda: {
        "courts": {"HDO": ["Högsta domstolen"]},
        "numbers": {"B 1-85": [["HDO", "1987-04-01", "dom/nja/1987s187"]]}})
    assert spans("HD:s dom i mål nr B 1-85") \
        == [(18, 24, "https://lagen.nu/dom/nja/1987s187")]
    assert spans("HD prövade Södertörns tingsrätts dom i mål nr B 1-85") == []
