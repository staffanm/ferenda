"""Begreppsdefinitioner detection (accommodanda.sfs.begrepp) -- the ported
find_definitions heuristics. Unit tests over the five definition cases plus
mode detection and URI minting; no corpus needed."""

from accommodanda.sfs import begrepp as b


def test_term_to_subject():
    assert b.term_to_subject("antisladdsystem") == \
        "https://lagen.nu/begrepp/Antisladdsystem"
    # capitalise first letter, spaces -> underscores
    assert b.term_to_subject("allmän plats") == "https://lagen.nu/begrepp/Allmän_plats"


def test_paragraf_mode_triggers():
    assert b.paragraf_mode(["I denna lag avses med följande"]) == "normal"
    assert b.paragraf_mode(["I denna balk betyder ord"]) == "normal"
    assert b.paragraf_mode(["Den som dödar, döms för mord till fängelse"]) == \
        "brottsrubricering"
    assert b.paragraf_mode(["För miljöbrott döms till böter"]) == "brottsrubricering"
    assert b.paragraf_mode(["Med detaljhandel avses i denna lag x"]) == "loptext"
    assert b.paragraf_mode(["Ett vanligt stycke utan definition."]) is None
    # a re_definitions match on any stycke upgrades the mode to normal
    assert b.paragraf_mode(["en inledning", "I denna lag avses med y"]) == "normal"


def test_normal_term_before_colon():
    assert b.defined_term("antisladdsystem: ett tekniskt stödsystem",
                          "normal", "stycke") == "antisladdsystem"


def test_normal_skips_the_announcing_stycke():
    # the "I denna lag avses med ..." stycke itself is not a term
    assert b.defined_term("I denna lag avses med följande", "normal", "stycke") is None


def test_normal_disambiguates_embedded_sfs_colon():
    # an SFS number's colon appears before any real ":" delimiter, so the split
    # falls back to the space delimiter (the first word) rather than splitting
    # inside "2018:218"
    term = b.defined_term("personuppgift enligt lagen (2018:218) viss data",
                          "normal", "stycke")
    assert term == "personuppgift"


def test_brottsdef_and_alt():
    assert b.defined_term("Den som berövar annan livet, döms för mord till fängelse",
                          None, "stycke") == "mord"
    assert b.defined_term("För miljöbrott döms till böter", None, "stycke") == "miljöbrott"


def test_parentes_and_loptext():
    assert b.defined_term("Inteckning får dödas (dödning).", None, "stycke") == "dödning"
    assert b.defined_term("Med detaljhandel avses i denna lag försäljning",
                          None, "stycke") == "detaljhandel"


def test_listelement_strips_prefix():
    assert b.defined_term("1. antisladdsystem: ett system", "normal",
                          "listelement") == "antisladdsystem"


def test_tabellrad_header_is_not_a_term():
    assert b.defined_term("Beteckning", "normal", "tabellrad") is None
    assert b.defined_term("Begrepp", "normal", "tabellrad") is None
    assert b.defined_term("Förskingring", "normal", "tabellrad") == "Förskingring"
    # a change note in the cell is not a term
    assert b.defined_term("Lag (2009:400).", "normal", "tabellrad") is None


def test_overlong_term_rejected():
    long = "x" * 70
    assert b.defined_term("%s: en definition" % long, "normal", "stycke") is None


def test_no_mode_no_term():
    # without a mode, a stycke with a colon is not treated as a definition
    assert b.defined_term("antisladdsystem: ett system", None, "stycke") is None


def test_formula_prefix_stripped_from_term():
    # a colon-list definition that swept a formula prefix recovers the real term
    assert b.defined_term("*/k/ utjämningsbelopp: ett belopp", "normal",
                          "stycke") == "utjämningsbelopp"


def test_parenthetical_clarifier_names_the_head_not_the_paren():
    # "Behandling (av personuppgifter)" -- head is the term, paren is a clarifier
    assert b.defined_term("Behandling (av personuppgifter).", None, "stycke") \
        == "Behandling av personuppgifter"
    # a real coinage still uses the parenthetical
    assert b.defined_term("Inteckning får dödas (dödning).", None, "stycke") \
        == "dödning"


def test_term_never_starts_with_a_preposition():
    # a mis-captured prepositional fragment is dropped, not minted as a concept
    assert b.defined_term("av personuppgifter: data", "normal", "stycke") is None
