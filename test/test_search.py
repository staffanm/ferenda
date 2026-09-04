"""The OpenSearch indexer's pure parts (ferenda/lib/search.py): artifact ->
bulk actions, the query body, and hit parsing. The cluster round-trip needs a
running OpenSearch and is exercised by the integration test at the bottom, gated
on OPENSEARCH_URL."""

import base64
import json
import os
import threading

import pytest

from ferenda.lib import catalog, search


def _build_catalog(tmp_path):
    """Two SFS artifacts where 2018:585 cites 1962:700#K3P1, so 1962:700 has a
    real inbound_count -- exercises the ranking-signal read in doc_actions."""
    art_dir = tmp_path / "artifact"
    art_dir.mkdir()
    bb = art_dir / "bb.json"
    bb.write_text(json.dumps({
        "uri": "https://lagen.nu/1962:700",
        "metadata": {"properties": {"dcterms:title": "Brottsbalk (1962:700)"}},
        "structure": [{"type": "paragraf", "id": "K3P1",
                       "text": ["Den som dödar annan döms för mord."]}]}))
    fl = art_dir / "fl.json"
    fl.write_text(json.dumps({
        "uri": "https://lagen.nu/2018:585",
        "metadata": {"properties": {"dcterms:title": "Förvaltningslag (2018:585)"}},
        "structure": [{"type": "paragraf", "id": "P1",
                       "text": ["Se ", {"uri": "https://lagen.nu/1962:700#K3P1",
                                        "text": "3 kap. 1 §"}, " brottsbalken."]}]}))
    cat = tmp_path / "catalog.sqlite"
    catalog.rebuild(cat, "sfs", [bb, fl])
    return catalog.connect(cat)


def test_doc_actions_document_and_fragment_units(tmp_path):
    con = _build_catalog(tmp_path)
    uri = "https://lagen.nu/1962:700"
    row = catalog.document(con, uri)
    row = (*row[:5], str(catalog.data_root(con) / row[5]))   # stored path is relative
    actions = list(search.doc_actions(
        row, catalog.document_inbound_count(con, uri), version="h1"))
    assert actions[0]["_source"]["version"] == "h1"     # carried for the diff
    assert actions[0]["_source"]["year"] == "1962"      # shared search facet

    doc, frag = actions[0], actions[1]
    # the whole-document unit owns full text for exact cursor paging; fragment
    # units duplicate bounded sections for the second pinpoint lookup
    assert doc["_id"] == "https://lagen.nu/1962:700"
    assert doc["_source"]["is_doc"] is True
    assert doc["_source"]["doc_uri"] == "https://lagen.nu/1962:700"
    assert doc["_source"]["uri"] == "https://lagen.nu/1962:700"
    assert doc["_source"]["title"] == "Brottsbalk (1962:700)"
    assert doc["_source"]["identifier"] == "SFS 1962:700"
    # no shortname/abbr on the artifact -> the shown heading is just the title
    assert doc["_source"]["display"] == "Brottsbalk (1962:700)"
    assert doc["_source"]["text"] == "Den som dödar annan döms för mord."
    assert doc["_source"]["inbound_count"] == 1          # 2018:585 cites K3P1
    assert "_routing" not in doc and "relation" not in doc["_source"]

    # the fragment unit: standalone, owns the body text; document identity is
    # carried as display-only (non-searchable) doc_title / doc_label
    assert frag["_id"] == "https://lagen.nu/1962:700#K3P1"
    assert frag["_source"]["is_doc"] is False
    assert frag["_source"]["doc_uri"] == "https://lagen.nu/1962:700"
    assert frag["_source"]["uri"] == "https://lagen.nu/1962:700#K3P1"
    assert frag["_source"]["pinpoint"] == "K3P1"
    assert frag["_source"]["text"] == "Den som dödar annan döms för mord."
    assert frag["_source"]["doc_title"] == "Brottsbalk (1962:700)"
    assert frag["_source"]["doc_label"] == "SFS 1962:700"
    assert frag["_source"]["doc_display"] == "Brottsbalk (1962:700)"
    assert frag["_source"]["inbound_count"] == 1          # denormalised for ranking
    assert "title" not in frag["_source"]                 # not searchable on a frag
    assert "_routing" not in frag
    # a paragraf prints no heading of its own -- its anchor names it (K3P1)
    assert "heading" not in frag["_source"]


def test_doc_actions_display_uses_shortname_and_abbr(tmp_path):
    # an eurlex act carrying shortname/abbr (the CRA): the hit heading is the
    # short name + acronym, while the searchable `title` stays the full official
    # title -- so the readable label costs no findability
    art_dir = tmp_path / "artifact"
    art_dir.mkdir()
    cra = art_dir / "cra.json"
    cra.write_text(json.dumps({
        "uri": "https://lagen.nu/celex/32024R2847", "celex": "32024R2847",
        "doctype": "regulation", "shortname": "Cyberresiliensförordningen",
        "abbr": "CRA",
        "title": "Europaparlamentets och rådets förordning (EU) 2024/2847 ... "
                 "(cyberresiliensförordningen) (Text av betydelse för EES)",
        "structure": [{"type": "article", "id": "1", "text": ["Syfte och mål."]}]}))
    cat = tmp_path / "catalog.sqlite"
    catalog.rebuild(cat, "eurlex", [cra])
    con = catalog.connect(cat)
    uri = "https://lagen.nu/celex/32024R2847"
    row = catalog.document(con, uri)
    row = (*row[:5], str(catalog.data_root(con) / row[5]))   # stored path is relative
    doc, frag = list(search.doc_actions(row, 0))
    assert doc["_source"]["display"] == "Cyberresiliensförordningen (CRA)"
    assert doc["_source"]["title"].startswith("Europaparlamentets")   # full, searchable
    assert doc["_source"]["identifier"] == "32024R2847"               # CELEX, the sub
    assert frag["_source"]["doc_display"] == "Cyberresiliensförordningen (CRA)"


def test_doc_actions_omits_the_identifier_when_the_label_is_prose():
    """A lagrådsremiss has no official number, so `lib/regeringen.py:lr_identity`
    files it under its own heading and its label IS its title (2,764 of 2,796
    catalogued lagrådsremisser). `identifier^16` then boosts ordinary prose
    sixteenfold: the live top hit for "olaga hot mot journalist" drew 43 of its
    116 points from `identifier:mot` alone. Such a document indexes no identifier
    -- its title still carries every word."""
    title = "Skärpt syn på brott mot journalister och vissa andra samhällsnyttiga funktioner"
    [lr] = list(search.doc_actions(
        ("https://lagen.nu/lr/2023/skarpt-syn-pa-brott-mot-journalister",
         "forarbete", "lr", title, title, ""), 0, version="v"))
    assert "identifier" not in lr["_source"]
    assert lr["_source"]["title"] == title        # still findable by every word
    assert lr["_source"]["label"] == title        # the display identity is kept
    # a concept page and a kommentar page are labelled the same way -- a name, or
    # the bare word "Kommentar", which x16 would push over every real match for it
    for row in (("https://lagen.nu/begrepp/Klander_av_stämmobeslut", "begrepp",
                 "begrepp", "Klander av stämmobeslut", "Klander av stämmobeslut"),
                ("https://lagen.nu/kommentar/2001:527", "kommentar", "kommentar",
                 "Kommentar", "Kommentar")):
        [unit] = list(search.doc_actions((*row, ""), 0, version="v"))
        assert "identifier" not in unit["_source"]

    # equal label and title is NOT enough to drop it: a court decision's citation
    # IS its title (all 23,733 catalogued dv decisions), and that citation is
    # exactly what the x16 boost is for. A number is what tells the two apart.
    for label in ("Prop. 2022/23:106", "NJA 2005 s. 417", "31958R0001(01)"):
        [unit] = list(search.doc_actions(
            ("https://lagen.nu/x", "dv", "case", label, label, ""), 0, version="v"))
        assert unit["_source"]["identifier"] == label
    # ... and a label that differs from the title is always indexed
    [prop] = list(search.doc_actions(
        ("https://lagen.nu/prop/2022/23:106", "forarbete", "prop",
         "Prop. 2022/23:106", title, ""), 0, version="v"))
    assert prop["_source"]["identifier"] == "Prop. 2022/23:106"
    # emitted units changed without any artifact changing, so the version prefix
    # has to move for the incremental indexer to refresh them
    assert search.INDEX_FORMAT == "7"
    assert search._index_version("h1") == "7:h1"


def test_doc_actions_no_fragments_carries_full_text(tmp_path):
    # a flat artifact (no id-bearing nodes) -> the single document unit holds the
    # whole body text, since there is no fragment to own it
    art = tmp_path / "flat.json"
    art.write_text(json.dumps({
        "uri": "https://lagen.nu/dom/x", "metadata": {"properties": {}},
        "body": [{"type": "stycke", "text": ["Domskälen anför följande."]}]}))
    [unit] = list(search.doc_actions(
        ("https://lagen.nu/dom/x", "dv", "case", "X", "X", str(art)), 0))
    assert unit["_source"]["is_doc"] is True
    assert unit["_source"]["text"] == "Domskälen anför följande."


def test_doc_actions_alternate_citation_is_searchable(tmp_path):
    # a published alternate citation with no body span (a JO decision's
    # ämbetsberättelse) rides the whole-doc unit's text, so querying
    # "JO 1990/91 s. 70" finds the decision
    art = tmp_path / "jo.json"
    art.write_text(json.dumps({
        "uri": "https://lagen.nu/avg/jo/1672-1987",
        "metadata": {"officialReport": "JO 1990/91 s. 70"},
        "structure": [{"type": "stycke", "text": ["Beslutets text."]}]}))
    [unit] = list(search.doc_actions(
        ("https://lagen.nu/avg/jo/1672-1987", "avg", "jo",
         "JO dnr 1672-1987", "Förföljande med polisfordon", str(art)), 0))
    assert unit["_source"]["text"] == "JO 1990/91 s. 70\nBeslutets text."


def test_doc_actions_indexes_case_numbers_as_a_second_identity(tmp_path):
    # a commentary cites the decision by date and case number ("HD:s dom
    # 2009-11-03 T 3-08"), never by the referat number the corpus files it under
    # -- so the case numbers are indexed as an identity of their own. Several per
    # referat: NJA 1992 s. 740 collects T 369-91 and T 224-91, printed inside it
    # as I and II.
    art = tmp_path / "nja.json"
    art.write_text(json.dumps({
        "uri": "https://lagen.nu/dom/nja/1992s740",
        "malnummer": ["T 369-91", "T 224-91"],
        "structure": [{"type": "paragraf", "id": "P1",
                       "text": ["Skadestånd för sveda och värk."]}]}))
    doc, frag = list(search.doc_actions(
        ("https://lagen.nu/dom/nja/1992s740", "dv", "case",
         "NJA 1992 s. 740", "NJA 1992 s. 740", str(art)), 0))
    assert doc["_source"]["malnummer"] == ["T 369-91", "T 224-91"]
    # whole-document unit only: a case number names the decision, not a paragraph
    assert "malnummer" not in frag["_source"]

    # a source without the key contributes no field at all (strict mapping is
    # fine with an absent field, and a null would match a null query)
    plain = tmp_path / "sfs.json"
    plain.write_text(json.dumps({"uri": "https://lagen.nu/1962:700",
                                 "structure": [{"type": "stycke",
                                                "text": ["Lagtext."]}]}))
    [law] = list(search.doc_actions(
        ("https://lagen.nu/1962:700", "sfs", "law", "SFS 1962:700",
         "Brottsbalk (1962:700)", str(plain)), 0))
    assert "malnummer" not in law["_source"]


def test_doc_actions_skips_empty_artifact(tmp_path):
    # a row whose artifact file is empty yields nothing
    empty = tmp_path / "empty.json"
    empty.write_bytes(b"")
    assert list(search.doc_actions(
        ("u", "sfs", "law", "L", "L", str(empty)), 0)) == []


def test_doc_actions_pathless_stub_indexes_identity_only():
    # a synthesized stub (begrepp concept, no artifact on disk -> empty path)
    # must not read a file; it indexes one whole-doc unit carrying its name
    [unit] = list(search.doc_actions(
        ("https://lagen.nu/begrepp/Uppsat", "begrepp", "begrepp",
         "Uppsåt", "Uppsåt", ""), 3, version="v"))
    assert unit["_id"] == "https://lagen.nu/begrepp/Uppsat"
    assert unit["_source"]["is_doc"] is True
    assert unit["_source"]["title"] == "Uppsåt"
    assert unit["_source"]["version"] == "v"
    assert "text" not in unit["_source"]            # no body, no fragments


def test_doc_actions_carries_repeal_date_when_present():
    # the repeal date rides onto every unit so the query can time-filter it (S6/S7)
    [unit] = list(search.doc_actions(
        ("https://lagen.nu/1960:1", "sfs", "law", "1960:1", "Gammal lag", ""),
        0, version="v", expired="2020-01-01"))
    assert unit["_source"]["expired"] == "2020-01-01"
    # no repeal date -> no field (so the range filter never matches it)
    [live] = list(search.doc_actions(
        ("https://lagen.nu/2020:1", "sfs", "law", "2020:1", "Ny lag", ""),
        0, version="v"))
    assert "expired" not in live["_source"]


def test_query_excludes_in_force_repeals_but_keeps_future_ones():
    # a repeal already in force is filtered out; a future/absent repeal is kept
    # (S6/S7) -- evaluated against `now` at query time, not baked in
    body = search.query_body("mord")
    must_not = body["query"]["function_score"]["query"]["bool"]["must_not"]
    assert {"range": {"expired": {"lte": "now/d"}}} in must_not


def test_query_body_pages_exact_document_units_and_ranks_by_inbound():
    body = search.query_body("mord", source="sfs", year="1962",
                             limit=5, offset=10)
    assert body["from"] == 10 and body["size"] == 5
    assert "collapse" not in body                         # one unit per document
    assert body["track_total_hits"] is True                # exact result count
    assert body["sort"] == [{"_score": "desc"}, {"doc_uri": "asc"}]
    fs = body["query"]["function_score"]
    assert fs["functions"][0]["field_value_factor"]["field"] == "inbound_count"
    # the acts tier (sfs/foreskrift/EU acts) gets a flat score bonus (S3)
    assert fs["functions"][1]["weight"] == search.ACT_TIER_BOOST
    assert {"term": {"source": "sfs"}} in \
        fs["functions"][1]["filter"]["bool"]["should"]
    assert fs["boost_mode"] == "sum" and fs["score_mode"] == "sum"
    # filtering happens in post_filter only (facet counts stay unnarrowed)
    assert {"term": {"is_doc": True}} in fs["query"]["bool"]["filter"]
    assert {"term": {"source": "sfs"}} in body["post_filter"]["bool"]["filter"]
    assert {"term": {"year": "1962"}} in body["post_filter"]["bool"]["filter"]
    # A facet omits its own selected value, but retains the other filters.
    source_filters = body["aggs"]["source"]["filter"]["bool"]["filter"]
    assert {"term": {"source": "sfs"}} not in source_filters
    assert {"term": {"year": "1962"}} in source_filters
    # Exact-token and automatic-prefix branches search all standalone units.
    queries = fs["query"]["bool"]["must"]["bool"]["should"]
    assert queries[0]["simple_query_string"]["query"] == "mord"
    assert queries[1]["simple_query_string"]["query"] == "mord*"


def test_query_body_no_filters_when_unscoped():
    body = search.query_body("mord")
    assert body["query"]["function_score"]["query"]["bool"]["filter"] \
        == [{"term": {"is_doc": True}}]
    assert body["post_filter"]["bool"]["filter"] == []


def test_cursor_roundtrip_and_search_after_query():
    cursor = search.encode_cursor([7.5, "https://lagen.nu/1962:700"], 20)
    sort, seen, by = search.decode_cursor(cursor)
    assert sort == [7.5, "https://lagen.nu/1962:700"] and seen == 20
    assert by == "relevance"                 # the default order, stamped in
    body = search.query_body("mord", search_after=sort)
    assert body["search_after"] == sort and "from" not in body
    with pytest.raises(ValueError, match="invalid search cursor"):
        search.decode_cursor("not-json")


def test_fragment_query_is_bounded_to_page_documents():
    body = search.fragment_query_body("mord", ["u1", "u2"])
    assert body["size"] == 2
    assert {"term": {"is_doc": False}} in body["query"]["bool"]["filter"]
    assert {"terms": {"doc_uri": ["u1", "u2"]}} in body["query"]["bool"]["filter"]
    # one group per document, and up to PASSAGES_PER_HIT passages inside it --
    # a single passage per document reads as *the* place the query matched
    collapse = body["collapse"]
    assert collapse["field"] == "doc_uri"
    assert collapse["inner_hits"]["size"] == search.PASSAGE_CANDIDATES
    assert collapse["inner_hits"]["highlight"] == search.HIGHLIGHT
    # every field parse_fragment reads has to come back on the inner hit: a
    # fragment's `_source` is its whole text, so the query asks for these three
    # by name -- and leaving `heading` out silently unnamed every förarbete
    # passage (the label fell back to None with no error anywhere)
    assert collapse["inner_hits"]["_source"] == ["uri", "pinpoint", "heading"]


def test_prefix_query_handles_incomplete_legal_compounds_and_syntax():
    assert search.prefix_query("avtalsl") == "avtalsl*"
    assert search.prefix_query("(36 §) upphovsr") == "36* upphovsr*"


def test_prefix_query_leaves_a_quoted_phrase_exact():
    # quotes ask for exactly this string. Expanding their words said the
    # opposite: '"T 3-08"' became 't* 3* 08*', which under AND matched 43,648
    # documents and buried the one case filed under that number.
    assert search.prefix_query('"T 3-08"') == '"T 3-08"'
    # words outside the quotes are still completed
    assert search.prefix_query('"T 3-08" hovr') == '"T 3-08" hovr*'
    assert search.prefix_query('avtalsl "god sed" (36 §)') \
        == 'avtalsl* "god sed" 36*'
    # an unbalanced quote has no phrase to keep -- every word is prefixed, as before
    assert search.prefix_query('"T 3-08') == "T* 3* 08*"
    # both text branches carry the phrase, so nothing re-expands it
    should = search._text_query('"T 3-08"')["bool"]["should"]
    assert [c["simple_query_string"]["query"] for c in should
            if "simple_query_string" in c] == ['"T 3-08"', '"T 3-08"']


def test_case_number_query_is_a_phrase_over_what_the_query_names():
    # the case number is matched whole. Per-term it would be ordinary numbers:
    # 373 of 2,109 sampled case numbers hold a year-like token, so "brott 2009"
    # would promote every decision whose case number contains 2009.
    clause = {"match_phrase": {"malnummer": {
        "query": "T 3-08", "boost": search.CASE_NUMBER_BOOST}}}
    assert search.case_number_queries("T 3-08") == [clause]
    # both spellings are the same number, and the citation it was lifted from
    # carries the decision date in front of it
    assert search.case_number_queries("T3-08") == [clause]
    assert search.case_number_queries(
        "Högsta domstolens dom 2009-11-03 T 3-08") == [clause]
    # what is not a case number takes no clause: a bare year, prose, and the
    # date on its own (whose "2009-11" is not a case number either)
    for q in ("2009", "uppsägningstid", "brott 2009", "2009-11-03"):
        assert search.case_number_queries(q) == [], q
    # it rides beside the text branches, never instead of them: a document whose
    # case number matches answers even when its body does not carry the words
    should = search._text_query("T 3-08")["bool"]["should"]
    assert should[-1] == clause and len(should) == 3


def test_eu_case_number_query_is_a_phrase_on_the_title():
    # an eurlex judgment is titled by its case number, and the analyzer splits
    # "C-199/24" into "c", "199", "24" -- ordinary tokens that ranked every
    # document holding those two numbers over the judgment itself; the phrase
    # over the title is the same answer the målnummer field gives a Swedish
    # case number
    clause = {"match_phrase": {"title": {
        "query": "C-199/24", "boost": search.CASE_NUMBER_BOOST}}}
    assert search.case_number_queries("C-199/24") == [clause]
    # the marker word, a non-breaking hyphen, a lower-case letter: one number
    for q in ("mål C-199/24", "Case C‑199/24", "c-199/24", "dom i mål C-199/24"):
        assert search.case_number_queries(q) == [clause], q
    # joined cases: one clause each
    assert [c["match_phrase"]["title"]["query"] for c in
            search.case_number_queries("C-199/24 och C-200/24")] \
        == ["C-199/24", "C-200/24"]
    # pre-1989 numbering counts only behind the marker word -- a bare "31/87"
    # is as often a riksmöte or a page reference
    assert search.case_number_queries("mål 31/87") == [{"match_phrase": {
        "title": {"query": "31/87", "boost": search.CASE_NUMBER_BOOST}}}]
    for q in ("31/87", "prop. 2001/02:5", "artikel 24", "C-199"):
        assert search.case_number_queries(q) == [], q
    # a query that cannot be a case number keeps the two text branches alone
    assert len(search._text_query("mord")["bool"]["should"]) == 2


def test_highlight_cap_stays_under_index_limit():
    # A whole-document unit's `text` runs past 1M chars for large statutes; without
    # a query-level cap OpenSearch 400s the whole search on such a hit. The cap must
    # be present in the highlight body AND stay <= the index default max_analyzed_offset
    # (1_000_000), or the 400 returns. Verified live against OpenSearch 2.9.0 (see the
    # HIGHLIGHT comment in search.py); this guards the invariant the mocked client can't.
    offset = search.HIGHLIGHT["max_analyzer_offset"]
    assert 0 < offset <= 1_000_000
    body = search.query_body("mord")
    assert body["highlight"]["max_analyzer_offset"] == offset
    # the cap has to ride every body that highlights, and the ranking query no
    # longer does: `search` asks for the page's snippets separately
    assert "highlight" not in search.query_body("mord", highlight=False)
    assert (search.document_highlight_body("mord", ["u1"])
            ["highlight"]["max_analyzer_offset"] == offset)
    # the fragment query highlights inside the collapse, not on the outer hit
    assert (search.fragment_query_body("mord", ["u1"])["collapse"]["inner_hits"]
            ["highlight"]["max_analyzer_offset"] == offset)


# the four steps of one real beredningskedja, as the live index holds their titles
CHAIN = "Skärpt syn på brott mot journalister och vissa andra samhällsnyttiga funktioner"
CHAIN_SOU = ("En skärpt syn på brott mot journalister och utövare av vissa "
             "samhällsnyttiga funktioner")


def test_same_project_groups_a_beredningskedja_but_not_its_neighbours():
    """The lagrådsremiss, Bet. 2022/23:JuU27 and Prop. 2022/23:106 print the same
    title; SOU 2022:2 rewords it (token overlap 0.71). Everything else in the live
    top-50 for "olaga hot mot journalist" is a different project and must stay
    separate -- including two whose titles share the word "mot"."""
    def same(a, b):
        return search.same_project(search.title_tokens(a), search.title_tokens(b))

    assert same(CHAIN, CHAIN)                      # lr / bet / prop, verbatim
    assert same(CHAIN, CHAIN_SOU)                  # SOU 2022:2, reworded
    # word order and punctuation do not separate a project from itself
    assert same("Vårdnad om barn m.m.", "om vårdnad om barn m.m.")

    for other in ("Kraftsamling mot antiziganism",              # SOU 2016:44
                  "Hemliga tvångsmedel mot allvarliga brott",   # SOU 2012:44
                  "Ett starkare skydd för offentliganställda mot våld, hot "
                  "och trakasserier m.m."):                     # a second lr
        assert not same(CHAIN, other)
        assert not same(CHAIN_SOU, other)
    assert not same("Kraftsamling mot antiziganism",
                    "Hemliga tvångsmedel mot allvarliga brott")
    # an act must not merge with the props that amend it: 0.69 overlap, just under
    # the threshold, measured live for the query "upphovsrätt"
    assert not same(
        "Lag (1960:729) om upphovsrätt till litterära och konstnärliga verk",
        "om ändring i lagen (1960:729) om upphovsrätt till litterära och "
        "konstnärliga verk")
    # an untitled document is its own cluster, never one big cluster of them all
    assert not search.same_project(search.title_tokens(""), search.title_tokens(""))


def test_cap_title_clusters_keeps_the_two_best_and_backfills():
    # the live candidate order for "olaga hot mot journalist": the chain takes
    # ranks 1, 2, 4 and 5, with three unrelated documents between them
    titles = [CHAIN,                                    # 0 lr
              CHAIN,                                    # 1 bet
              "Ett starkare skydd för offentliganställda mot våld, hot m.m.",
              CHAIN_SOU,                                # 3 SOU 2022:2 -- capped
              CHAIN,                                    # 4 prop -- capped
              "Skydd mot avlyssning",
              "Skadeståndsanspråk mot staten",
              "Kraftsamling mot antiziganism"]
    keep, used = search.cap_title_clusters(titles, limit=5)
    assert keep == [0, 1, 2, 5, 6]        # two of the chain, then the next distinct
    assert used == 7                      # ... and the page read that far to fill
    # every candidate the page skipped is behind the cursor, so page 2 resumes
    # after index 6 and never re-shows the two capped hits
    assert [titles[i] for i in keep].count(CHAIN) == 2

    # nothing to declutter -> the raw order, untouched
    assert search.cap_title_clusters(titles[5:], limit=3) == ([0, 1, 2], 3)


def test_cap_title_clusters_never_shortens_a_page():
    # decluttering must not cost a reader a result: where the candidates run out
    # before the page is full, the capped-out hits come back to fill it
    keep, used = search.cap_title_clusters([CHAIN] * 4, limit=10)
    assert keep == [0, 1, 2, 3] and used == 4
    # ... and a full window still yields exactly the page asked for
    keep, _ = search.cap_title_clusters([CHAIN] * 3 + ["Skydd mot avlyssning"] * 9,
                                        limit=10)
    assert len(keep) == 10


def test_strip_stopword_highlights_keeps_content_words():
    # the index has no Swedish stopword filter, so "mot" is matched and marked
    # like any term: SOU 2016:44 came back as "Kraftsamling <em>mot</em>
    # antiziganism", a snippet whose only mark is a function word
    assert search.strip_stopword_highlights(
        ["Kraftsamling <em>mot</em> antiziganism",
         "dömdes för <em>olaga</em> <em>hot</em>"]) \
        == ["dömdes för <em>olaga</em> <em>hot</em>"]
    # the marks around content words stay, and no text is lost with the wrapper
    assert search.strip_stopword_highlights(
        ["olaga <em>hot</em> mot <em>journalist</em>"]) \
        == ["olaga <em>hot</em> mot <em>journalist</em>"]
    assert search.strip_stopword_highlights(
        ["<em>hot</em> <em>mot</em> en <em>journalist</em> via Twitter"]) \
        == ["<em>hot</em> mot en <em>journalist</em> via Twitter"]
    # a word that merely starts with a stopword is a content word (the prefix
    # branch marks "motiverade" for the query "mot")
    assert search.strip_stopword_highlights(["som <em>motiverade</em> insatsen"]) \
        == ["som <em>motiverade</em> insatsen"]
    # an empty snippet reads as "no match in the body", so the last fragment
    # survives even when its only mark is a function word
    assert search.strip_stopword_highlights(["Kraftsamling <em>mot</em> antiziganism"]) \
        == ["Kraftsamling mot antiziganism"]
    assert search.strip_stopword_highlights([]) == []


def test_parse_hit_strips_the_function_word_marks_it_returns():
    hit = search.parse_hit({
        "_source": {"doc_uri": "https://lagen.nu/sou/2016:44",
                    "uri": "https://lagen.nu/sou/2016:44", "is_doc": True,
                    "title": "Kraftsamling mot antiziganism", "source": "forarbete"},
        "_score": 32.75,
        "highlight": {"text": ["ska <em>motverka</em> hatbrott",
                               "det ”<em>hot</em>” som <em>motiverade</em> styrkan",
                               "brotten <em>mot</em> någons frid"]},
    })
    assert hit["highlight"] == ["ska <em>motverka</em> hatbrott",
                                "det ”<em>hot</em>” som <em>motiverade</em> styrkan"]


def test_distinct_passages_drops_what_a_passage_above_already_marked():
    # a fragment's text includes its descendants', so 14 § and 14 § 1 st mark the
    # same words -- one line for the two of them
    def passage(pinpoint, *marked):
        return {"uri": "u1#" + pinpoint, "pinpoint": pinpoint, "label": None,
                "highlight": list(marked)}
    kept = search.distinct_passages(
        [passage("P14", "om <em>rekonstruktion</em>"),
         passage("P14S1", "om <em>rekonstruktion</em>"),
         passage("P8", "beslutet om <em>rekonstruktion</em>"),
         passage("P3"),                                 # matched, nothing marked
         passage("P2", "en <em>rekonstruktion</em> inleds")], 3)
    assert [p["pinpoint"] for p in kept] == ["P14", "P8", "P2"]


def test_doc_actions_names_a_section_fragment_by_its_heading(tmp_path):
    # a förarbete section's anchor ("sec2") is no citation, so the fragment
    # carries the heading the document prints over it -- trimmed, since a
    # runaway parse would otherwise push the marked text off the result line
    art = tmp_path / "sou.json"
    art.write_text(json.dumps({
        "uri": "https://lagen.nu/utr/sou/2025:1",
        "body": [{"type": "avsnitt", "id": "sec1", "text": ["Sammanfattning"],
                  "children": [{"text": ["Utredningen föreslår."]}]},
                 {"type": "avsnitt", "id": "sec2",
                  "text": ["8.5.1 " + "Samspelet mellan bestämmelserna " * 4],
                  "children": [{"text": ["Av artikel 1.5 följer."]}]},
                 {"type": "stycke", "id": "S1", "text": ["Ett vanligt stycke."]}]}))
    units = {u["_id"].rsplit("#", 1)[-1]: u["_source"] for u in search.doc_actions(
        ("https://lagen.nu/utr/sou/2025:1", "forarbete", "sou", "SOU 2025:1",
         "En utredning", str(art)), 0, version="h1")}
    assert units["sec1"]["heading"] == "Sammanfattning"
    assert len(units["sec2"]["heading"]) <= search.HEADING_CHARS + 1
    assert units["sec2"]["heading"].startswith("8.5.1 Samspelet")
    assert units["sec2"]["heading"].endswith("…")
    assert "heading" not in units["S1"]          # a stycke prints body, not a heading


def test_parse_fragment_names_the_pinpoint_it_can_name():
    # a passage says where in the document the words stand. `label` is the
    # pinpoint as a reader cites it, and None where the anchor has no citation
    # grammar (a förarbete section id) -- the passage then shows its text alone
    passage = search.parse_fragment({
        "_source": {"doc_uri": "https://lagen.nu/1962:700",
                    "uri": "https://lagen.nu/1962:700#K3P1", "pinpoint": "K3P1"},
        "highlight": {"text": ["döms för <em>mord</em>"]},
    })
    assert passage == {"uri": "https://lagen.nu/1962:700#K3P1", "pinpoint": "K3P1",
                       "label": "3 kap. 1 §",
                       "highlight": ["döms för <em>mord</em>"]}
    # no citation grammar -> the heading the document prints over the section
    assert search.parse_fragment(
        {"_source": {"uri": "u1#sec745", "pinpoint": "sec745",
                     "heading": "8.5.1 Samspelet mellan bestämmelserna"},
         "highlight": {"text": ["<em>mord</em>"]}})["label"] == \
        "8.5.1 Samspelet mellan bestämmelserna"
    # neither: an EDPB point, whose passage shows its text alone
    assert search.parse_fragment(
        {"_source": {"uri": "u1#punkt5", "pinpoint": "punkt5"},
         "highlight": {"text": ["<em>mord</em>"]}})["label"] is None


def test_parse_hit_is_a_document_hit_with_no_pin():
    # full text finds documents: the hit has no pin, so nothing moves its link
    # off the document, and its passages are merged in by `search`
    hit = search.parse_hit({
        "_source": {"doc_uri": "https://lagen.nu/1962:700",
                    "uri": "https://lagen.nu/1962:700", "is_doc": True,
                    "title": "Brottsbalk", "source": "sfs", "inbound_count": 42},
        "_score": 3.0,
        "highlight": {"title": ["<em>Brottsbalk</em>"]},
    })
    assert hit["uri"] == "https://lagen.nu/1962:700"
    assert hit["pin"] is None and hit["fragments"] == []
    assert hit["highlight"] == ["<em>Brottsbalk</em>"]    # falls back to title


def test_search_parses_filtered_total_and_facet_buckets():
    class Client:
        def search(self, index, body):
            assert index == "test"
            assert {"term": {"year": "1962"}} in body["post_filter"]["bool"]["filter"]
            return {
                "hits": {"total": {"value": 12, "relation": "eq"}, "hits": []},
                "aggregations": {
                    "source": {"values": {"buckets": [
                        {"key": "sfs", "doc_count": 9}]}},
                    "kind": {"values": {"buckets": [
                        {"key": "law", "doc_count": 9}]}},
                    "year": {"values": {"buckets": [
                        {"key": "1962", "doc_count": 12}]}},
                },
            }

    index = object.__new__(search.SearchIndex)
    index.index = "test"
    index.client = Client()
    result = index.search("mord", year="1962")
    assert result["total"] == 12
    assert result["facets"]["source"] == [{"value": "sfs", "count": 9}]
    assert result["facets"]["year"] == [{"value": "1962", "count": 12}]
    assert result["next_cursor"] is None


def test_search_adds_passages_without_moving_the_hit():
    """The passage query ADDS to a hit. The document keeps its own snippet and
    stays what the hit points at -- the reader who searched "dataförordningen"
    wants the act, not article 47, which is where the act's name stands because
    that article amends another regulation by quoting the title."""
    class Client:
        def __init__(self):
            self.calls = 0

        def search(self, index, body):
            self.calls += 1
            if self.calls == 1:
                # the ranking query carries no highlight -- the snippets come from
                # the two bounded follow-ups, for this page's documents only
                assert "highlight" not in body
                return {
                    "hits": {"total": {"value": 2, "relation": "eq"}, "hits": [{
                        "_source": {"doc_uri": "u1", "uri": "u1", "is_doc": True,
                                    "title": "One", "source": "sfs"},
                        "_score": 5.0, "sort": [5.0, "u1"],
                    }]},
                    "aggregations": {field: {"values": {"buckets": []}}
                                     for field in ("source", "kind", "year")},
                }
            if self.calls == 2:
                assert {"terms": {"uri": ["u1"]}} in body["query"]["bool"]["filter"]
                return {"hits": {"hits": [{
                    "_source": {"uri": "u1"},
                    "highlight": {"text": ["the <em>document</em> itself"]},
                }]}}
            assert body["collapse"]["field"] == "doc_uri"
            return {"hits": {"hits": [{
                "_source": {"doc_uri": "u1"},
                "inner_hits": {"passages": {"hits": {"hits": [
                    {"_source": {"uri": "u1#K3P1", "pinpoint": "K3P1"},
                     "highlight": {"text": ["<em>mord</em>"]}},
                    {"_source": {"uri": "u1#K3P2", "pinpoint": "K3P2"},
                     "highlight": {"text": ["dråp och <em>mord</em>"]}},
                ]}}},
            }]}}

    index = object.__new__(search.SearchIndex)
    index.index = "test"
    index.client = Client()
    result = index.search("mord", limit=1)
    top = result["results"][0]
    assert top["pin"] is None                       # nothing moves the link
    assert top["highlight"] == ["the <em>document</em> itself"]   # the doc's own
    assert [(f["pinpoint"], f["label"]) for f in top["fragments"]] == [
        ("K3P1", "3 kap. 1 §"), ("K3P2", "3 kap. 2 §")]
    sort, seen, _by = search.decode_cursor(result["next_cursor"])
    assert sort == [5.0, "u1"] and seen == 1


def _clustered_client(sizes, froms=None):
    """A client whose ranking query answers with one whole-document hit per title
    of the audit's live candidate order, recording each requested `size` (and,
    when `froms` is given, each request's `from` -- which must always be an int:
    the offset-mode sentinel None reaching the body is an OpenSearch 400)."""
    titles = [CHAIN, CHAIN, "Ett starkare skydd för offentliganställda",
              CHAIN_SOU, CHAIN, "Skydd mot avlyssning", "Skadeståndsanspråk mot staten",
              "Kraftsamling mot antiziganism", "Nya nätbrott", "Hemliga tvångsmedel"]

    class Client:
        def search(self, index, body):
            if "aggs" in body:
                sizes.append(body["size"])
                if froms is not None:
                    froms.append(body.get("from"))
                return {"hits": {
                    "total": {"value": 747, "relation": "eq"},
                    "hits": [{"_source": {"doc_uri": "u%d" % i, "uri": "u%d" % i,
                                          "is_doc": True, "title": title,
                                          "source": "forarbete"},
                              "_score": 100.0 - i, "sort": [100.0 - i, "u%d" % i]}
                             for i, title in enumerate(titles[:body["size"]])]},
                    "aggregations": {field: {"values": {"buckets": []}}
                                     for field in ("source", "kind", "year")}}
            return {"hits": {"hits": []}}          # no snippets, no fragments

    index = object.__new__(search.SearchIndex)
    index.index = "test"
    index.client = Client()
    return index


def test_search_caps_one_project_per_page_and_keeps_the_cursor_coherent():
    """Page 1 of "olaga hot mot journalist" was four steps of one legislative
    project. At most CLUSTER_CAP of them show, the freed slots go to the next
    distinct projects, and the cursor resumes after the last candidate the page
    read -- so page 2 neither repeats those hits nor brings the capped ones back."""
    sizes, froms = [], []
    result = _clustered_client(sizes, froms).search("olaga hot mot journalist",
                                                    limit=5)
    assert sizes == [15]                     # 3x the page, ranked but not highlighted
    assert froms == [0]                      # the None mode sentinel never reaches `from`
    assert [r["title"] for r in result["results"]] == [
        CHAIN, CHAIN, "Ett starkare skydd för offentliganställda",
        "Skydd mot avlyssning", "Skadeståndsanspråk mot staten"]
    assert len(result["results"]) == 5       # the cap costs the page nothing
    assert result["total"] == 747            # the raw query's count, not the page's
    sort, seen, _by = search.decode_cursor(result["next_cursor"])
    assert sort == [94.0, "u6"] and seen == 7    # the 7th candidate, not the 5th hit


def test_search_leaves_offset_paging_uncapped():
    # `offset` is bounded random access: page N must line up with `from`, so a cap
    # there would re-show on page 2 the candidates page 1 consumed past its limit
    sizes = []
    result = _clustered_client(sizes).search("olaga hot mot journalist",
                                            limit=5, offset=5)
    assert sizes == [5]                                  # no candidate window
    assert [r["title"] for r in result["results"]][:2] == [CHAIN, CHAIN]
    assert len(result["results"]) == 5


def test_search_explicit_offset_zero_is_the_raw_first_page():
    """The mode signal is the offset's presence, not its value: a client walking
    by offset starts at 0, and its first page must line up with its later raw
    pages -- capped there, hits 3-4 would never show on any page and the
    backfilled ones would repeat. Only the cursorless, offsetless first page
    (the web UI's) is capped."""
    sizes, froms = [], []
    result = _clustered_client(sizes, froms).search("olaga hot mot journalist",
                                                    limit=5, offset=0)
    assert sizes == [5]                                  # no candidate window
    assert froms == [0]
    # raw candidate order: the four chain steps stay where they ranked
    assert [r["title"] for r in result["results"]] == [
        CHAIN, CHAIN, "Ett starkare skydd för offentliganställda",
        CHAIN_SOU, CHAIN]


def test_threaded_bulk_passes_the_retry_arguments_to_streaming_bulk(monkeypatch):
    """A chunk the cluster rejects under load must be retried, not written off.

    `helpers.parallel_bulk` -- which this path used to call -- hands each chunk
    straight to the bulk endpoint and takes no `max_retries` at all, so one 429
    failed every item in it permanently and the units went silently missing from
    the index (a rebuild lost 1,497 eurlex and 241 förarbete documents that way).
    Each worker runs `streaming_bulk` instead, which owns the retry loop. The
    retry itself is that helper's, so what is checked here is that the arguments
    turning it on actually reach the call -- and that every action reaches a
    worker."""
    seen = {}

    def fake_streaming_bulk(client, actions, **kw):
        seen.update(kw)
        for _ in actions:                 # drain, so the feeder is not blocked
            yield True, {}

    monkeypatch.setattr(search.helpers, "streaming_bulk", fake_streaming_bulk)
    index = search.SearchIndex.__new__(search.SearchIndex)
    index.client = object()
    indexed, errors = index._threaded_bulk(
        iter([{"_id": str(i)} for i in range(20)]), 3, {"index": "lagen-test"})
    assert (indexed, errors) == (20, [])          # every action reached a worker
    assert seen["max_retries"] == search.RETRIES  # ... with the backoff attached
    assert seen["max_backoff"] == search.BACKOFF_CAP
    assert seen["raise_on_error"] is False        # errors are collected, not raised


def test_threaded_bulk_collects_the_items_the_cluster_rejected(monkeypatch):
    def fake_streaming_bulk(client, actions, **kw):
        for action in actions:
            ok = action["_id"] != "3"
            yield ok, {} if ok else {"index": {"_id": "3", "status": 429,
                                               "error": {"type": "circuit_breaking"}}}

    monkeypatch.setattr(search.helpers, "streaming_bulk", fake_streaming_bulk)
    index = search.SearchIndex.__new__(search.SearchIndex)
    index.client = object()
    indexed, errors = index._threaded_bulk(
        iter([{"_id": str(i)} for i in range(6)]), 2, {"index": "lagen-test"})
    assert indexed == 5
    assert [e["index"]["status"] for e in errors] == [429]


@pytest.mark.skipif(not os.environ.get("OPENSEARCH_URL"),
                    reason="needs a running OpenSearch (set OPENSEARCH_URL)")
def test_index_and_search_round_trip(tmp_path):
    """End-to-end against a live cluster: index two acts, then a free-text query
    returns one result per document (collapsed by doc_uri), represented by the
    matching paragraph."""
    con = _build_catalog(tmp_path)
    index = search.SearchIndex(index="lagen-test")
    try:
        index.index_source(con, "sfs")
        res = index.search("mord")
        assert res["total"] == 1                             # one distinct document
        top = res["results"][0]
        assert top["uri"] == "https://lagen.nu/1962:700"
        assert top["inbound_count"] == 1                     # cited by 2018:585
        assert top["fragments"][0]["pinpoint"] == "K3P1"     # the matching paragraph
        # the real analyzer + wildcard path, not merely the pure query shape
        assert index.search("mor")["total"] == 1
        # a scoped query still works
        assert index.search("brottsbalken", source="sfs")["total"] >= 1
    finally:
        if index.client.indices.exists(index="lagen-test"):
            index.client.indices.delete(index="lagen-test")


@pytest.mark.skipif(not os.environ.get("OPENSEARCH_URL"),
                    reason="needs a running OpenSearch (set OPENSEARCH_URL)")
def test_eu_case_number_finds_the_judgment(tmp_path):
    """Against a live cluster: a CJEU judgment, titled by its case number, leads
    for that number -- ahead of an act whose body carries the same two numbers
    many times over, which per-term matching ranked first (2026-09-04)."""
    art = tmp_path / "artifact"
    art.mkdir()
    judgment = art / "judgment.json"
    judgment.write_text(json.dumps({
        # a judgment's catalog title is its case citation, stamped at parse
        # as `label` (catalog_rows._eurlex_document), not its Formex title
        "uri": "https://lagen.nu/celex/62024CJ0199", "doctype": "judgment",
        "celex": "62024CJ0199", "label": "C-199/24",
        "title": "Domstolens dom (första avdelningen) av den 4 september 2026",
        "structure": [{"type": "paragraf", "id": "P1",
                       "text": ["Domstolen meddelar följande dom."]}]}))
    act = art / "act.json"
    act.write_text(json.dumps({
        "uri": "https://lagen.nu/celex/32024R0001", "doctype": "regulation",
        "celex": "32024R0001", "title": "Förordning (EU) 2024/1",
        "structure": [{"type": "paragraf", "id": "P1",
                       "text": [" ".join(["artikel 24 c punkt 199"] * 20)]}]}))
    cat = tmp_path / "catalog.sqlite"
    catalog.rebuild(cat, "eurlex", [judgment, act])
    con = catalog.connect(cat)
    index = search.SearchIndex(index="lagen-test")
    try:
        index.index_source(con, "eurlex")
        for q in ("C-199/24", "c-199/24", "mål C‑199/24"):
            res = index.search(q, limit=5)
            assert res["results"][0]["uri"] == "https://lagen.nu/celex/62024CJ0199", q
    finally:
        if index.client.indices.exists(index="lagen-test"):
            index.client.indices.delete(index="lagen-test")


@pytest.mark.skipif(not os.environ.get("OPENSEARCH_URL"),
                    reason="needs a running OpenSearch (set OPENSEARCH_URL)")
def test_case_number_finds_the_decision(tmp_path):
    """Against a live cluster: a decision is findable by the case number it was
    filed under, months before its referat number exists -- how a law review
    article cites it ("HD:s dom 2009-11-03 T 3-08"). Two numbers per referat,
    since HD collects cases decided together."""
    art = tmp_path / "artifact"
    art.mkdir()
    nja = art / "nja.json"
    nja.write_text(json.dumps({
        "uri": "https://lagen.nu/dom/nja/1992s740",
        "malnummer": ["T 369-91", "T 224-91"],
        "metadata": {"properties": {"dcterms:title": "NJA 1992 s. 740"}},
        "structure": [{"type": "paragraf", "id": "P1",
                       "text": ["Skadestånd för sveda och värk."]}]}))
    other = art / "other.json"
    other.write_text(json.dumps({
        "uri": "https://lagen.nu/dom/nja/1993s41",
        "malnummer": ["B 1234-92"],
        "metadata": {"properties": {"dcterms:title": "NJA 1993 s. 41"}},
        "structure": [{"type": "paragraf", "id": "P1",
                       "text": ["Ansvar för misshandel."]}]}))
    cat = tmp_path / "catalog.sqlite"
    catalog.rebuild(cat, "dv", [nja, other])
    con = catalog.connect(cat)
    index = search.SearchIndex(index="lagen-test")
    try:
        index.index_source(con, "dv")
        for q in ("T 224-91", "t 224-91", '"T 224-91"', "T 369-91"):
            res = index.search(q, limit=5)
            assert res["results"][0]["uri"] == "https://lagen.nu/dom/nja/1992s740", q
        # the number belongs to one decision -- the other case is not a hit
        assert [r["uri"] for r in index.search("B 1234-92")["results"]] \
            == ["https://lagen.nu/dom/nja/1993s41"]
        # ... and the parts of a case number never match on their own
        assert index.search("skadestånd 92")["total"] == 0
    finally:
        if index.client.indices.exists(index="lagen-test"):
            index.client.indices.delete(index="lagen-test")


@pytest.mark.skipif(not os.environ.get("OPENSEARCH_URL"),
                    reason="needs a running OpenSearch (set OPENSEARCH_URL)")
def test_index_source_is_incremental(tmp_path):
    """Against a live cluster: a re-index with nothing changed touches nothing;
    editing one document re-indexes only it; removing it from the catalog drops
    its units. Exercises the content-hash diff + deletion sync, with jobs>1."""
    art = tmp_path / "artifact"
    art.mkdir()
    a = art / "a.json"
    a.write_text(json.dumps({
        "uri": "https://lagen.nu/1999:1", "metadata": {"properties":
        {"dcterms:title": "Alfa (1999:1)"}}, "structure": [
            {"type": "paragraf", "id": "P1", "text": ["Alfaregeln gäller."]}]}))
    b = art / "b.json"
    b.write_text(json.dumps({
        "uri": "https://lagen.nu/1999:2", "metadata": {"properties":
        {"dcterms:title": "Beta (1999:2)"}}, "structure": [
            {"type": "paragraf", "id": "P1", "text": ["Betaregeln gäller."]}]}))
    cat = tmp_path / "catalog.sqlite"
    catalog.rebuild(cat, "sfs", [a, b])
    con = catalog.connect(cat)
    index = search.SearchIndex(index="lagen-test")
    try:
        _, indexed, _, _, skipped, deleted = index.index_source(con, "sfs", jobs=2)
        assert (indexed, skipped, deleted) == (4, 0, 0)   # 2 docs * (doc + frag)

        # nothing changed -> nothing re-indexed, both skipped
        _, indexed, _, _, skipped, deleted = index.index_source(con, "sfs", jobs=2)
        assert (indexed, skipped, deleted) == (0, 2, 0)

        # edit one document -> only it is re-indexed
        a.write_text(json.dumps({
            "uri": "https://lagen.nu/1999:1", "metadata": {"properties":
            {"dcterms:title": "Alfa (1999:1)"}}, "structure": [
                {"type": "paragraf", "id": "P1", "text": ["Alfaregeln ändrad."]}]}))
        catalog.rebuild(cat, "sfs", [a, b])
        con = catalog.connect(cat)
        _, indexed, _, _, skipped, deleted = index.index_source(con, "sfs", jobs=2)
        assert (indexed, skipped, deleted) == (2, 1, 1)   # re-index a, skip b
        assert index.search("ändrad")["total"] == 1

        # drop one document from the catalog -> its units are deleted
        catalog.rebuild(cat, "sfs", [b])
        con = catalog.connect(cat)
        _, _, _, _, skipped, deleted = index.index_source(con, "sfs", jobs=2)
        assert (skipped, deleted) == (1, 1)
        assert index.search("alfaregeln")["total"] == 0
        assert index.search("betaregeln")["total"] == 1
    finally:
        if index.client.indices.exists(index="lagen-test"):
            index.client.indices.delete(index="lagen-test")


@pytest.mark.skipif(not os.environ.get("OPENSEARCH_URL"),
                    reason="needs a running OpenSearch (set OPENSEARCH_URL)")
def test_index_source_force_reindexes_all(tmp_path):
    """`force=True` reindexes every document regardless of content hash -- the
    full rebuild used when the index code changed (no hand-deleting the index)."""
    con = _build_catalog(tmp_path)
    index = search.SearchIndex(index="lagen-test")
    try:
        index.index_source(con, "sfs")
        _, indexed, _, _, skipped, _ = index.index_source(con, "sfs")
        assert (indexed, skipped) == (0, 2)              # nothing changed
        _, indexed, _, _, skipped, deleted = index.index_source(
            con, "sfs", force=True)
        assert skipped == 0 and indexed > 0 and deleted == 2   # both re-indexed
        assert index.search("mord")["total"] == 1        # still correct after
    finally:
        if index.client.indices.exists(index="lagen-test"):
            index.client.indices.delete(index="lagen-test")


def test_threaded_bulk_raises_when_a_worker_dies(monkeypatch):
    """A worker can die on something `raise_on_exception=False` does not cover --
    the serializer choking on a bad action, say. Left to itself it took its
    buffered chunk with it and `_threaded_bulk` still returned a clean count, so
    units went missing with a zero error count and a zero exit code: the very
    failure this path replaced `parallel_bulk` to stop."""
    # the first worker to draw *any* action is the one that dies, decided under a
    # lock -- picking it by call order instead let the loser be starved of actions
    # on a loaded machine and the test pass without ever raising
    victim, drawn, lock = [], [], threading.Lock()

    def dying_streaming_bulk(client, actions, **kw):
        me = object()
        for _ in actions:
            with lock:
                if not victim:
                    victim.append(me)
                mine = victim[0] is me
                if mine:
                    drawn.append(1)
            # the victim reports a few successes first, then dies holding what it
            # has buffered -- the shape that made `_threaded_bulk` return a clean
            # count for an incomplete index
            if mine and len(drawn) > 5:
                raise RuntimeError("worker blew up")
            yield True, {}

    monkeypatch.setattr(search.helpers, "streaming_bulk", dying_streaming_bulk)
    index = search.SearchIndex.__new__(search.SearchIndex)
    index.client = object()
    with pytest.raises(RuntimeError, match="worker blew up"):
        index._threaded_bulk(iter([{"_id": str(i)} for i in range(2000)]), 4,
                             {"index": "lagen-test"})


def test_threaded_bulk_does_not_hang_when_every_worker_dies(monkeypatch):
    """With no worker left draining it, the feeder blocks on a full queue and the
    whole command sits there forever -- a hang, not a crash, so nothing reports
    it. A dead worker keeps draining precisely so this cannot happen."""
    def dead_streaming_bulk(client, actions, **kw):
        raise RuntimeError("all dead")
        yield                                  # pragma: no cover -- a generator

    monkeypatch.setattr(search.helpers, "streaming_bulk", dead_streaming_bulk)
    index = search.SearchIndex.__new__(search.SearchIndex)
    index.client = object()
    with pytest.raises(RuntimeError, match="all dead"):
        index._threaded_bulk(iter([{"_id": str(i)} for i in range(5000)]), 2,
                             {"index": "lagen-test"})


def test_threaded_bulk_refuses_to_report_fewer_outcomes_than_actions(monkeypatch):
    """`streaming_bulk` yields exactly one outcome per action, so a shortfall
    means units reached the cluster unaccounted for. Raise rather than assert:
    `python -O` would strip the one check that catches silent under-indexing."""
    def swallowing_streaming_bulk(client, actions, **kw):
        for i, _ in enumerate(actions):
            if i % 2 == 0:                     # silently drops every other one
                yield True, {}

    monkeypatch.setattr(search.helpers, "streaming_bulk", swallowing_streaming_bulk)
    index = search.SearchIndex.__new__(search.SearchIndex)
    index.client = object()
    with pytest.raises(ValueError, match="fed 10 actions but accounted for"):
        index._threaded_bulk(iter([{"_id": str(i)} for i in range(10)]), 1,
                             {"index": "lagen-test"})


def test_threaded_bulk_does_not_hang_when_the_final_flush_dies(monkeypatch):
    """The narrowest window, and the one the first fix reopened: `streaming_bulk`
    sends its last buffered chunk *after* `drain` has taken this worker's
    sentinel, so a failure there has no sentinel left to drain to. Waiting for one
    parks the worker in `get()` and the caller in `join()` -- a silent hang of
    `lagen index`, the failure mode this whole path exists to remove."""
    def raise_after_the_stream_ends(client, actions, **kw):
        for _ in actions:                      # consumes the sentinel too
            yield True, {}
        raise RuntimeError("final chunk flush blew up")

    monkeypatch.setattr(search.helpers, "streaming_bulk",
                        raise_after_the_stream_ends)
    index = search.SearchIndex.__new__(search.SearchIndex)
    index.client = object()
    with pytest.raises(RuntimeError, match="final chunk flush"):
        index._threaded_bulk(iter([{"_id": str(i)} for i in range(50)]), 2,
                             {"index": "lagen-test"})


def test_sort_citations_query_shape_and_cursor_binding():
    """`sort=citations` swaps the sort clause to the stored inbound_count
    (doc_uri tiebreak kept, so search_after still works) and keeps relevance
    scores on the hits. A cursor is bound to the order that minted it: its
    opaque sort values are positions in ONE order, and replayed under another
    they would bind to different fields."""
    body = search.query_body("mord", sort="citations")
    assert body["sort"] == [{"inbound_count": {"order": "desc", "missing": 0}},
                            {"doc_uri": "asc"}]
    assert body["track_scores"] is True
    assert "track_scores" not in search.query_body("mord")

    cur = search.encode_cursor([42, "u1"], 10, "citations")
    assert search.decode_cursor(cur) == ([42, "u1"], 10, "citations")
    assert search.cursor_state(cur, "citations", None) == ([42, "u1"], 10)
    with pytest.raises(ValueError, match="made under sort=citations"):
        search.cursor_state(cur, "relevance", None)
    # a cursor minted before orders existed carries no "by": relevance
    legacy = base64.urlsafe_b64encode(json.dumps(
        {"sort": [7.5, "u1"], "seen": 3}).encode()).decode().rstrip("=")
    assert search.decode_cursor(legacy) == ([7.5, "u1"], 3, "relevance")
