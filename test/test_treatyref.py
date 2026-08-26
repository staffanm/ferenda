"""The shared treaty-citation matcher (`lib/treatyref`).

One matcher serves both international courts: `icj` names the instrument it
applies, `icc` cites the Rome Statute by article on nearly every page. Every
case below is a form taken from the real corpus -- the ICC's 269 decisions
carry 13,887 "article N ... of the Statute" citations, and getting the binding
wrong files them against the wrong treaty rather than against none.
"""

import re

from ferenda.lib import treatyref

STATUTE = (("the Statute", "icrc/585"),)
EXT = "https://lagen.nu/ext/"


def _refs(text, extra=()):
    return [(r["uri"].replace(EXT, ""), r["text"])
            for r in treatyref.references(text, extra=extra)]


def test_an_article_resolves_to_the_provision():
    """The whole point: `#A74` is the trial-judgment provision, not the treaty."""
    assert _refs("under article 74 of the Statute", STATUTE) == \
        [("icrc/585#A74", "the Statute, article 74")]


def test_a_roman_article_number_resolves_too():
    """The Genocide Convention runs Article I to Article XIX and the ICJ cites
    it that way. An Arabic-only pattern missed the corpus's most-cited
    instrument entirely -- 91 of the ICJ's references are to this treaty."""
    assert _refs("Article II of the Genocide Convention") == \
        [("untc/I-1021#AII", "Genocide Convention, article II")]
    assert [uri for uri, _ in _refs("Articles I and III of the Genocide Convention")] == \
        ["untc/I-1021#AI", "untc/I-1021#AIII"]


def test_the_numeral_is_matched_case_sensitively():
    """A roman article number is always set in capitals. Matching it
    case-insensitively would read the "i" of "article i" as a numeral."""
    assert treatyref.RE_ARTICLE.match("Article II")
    assert treatyref.RE_ARTICLE.match("article 74")
    assert not treatyref.RE_ARTICLE.match("article i")


def test_the_nearest_instrument_wins_and_direction_decides():
    """"article 3 common to the Geneva Conventions" is an article of the Geneva
    Conventions however recently the Rome Statute was named. Binding to every
    name in range filed it as Rome Statute article 3."""
    uris = [uri for uri, _ in _refs(
        "of the Rome Statute. See also article 3 common to the Geneva Conventions.",
        STATUTE)]
    # the Rome Statute is named, so it is referenced -- but as the instrument,
    # because no article of its own is cited here
    assert uris == ["icrc/365#A3", "icrc/370#A3", "icrc/375#A3", "icrc/380#A3",
                    "icrc/585"]
    assert "icrc/585#A3" not in uris


def test_a_shared_name_resolves_to_every_instrument_it_names():
    """Common article 3 really is an article of all four conventions, so it
    resolves to four references rather than to a guess at which was meant."""
    assert len(_refs("article 3 common to the Geneva Conventions")) == 4


def test_a_preceding_name_may_still_claim_its_article():
    """The other form the courts write."""
    assert _refs("The Statute, article 74, requires a written decision.", STATUTE) == \
        [("icrc/585#A74", "the Statute, article 74")]


def test_an_article_never_binds_backwards_past_its_own_instrument():
    """The ICC writes "International Covenant *of* Civil and Political Rights"
    where the curated name says "on". With the name unmatched, article 9 bound
    backwards to "the Statute" and was filed as Rome Statute article 9."""
    assert _refs("under article 9 (3) of the International Covenant of Civil "
                 "and Political Rights", STATUTE) == \
        [("untc/I-14668#A9", "International Covenant of Civil and Political "
                             "Rights, article 9")]
    # and an instrument the corpus does not hold yields nothing, not a guess
    assert _refs("under article 9 (3) of the Convention on Something We Lack",
                 STATUTE) == []


def test_a_caller_s_short_form_is_only_its_own():
    """Inside an ICC decision "the Statute" is the Rome Statute. In an ICJ
    judgment the same words mean the Statute of the Court, which the corpus
    does not hold -- so it must resolve to nothing there."""
    assert _refs("article 36 of the Statute of the Court") == []
    assert _refs("article 74 of the Statute", STATUTE)


def test_one_reference_per_instrument_and_article():
    """A decision applying article 74 twenty times states one relation to it."""
    assert len(_refs("article 74 of the Statute. " * 20, STATUTE)) == 1


def test_an_article_the_instrument_lacks_is_not_a_provision():
    """"the Additional Protocols, article 85" binds to both, but Protocol II
    ends at article 28. Naming the instrument is the honest answer where #A85
    was a link to nothing: 97 references pointed at an absent anchor before the
    range check. (The family name beside it is what lets the generic ordinal
    name bind at all -- see the generic-context tests below.)"""
    refs = _refs("article 85 of the Additional Protocols "
                 "to the Geneva Conventions")
    assert ("icrc/470#A85", "Additional Protocols, article 85") in refs
    assert ("icrc/475", "Additional Protocols") in refs
    assert not any(uri == "icrc/475#A85" for uri, _ in refs)


def test_an_annexed_instrument_anchors_under_its_annex():
    """The Hague Regulations are the annex to Convention (IV), so article 42 is
    #Annex42 -- #A42 pointed at nothing. The 1907 Convention is the target
    because it is the one in force and the one a court cites; the entry's own
    `note` in treaty_names.json carries that reasoning and the count behind
    it, because the 1899 Convention annexes Regulations with the same title."""
    assert _refs("article 42 of the Hague Regulations") == \
        [("icrc/195#Annex42", "Hague Regulations, article 42")]


def test_an_enumeration_cites_every_article_it_names():
    """"articles 15, 53, 54, 58 and 61 (5) of the Statute" states five
    relations. Reading only the first two left 568 article numbers across 120
    ICC decisions unlinked."""
    uris = [uri for uri, _ in _refs(
        "pursuant to articles 15, 53, 54, 58 and 61 (5) of the Statute", STATUTE)]
    assert uris == ["icrc/585#A15", "icrc/585#A53", "icrc/585#A54",
                    "icrc/585#A58", "icrc/585#A61"]
    assert [uri for uri, _ in _refs("under article 64 or 69 of the Statute",
                                    STATUTE)] == ["icrc/585#A64", "icrc/585#A69"]


def test_a_range_cites_the_articles_inside_it():
    """"the crimes embodied in articles 6 to 8 of the Statute" cites article 7
    too, and the interior was dropped -- 49 citations across the corpus. A
    descending or runaway span is a misread sentence, so it keeps its two
    ends."""
    assert [uri for uri, _ in _refs("crimes embodied in articles 6 to 8 of the "
                                    "Statute", STATUTE)] == \
        ["icrc/585#A6", "icrc/585#A7", "icrc/585#A8"]
    match = treatyref.RE_ARTICLE.search("articles 100 to 3")
    assert treatyref.cited_articles(match) == ["100", "3"]
    match = treatyref.RE_ARTICLE.search("articles 2 to 99")
    assert treatyref.cited_articles(match) == ["2", "99"]


def test_a_comma_alone_does_not_join_a_list():
    """"Article 58, 10 February 2006" is an article and a date. Reading the
    comma as a separator files the day of the month as an article, which is why
    a list is only read where it closes with "and", "or" or "to"."""
    match = treatyref.RE_ARTICLE.search("Article 58, 10 February 2006")
    assert treatyref.cited_articles(match) == ["58"]
    assert [uri for uri, _ in _refs("Article 58, 10 February 2006, of the "
                                    "Statute", STATUTE)] == ["icrc/585#A58"]
    # a four-digit year is not an article number either
    assert not treatyref.RE_ARTICLE.search("article 1949 of the Convention")


def test_an_instrument_named_without_an_article_is_referenced_whole():
    assert _refs("The Vienna Convention on the Law of Treaties reflects "
                 "customary international law.") == \
        [("untc/I-18232", "Vienna Convention on the Law of Treaties")]


def test_spans_link_each_enumerated_number_inline():
    """`spans` is the inline projection of `references`: the first number
    folds in the leading "articles", later numbers link their own token, and
    the bound instrument name links to the instrument itself."""
    text = "pursuant to articles 15, 53 and 54 of the Statute"
    got = [(text[s:e], uri.replace("https://lagen.nu/ext/", ""))
           for s, e, uri in treatyref.spans(text, extra=STATUTE)]
    assert got == [("articles 15", "icrc/585#A15"), ("53", "icrc/585#A53"),
                   ("54", "icrc/585#A54"), ("the Statute", "icrc/585")]


def test_spans_skip_a_range_interior_and_an_ambiguous_binding():
    """"articles 6 to 8" cites article 7 too, but no text carries it, so only
    the ends link inline (the aggregate keeps all three). "the Geneva
    Conventions" names four instruments at one span: linking would guess."""
    text = "articles 6 to 8 of the Rome Statute"
    uris = [uri.split("#")[-1] for _s, _e, uri in treatyref.spans(text)]
    assert uris == ["A6", "A8", "https://lagen.nu/ext/icrc/585"]
    assert [r["uri"].split("#")[-1] for r in treatyref.references(text)
            if "#" in r["uri"]] == ["A6", "A7", "A8"]
    ambiguous = "common article 3 of the Geneva Conventions"
    assert treatyref.spans(ambiguous) == []


def test_a_generic_name_needs_its_family_named_beside_it():
    """Every treaty family numbers its protocols: the corpus holds a "Second
    Additional Protocol" to the European Conventions on extradition (coe/098),
    mutual assistance (coe/182) and cybercrime (coe/224). So an ordinal name
    binds to a Geneva instrument only where the family is named within
    `CONTEXT_WINDOW` -- the phrase on coe/182's own page must stay unlinked."""
    assert _refs("the Second Additional Protocol to this Convention, "
                 "done at Strasbourg") == []
    assert _refs("grave breaches of Additional Protocol II to the Geneva "
                 "Conventions of 12 August 1949") == \
        [("icrc/365", "Geneva Conventions"), ("icrc/370", "Geneva Conventions"),
         ("icrc/375", "Geneva Conventions"), ("icrc/380", "Geneva Conventions"),
         ("icrc/475", "Additional Protocol II")]


def test_the_full_official_citation_carries_its_own_context():
    """AP II article 1 names its sibling by the full title with the short form
    parenthesised at the end -- the relating-to clause puts ~90 characters
    between "12 August 1949" and "(Protocol I)", which is why CONTEXT_WINDOW
    spans a sentence rather than a phrase."""
    text = ("Article 1 of the Protocol Additional to the Geneva Conventions "
            "of 12 August 1949, and relating to the Protection of Victims of "
            "International Armed Conflicts (Protocol I)")
    assert ("icrc/470", "Protocol I") in _refs(text)


def test_a_longer_name_wins_a_span_it_only_overlaps():
    """Two names of the same instrument may start a word apart. The Hague
    Regulations' short form does not nest inside its official title -- "Hague
    Regulations" is [4:21] and "Regulations concerning the Laws and Customs of
    War on Land" is [10:68] -- so a containment test kept both, and
    `lagrum.interleave` cannot splice two spans that share text."""
    text = ("The Hague Regulations concerning the Laws and Customs of War on "
            "Land of 18 October 1907 contain, inter alia, relevant provisions.")
    assert _refs(text) == [
        ("icrc/195", "Regulations concerning the Laws and Customs of War on Land")]
    assert treatyref.spans(text) == [(10, 68, EXT + "icrc/195")]


def test_a_short_form_loses_to_the_title_that_continues_it():
    """The same overlap across the caller's own table: "the Convention" and the
    curated "Convention against Torture" share one word. Keeping both filed a
    citation of the ECHR that the sentence never makes, and bound article 3 to
    it -- 41 hudoc judgments failed to parse on the assertion it raised."""
    extra = ((re.compile(r"\b[Tt]he Convention\b"), "coe/005"),)
    text = "refoulement provisions in Article 3 of the Convention Against Torture"
    assert _refs(text, extra) == \
        [("untc/I-24841#A3", "Convention against Torture, article 3")]
    assert treatyref.spans(text, extra) == \
        [(26, 35, EXT + "untc/I-24841#A3"), (43, 69, EXT + "untc/I-24841")]


def test_a_caller_s_short_form_may_be_a_guarded_pattern():
    """hudoc reads "the Convention" as the ECHR -- but only where no longer
    title continues it, which a plain escaped name cannot express."""
    extra = ((re.compile(r"\b[Tt]he Convention\b"
                         r"(?!\s+(?:on|for|against|of|relating|concerning|to)"
                         r"\b)"), "coe/005"),)
    assert _refs("a violation of Article 8 of the Convention", extra) == \
        [("coe/005#A8", "the Convention, article 8")]
    assert _refs("under the Convention on the Rights of the Child", extra) == \
        [("untc/I-27531", "Convention on the Rights of the Child")]
