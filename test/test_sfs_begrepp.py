"""Begreppsdefinitioner detection (ferenda.lib.begrepp) -- the ported
find_definitions heuristics, exercised through the SFS rule set. Unit tests
over the five definition cases plus mode detection and URI minting; no
corpus needed."""

from ferenda.lib.markdown import begrepp_uri
from ferenda.sfs.nf import BEGREPP_RULES as b


def test_term_uri_is_the_shared_mint():
    """The term-run URI comes from markdown.begrepp_uri -- the one concept
    mint -- since the hand-rolled sfs copy (term_to_subject) was deleted."""
    assert begrepp_uri("antisladdsystem") == \
        "https://lagen.nu/begrepp/Antisladdsystem"
    # capitalise first letter, spaces -> underscores
    assert begrepp_uri("allmän plats") == "https://lagen.nu/begrepp/Allmän_plats"


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


def test_a_mid_sentence_coinage_announces_the_mode():
    # a paragraf whose only definition sits mid-sentence matches none of the
    # four classic triggers, and used to yield no terms at all
    assert b.paragraf_mode(
        ["Den som bedriver verksamhet (verksamhetsutövare) ska anmäla detta"]
    ) == "parantes"


def test_an_appendix_does_not_coin_terms_mid_sentence():
    """A bilaga is a list of things, not drafting. Of 1,238 mid-sentence
    candidates over 1,500 acts, the 198 inside a bilaga, a table or
    övergångsbestämmelser held nearly all the noise -- annex table cells and
    the English half of a dubbelbeskattningsavtal."""
    text = "Den som bedriver verksamhet (verksamhetsutövare) ska anmäla detta"
    assert b.paragraf_mode([text], in_appendix=True) is None
    assert b.defined_terms(text, "parantes", "stycke", in_appendix=True) == []
    # a parenthesis closing the sentence still counts there, as it always has
    assert b.defined_terms("Inteckning får dödas (dödning).", "parantes",
                           "stycke", in_appendix=True) == ["dödning"]


def test_act_local_labels_are_not_concepts():
    """A blacklist, because no shape test separates them: "myndigheten" is this
    act's word for the body it just named, "spotmarknaden" is a real term, and
    both are lowercase definite-form nouns."""
    assert b.defined_terms("prövas av Verket för innovationssystem "
                           "(myndigheten) enligt 3 §", "parantes", "stycke") == []
    assert b.defined_terms("handel på den nordiska elbörsen (spotmarknaden) "
                           "ska rapporteras", "parantes", "stycke") == \
        ["spotmarknaden"]


def test_normal_term_before_colon():
    assert b.defined_terms("antisladdsystem: ett tekniskt stödsystem",
                           "normal", "stycke") == ["antisladdsystem"]


def test_normal_skips_the_announcing_stycke():
    # the "I denna lag avses med ..." stycke itself is not a term
    assert b.defined_terms("I denna lag avses med följande", "normal", "stycke") == []


def test_normal_disambiguates_embedded_sfs_colon():
    # an SFS number's colon appears before any real ":" delimiter, so the split
    # falls back to the space delimiter (the first word) rather than splitting
    # inside "2018:218"
    assert b.defined_terms("personuppgift enligt lagen (2018:218) viss data",
                           "normal", "stycke") == ["personuppgift"]


def test_brottsdef_and_alt():
    assert b.defined_terms("Den som berövar annan livet, döms för mord till fängelse",
                           None, "stycke") == ["mord"]
    assert b.defined_terms("För miljöbrott döms till böter", None, "stycke") == \
        ["miljöbrott"]


def test_parentes_and_loptext():
    assert b.defined_terms("Inteckning får dödas (dödning).", None, "stycke") == \
        ["dödning"]
    assert b.defined_terms("Med detaljhandel avses i denna lag försäljning",
                           None, "stycke") == ["detaljhandel"]


def test_loptext_does_not_need_i_denna_lag():
    """säkerhetsskyddslagen 1 kap. 2 §: "Med säkerhetsskyddsklassificerade
    uppgifter avses uppgifter som rör säkerhetskänslig verksamhet ..." -- no
    "i denna lag" anywhere. Requiring that tail lost 3 558 definitions in
    1 427 acts, so it is not required."""
    assert b.paragraf_mode(["Med säkerhetsskydd avses skydd av verksamhet"]) \
        == "loptext"
    assert b.defined_terms(
        "Med säkerhetsskyddsklassificerade uppgifter avses uppgifter som rör "
        "säkerhetskänslig verksamhet.", None, "stycke") \
        == ["säkerhetsskyddsklassificerade uppgifter"]
    # "i detta kapitel", "vid tillämpning av 5 §" and no tail at all all count
    assert b.defined_terms("Med fingeravtryck avses även handavtryck.",
                           None, "stycke") == ["fingeravtryck"]


def test_med_opening_an_adverbial_defines_nothing():
    """"Med undantag av de fordon som avses i 6 kap. 3 § ..." reads exactly like
    a definition and is a prepositional phrase. Only these two heads are
    excluded, by name: "stöd till start av näringsverksamhet" is a defined term
    and has the same shape."""
    assert b.defined_terms("Med undantag av de fordon som avses i 6 kap. 3 § "
                           "får endast registreras.", None, "stycke") == []
    assert b.defined_terms("Med hjälp av ett underhållssystem som avses i "
                           "2 kap. 10 § ska den göras.", None, "stycke") == []
    assert b.defined_terms("Med stöd till start av näringsverksamhet avses "
                           "stöd till en näringsidkares försörjning.",
                           None, "stycke") == ["stöd till start av näringsverksamhet"]


def test_a_loptext_term_drops_its_article_and_its_scope():
    """The definiendum is the term, not the sentence around it: "Med ett träds
    grundyta avses ..." defines grundyta, and "Med dotterbolag enligt första
    stycket 3 avses ..." defines dotterbolag. Both would otherwise mint a
    begrepp page under a name no one looks up."""
    assert b.defined_terms("Med ett elektroniskt dokument avses en upptagning.",
                           None, "stycke") == ["elektroniskt dokument"]
    assert b.defined_terms("Med dotterbolag enligt första stycket 3 avses "
                           "dotterbolag som organisationen äger.",
                           None, "stycke") == ["dotterbolag"]
    # the four other modes write the bare term already, so they are untouched
    assert b.defined_terms("Inteckning får dödas (en dödning).",
                           None, "stycke") == ["en dödning"]


def test_one_sentence_can_define_two_terms():
    """säkerhetsskyddslagen 2 kap. 1 §. The rule used to require the parenthesis
    to close the sentence, so säkerhetsskyddsanalys was a defined term and
    verksamhetsutövare -- coined by the same sentence -- was not."""
    text = ("Den som till någon del bedriver säkerhetskänslig verksamhet "
            "(verksamhetsutövare) ska utreda behovet av säkerhetsskydd "
            "(säkerhetsskyddsanalys).")
    assert b.paragraf_mode([text]) == "parantes"
    assert b.defined_terms(text, "parantes", "stycke") == \
        ["säkerhetsskyddsanalys", "verksamhetsutövare"]


def test_mid_sentence_parenthesis_must_read_as_a_term():
    """Accepting every mid-sentence parenthesis minted "EEG", "nr 570" and
    "1993" across the corpus. A coinage is lowercase, digit-free and not a list
    marker; the sentence-final rule is unchanged and still takes any shape."""
    for noise in ("Gemenskapen (EEG) ska höras",
                  "rådets förordning (nr 570) gäller",
                  "beslutet (1993) upphör",
                  "leden (iii) och (iv) tillämpas"):
        assert b.defined_terms(noise, "parantes", "stycke") == []
    # sentence-final keeps minting whatever shape it has, as it always did
    assert b.defined_terms("Detta gäller Europeiska ekonomiska samarbetsområdet "
                           "(EES).", "parantes", "stycke") == ["EES"]


def test_listelement_strips_prefix():
    assert b.defined_terms("1. antisladdsystem: ett system", "normal",
                           "listelement") == ["antisladdsystem"]


def test_tabellrad_header_is_not_a_term():
    assert b.defined_terms("Beteckning", "normal", "tabellrad") == []
    assert b.defined_terms("Begrepp", "normal", "tabellrad") == []
    assert b.defined_terms("Förskingring", "normal", "tabellrad") == ["Förskingring"]
    # a change note in the cell is not a term
    assert b.defined_terms("Lag (2009:400).", "normal", "tabellrad") == []


def test_overlong_term_rejected():
    long = "x" * 70
    assert b.defined_terms("%s: en definition" % long, "normal", "stycke") == []


def test_no_mode_no_term():
    # without a mode, a stycke with a colon is not treated as a definition
    assert b.defined_terms("antisladdsystem: ett system", None, "stycke") == []


def test_formula_prefix_stripped_from_term():
    # a colon-list definition that swept a formula prefix recovers the real term
    assert b.defined_terms("*/k/ utjämningsbelopp: ett belopp", "normal",
                           "stycke") == ["utjämningsbelopp"]


def test_parenthetical_clarifier_names_the_head_not_the_paren():
    # "Behandling (av personuppgifter)" -- head is the term, paren is a clarifier
    assert b.defined_terms("Behandling (av personuppgifter).", None, "stycke") \
        == ["Behandling av personuppgifter"]
    # a real coinage still uses the parenthetical
    assert b.defined_terms("Inteckning får dödas (dödning).", None, "stycke") \
        == ["dödning"]


def test_term_never_starts_with_a_preposition():
    # a mis-captured prepositional fragment is dropped, not minted as a concept
    assert b.defined_terms("av personuppgifter: data", "normal", "stycke") == []


def test_ska_forstas_keeps_the_auxiliary_out_of_the_term():
    """PMFS 2017:10 writes "Med utbildningsverksamhet ska förstås ..." -- the
    auxiliary belongs to the verb, not the definiendum. Found in the 50-term
    föreskrift adjudication sample (2026-08-27)."""
    assert b.defined_terms("Med utbildningsverksamhet ska förstås bedrivandet "
                           "av föreskriven utbildning.", None, "stycke") == \
        ["utbildningsverksamhet"]
    assert b.defined_terms("Med butikskontroll skall förstås personell "
                           "bevakning.", None, "stycke") == ["butikskontroll"]


def test_a_table_cells_trailing_colon_is_not_part_of_the_term():
    # SKSFS 2010:3 sets its definition table as "Naturvårdsbränning: ..." cells
    assert b.defined_terms("Naturvårdsbränning:", "normal", "tabellrad") == \
        ["Naturvårdsbränning"]


def test_a_letterless_term_is_a_designation_not_a_concept():
    """ELSÄK-FS 2018:1 sets a definitions-mode table whose first column holds
    författningsnummer; "2019:1" minted as a term and crashed relate's
    sentence-finder. A term must contain at least one letter."""
    assert b.defined_terms("2019:1", "normal", "tabellrad") == []
    assert b.defined_terms("1. 2019:1 om elsäkerhet", "normal",
                           "listelement") == []
    assert b.defined_terms("Förskingring", "normal", "tabellrad") == \
        ["Förskingring"]
