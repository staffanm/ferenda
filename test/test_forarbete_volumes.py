"""Which of a förarbete record's PDFs are its body (accommodanda/forarbete/
volumes.py). The record's `files` is every PDF the landing page linked, so the
rule has to tell a volume from a rättelseblad, an English summary, a reprinted
EU directive and a duplicate 'hela dokumentet' edition."""

from accommodanda.forarbete import volumes


def _rec(files, typ="prop", basefile="2015/16:195", labels=None, **extra):
    return {"type": typ, "basefile": basefile, "files": files,
            "_labels": labels} | extra


def _probe(spec):
    """spec: name -> (pages, title, first page text)."""
    return lambda name: spec.get(name, (100, "", "Regeringens proposition"))


def test_population_is_read_off_the_record_alone():
    assert volumes.population(_rec([], orig_url="http://urn.kb.se/resolve?x")) \
        == "kb"
    assert volumes.population(_rec([], basefile="2024/25:1")) == "budget"
    assert volumes.population(_rec([], basefile="2015/16:100")) == "budget"
    assert volumes.population(_rec([], source="dsregeringen")) == "legacy"
    assert volumes.population(_rec([])) == "live"


def test_a_single_pdf_is_always_the_body():
    rec = _rec(["a.pdf"])
    assert volumes.body_pdfs(rec, _probe({})) == (["a.pdf"], {})


def test_kb_scan_set_keeps_only_the_first_file():
    # sou/1996:158's 22 files are Bilaga 15, 21, 14, 16 … of the EMU-utredningen
    # -- sibling volumes catalogued under one SOU number, not parts of one text
    rec = _rec(["a.pdf", "b.pdf", "c.pdf"], typ="sou", basefile="1996:158",
               orig_url="http://urn.kb.se/resolve?urn=x")
    body, dropped = volumes.body_pdfs(rec, _probe({}))
    assert body == ["a.pdf"]
    assert set(dropped) == {"b.pdf", "c.pdf"}


def test_budget_proposition_is_skipped_whole():
    rec = _rec(["a.pdf", "b.pdf"], basefile="2024/25:1")
    body, dropped = volumes.body_pdfs(rec, _probe({}))
    assert body == [] and len(dropped) == 2


def test_multi_volume_document_keeps_every_labelled_part():
    # prop. 2015/16:195, the case the concatenation was written for
    rec = _rec(["v1.pdf", "v2.pdf", "v3.pdf", "v4.pdf"], labels=[
        "Nytt regelverk om upphandling, del 1 av 4, kapitel 1-21",
        "Nytt regelverk om upphandling, del 2 av 4, kapitel 22-36",
        "Nytt regelverk om upphandling, del 3 av 4, bilaga 1-19",
        "Nytt regelverk om upphandling, del 4 av 4, bilaga 20-30"])
    body, dropped = volumes.body_pdfs(rec, _probe({}))
    assert body == ["v1.pdf", "v2.pdf", "v3.pdf", "v4.pdf"] and dropped == {}


def test_a_rattelseblad_is_dropped_even_as_the_first_file():
    # sou/2016:77: files[0] is a one-page Rättelseblad and the 861-page
    # betänkande is files[1]. "Read the first PDF" published the erratum as the
    # whole SOU; "read them all" glued the erratum onto the front of it.
    rec = _rec(["r.pdf", "body.pdf"], typ="sou", basefile="2016:77")
    body, dropped = volumes.body_pdfs(rec, _probe({
        "r.pdf": (1, "Microsoft Word - Rättelseblad ang sid 199", "Rättelseblad"),
        "body.pdf": (861, "En gymnasieutbildning för alla", "Betänkande av")}))
    assert body == ["body.pdf"] and dropped == {"r.pdf": "rättelse"}


def test_hela_dokumentet_wins_over_its_own_parts():
    # lr/2007 ny lag om värdepappersmarknaden: 1009 pages == 664 + 345, so
    # keeping all three would read the whole text twice
    rec = _rec(["whole.pdf", "p1.pdf", "p2.pdf"], typ="lr", basefile="2007:x")
    body, dropped = volumes.body_pdfs(rec, _probe({
        "whole.pdf": (1009, "", "Lagrådsremiss"),
        "p1.pdf": (664, "", "Lagrådsremiss"),
        "p2.pdf": (345, "", "Lagrådsremiss")}))
    assert body == ["whole.pdf"]
    assert set(dropped) == {"p1.pdf", "p2.pdf"}


def test_english_summary_and_reprinted_eu_act_are_not_body():
    rec = _rec(["body.pdf", "sum.pdf", "eu.pdf"], labels=[
        "Betänkandet", "Summary in English", "Direktivet"])
    body, dropped = volumes.body_pdfs(rec, _probe({
        "body.pdf": (300, "", "Regeringens proposition"),
        "sum.pdf": (13, "Summary", "Summary The inquiry proposes"),
        "eu.pdf": (95, "", "L 96/118 SV Europeiska unionens officiella tidning")}))
    assert body == ["body.pdf"]
    assert dropped == {"sum.pdf": "engelsk", "eu.pdf": "eu-rättsakt"}


def test_an_unlabelled_extra_is_dropped_when_labels_exist():
    # with link texts available, a further volume needs positive evidence
    rec = _rec(["body.pdf", "other.pdf"], labels=["Promemorian", "Remisslistan"])
    body, dropped = volumes.body_pdfs(rec, _probe({}))
    assert body == ["body.pdf"] and dropped == {"other.pdf": "remisslista"}


def test_without_link_texts_everything_not_ruled_out_is_kept():
    # the legacy `_N` records have no landing page, and their files really are
    # consecutive parts -- missing evidence must not be read as evidence of
    # absence, or all 40 of them lose their later volumes
    rec = _rec(["a.pdf", "a_2.pdf", "a_3.pdf"], typ="ds", basefile="2000:39",
               source="dsregeringen")
    body, dropped = volumes.body_pdfs(rec, _probe({}))
    assert body == ["a.pdf", "a_2.pdf", "a_3.pdf"] and dropped == {}


def test_underrattelse_is_not_a_rattelse():
    # the errata pattern must not fire on "underrättelseskyldighet", which cost
    # sou/2018:14 its 366-page body in an earlier draft of this rule
    rec = _rec(["body.pdf", "x.pdf"], labels=["Betänkandet", "Bilaga"])
    body, dropped = volumes.body_pdfs(rec, _probe({
        "body.pdf": (366, "", "Betänkande om underrättelseskyldighet vid"),
        "x.pdf": (10, "", "Något annat")}))
    assert body == ["body.pdf"]                 # the betänkande survives ...
    assert dropped == {"x.pdf": "separat dokument"}   # ... on its own merits


def test_a_record_of_only_extras_reports_that_rather_than_guessing():
    # every file read as an extra: returning files[0] would hand back exactly
    # the file the module distrusts, and reporting nothing would look like a
    # clean single-volume decision
    rec = _rec(["r.pdf", "sum.pdf"], labels=["Rättelseblad", "Summary"])
    body, dropped = volumes.body_pdfs(rec, _probe({}))
    assert body == []
    assert set(dropped) == {"r.pdf", "sum.pdf"}


def test_labels_are_looked_up_by_position_in_files_not_among_the_pdfs():
    # `files` may hold a .docx beside the PDFs; indexing labels by the PDF-only
    # position shifted every label after it
    rec = _rec(["notes.docx", "body.pdf", "part2.pdf"],
               labels=["Bilagematerial (docx)", "Betänkandet del 1 av 2",
                       "Betänkandet del 2 av 2"])
    body, _dropped = volumes.body_pdfs(rec, _probe({}))
    assert body == ["body.pdf", "part2.pdf"]


def test_a_curated_skip_entry_takes_the_document_out_entirely():
    # Ds 2001:15 is a consultant's report in 13 unsorted part-files with no
    # page numbering and no författningsförslag -- parsing it produces a page
    # that is wrong rather than thin
    rec = _rec(["a.pdf", "b.pdf"], typ="ds", basefile="2001:15")
    assert volumes.population(rec) == "skip"
    body, dropped = volumes.body_pdfs(rec, _probe({}))
    assert body == []
    assert all("författningsförslag" in why for why in dropped.values())
    # the gate must fire for a single-PDF record too -- the skip list is a
    # judgement about the document, not about how many files it happens to hold
    single = _rec(["a.pdf"], typ="ds", basefile="2001:15")
    body, dropped = volumes.body_pdfs(single, _probe({}))
    assert body == [] and set(dropped) == {"a.pdf"}


def test_the_historical_corpus_is_not_skipped():
    # the old codebase marked 19,571 propositions "metadataonly", nearly all of
    # them the 1860s-1950s scans. That was a resource-constraint workaround, not
    # a judgement about the documents, and is deliberately not ported -- the
    # rewrite parses them in full.
    for basefile in ("1867:1", "1912:52", "1949:100"):
        assert volumes.population(_rec([], basefile=basefile)) == "live"


def test_every_skiplist_entry_is_well_formed():
    # the list is hand-edited data; a typo'd key would silently never match
    for key, why in volumes._skiplist().items():
        typ, _, basefile = key.partition("/")
        assert typ in ("prop", "sou", "ds", "pm", "dir", "fm", "skr", "so", "lr"), key
        assert ":" in basefile, key
        assert why and isinstance(why, str), key
