"""Which of a förarbete record's PDFs are its body (ferenda/forarbete/
volumes.py). The record's `files` is every PDF the landing page linked, so the
rule has to tell a volume from a rättelseblad, an English summary, a reprinted
EU directive and a duplicate 'hela dokumentet' edition."""

import pytest

from ferenda.forarbete import render, volumes


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


def test_kb_scan_set_reads_the_report_first_and_its_appendices_after():
    # sou/1997:116: KB lists the appendix before Barnkommitténs huvudbetänkande,
    # and taking the first file as the work published the appendix
    rec = _rec(["1997-116.pdf", "1997-116-1.pdf"][::-1], typ="sou",
               basefile="1997:116", orig_url="http://urn.kb.se/resolve?urn=x",
               volumes=[
                   "Barnets bästa i främsta rummet  FN:s konvention om barnets "
                   "rättigheter förverkligas i Sverige : Barnkommitténs "
                   "huvudbetänkande",
                   "Barnets bästa i främsta rummet  Bil.,FN:s konvention om "
                   "barnets rättigheter förverkligas i Sverige."])
    body, dropped = volumes.body_pdfs(rec, _probe({}))
    assert body == ["1997-116-1.pdf", "1997-116.pdf"] and dropped == {}
    assert volumes.page_labels(rec) == {"1997-116.pdf": "Bilaga"}


def test_kb_scan_set_without_volume_titles_is_refused():
    rec = _rec(["a.pdf", "b.pdf"], typ="sou", basefile="1996:158",
               orig_url="http://urn.kb.se/resolve?urn=x")
    with pytest.raises(AssertionError, match="soukb-scans"):
        volumes.body_pdfs(rec, _probe({}))


@pytest.mark.parametrize("title, expected", [
    ("Långtidsutredningen 1987.  Bil. 25,", ("appendix", "Bilaga 25")),
    ("Företagsförvärv i svenskt näringsliv  Bil. 1-5,betänkande", ("appendix", "Bilaga 1-5")),
    ("Arbete och hälsa  betänkande. Bilagedel D", ("appendix", "Bilaga D")),
    ("Sverige, framtiden och mångfalden  Bil. [A],slutbetänkande", ("appendix", "Bilaga A")),
    ("Skogspolitiken inför 2000-talet  huvudbetänkande. Bilagor II", ("appendix", "Bilaga II")),
    ("Förnyelse av kreditmarknaden  slutbetänkande. Bilaga", ("appendix", "Bilaga")),
    ("Omställning av energisystemet  D. 3slutbetänkande. Underlagsbilagor,", ("appendix", "Del 3")),
    ("Reformerad inkomstbeskattning  D. 2,betänkande.", ("main", "Del 2")),
    ("Partnerskap  D. Abetänkande.", ("main", "Del A")),
    ("Vilka vattendrag skall skyddas?  2,betänkande.", ("main", "Del 2")),
    ("Svensk kärnteknisk tillsynsverksamhet  Vol. 1,betänkande.", ("main", "Del 1")),
    ("Ett reformerat åklagarväsende: betänkande D. B", ("main", "Del B")),
    ("Arbete och hälsa  betänkande", ("main", None)),
    ("From massmedia to multimedia  English summary and conclusion", ("english", None)),
    ("Environment for sustainable health development  an action plan", ("english", None)),
    ("Märk väl!  [om märkning av varor vi köper nästan varje dag] : lättläst", ("kortversion", None)),
    ("Ny socialtjänstlag  sammanfattning och lagförslag : särtryck", ("sammanfattning", None)),
])
def test_kb_volume_reads_what_a_volume_is_off_its_title(title, expected):
    # every form here is taken from KB's index
    assert volumes.kb_volume(title) == expected


def test_kb_order_puts_the_parts_in_order_and_names_other_reports():
    # sou/1989:33 lists D. 2, D. 1, D. 3, D. 4
    order, _ = volumes.kb_order(["a", "b", "c", "d"], [
        "Reformerad inkomstbeskattning  D. 2,betänkande.",
        "Reformerad inkomstbeskattning  D. 1,betänkande.",
        "Reformerad inkomstbeskattning  D. 3,betänkande.",
        "Reformerad inkomstbeskattning  D. 4,betänkande. Bilagor, expertrapport"])
    assert order == [("b", None), ("a", "Del 2"), ("c", "Del 3"), ("d", "Del 4")]
    # sou/1987:3: two reports of their own follow the Långtidsutredning and
    # its appendices, under their own names
    order, _ = volumes.kb_order(["a", "b", "c"], [
        "Sveriges arbetskraft  prognos till år 2000",
        "Långtidsutredningen 1987.  Bil. 3,",
        "Långtidsutredningen 1987"])
    assert order == [("c", None), ("b", "Bilaga 3"), ("a", "Sveriges arbetskraft")]
    # English versions go, as for a live record
    order, dropped = volumes.kb_order(["a", "b"], [
        "Sweden and Europe  committee of enquiry: Consequences of the EU",
        "Sverige och Europa  en samhällsekonomisk konsekvensanalys"])
    assert order == [("b", None)] and dropped == {"a": "english"}


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
    # Historical source metadata marked 19,571 propositions "metadataonly",
    # mostly scans from the 1860s-1950s. That reflected processing cost, not a
    # judgement about the documents. Ferenda parses them in full.
    for basefile in ("1867:1", "1912:52", "1949:100"):
        assert volumes.population(_rec([], basefile=basefile)) == "live"


def test_every_skiplist_entry_is_well_formed():
    # the list is hand-edited data; a typo'd key would silently never match
    for key, why in volumes._skiplist().items():
        typ, _, basefile = key.partition("/")
        assert typ in ("prop", "sou", "ds", "pm", "dir", "fm", "skr", "so", "lr"), key
        assert ":" in basefile, key
        assert why and isinstance(why, str), key


def test_a_pdf_broken_at_the_source_is_dropped_without_being_probed():
    # skr. 2000/01:38's Swedish volume is 65 536 truncated bytes on
    # regeringen.se's own server, so poppler cannot open it at all -- `probe`
    # raises rather than answering. The file must be gone before anything
    # counts or reads it, leaving the intact English sibling to be judged on
    # its own evidence (and dropped as "engelsk").
    def exploding_probe(name):
        if name == "2000-01-38.pdf":
            raise AssertionError("a BROKEN_PDFS file must never be probed")
        return (67, "", "Government Communication")

    rec = _rec(["2000-01-38.pdf", "2000-01-38-1.pdf"],
               typ="skr", basefile="2000/01:38",
               labels=["Hållbara Sverige - uppföljning av åtgärder",
                       "Sustainable Sweden a Progress Report on Measures"])
    body, dropped = volumes.body_pdfs(rec, exploding_probe)
    assert body == []
    assert dropped["2000-01-38.pdf"] == "trasig hos källan"
    assert dropped["2000-01-38-1.pdf"] == "engelsk"


def test_a_record_whose_only_other_file_is_broken_falls_back_to_one_volume():
    # dropping the broken file must not leave a two-file record being weighed
    # by the multi-volume rules on one real file
    rec = _rec(["2000-01-38.pdf", "good.pdf"], typ="skr", basefile="2000/01:38")
    body, dropped = volumes.body_pdfs(rec, _probe({}))
    assert body == ["good.pdf"]
    assert dropped == {"2000-01-38.pdf": "trasig hos källan"}


def test_every_broken_pdf_entry_is_well_formed():
    # hand-edited data keyed "<type>/<basefile>/<filename>"; a typo'd key would
    # silently never match and the document would keep failing every build
    for key, why in volumes.BROKEN_PDFS.items():
        typ, _, rest = key.partition("/")
        assert typ in ("prop", "sou", "ds", "pm", "dir", "fm", "skr", "so", "lr"), key
        basefile, _, name = rest.rpartition("/")
        assert ":" in basefile, key
        assert name.lower().endswith(".pdf"), key
        assert why and isinstance(why, str), key


def test_a_volume_label_is_shown_as_written_and_anchored_off_it():
    # a number is an appendix the body's own pages detected, and keeps the
    # #bilaga23-sid{N} anchor those pages always had
    assert render.volume_label("23") == "Bilaga 23"
    assert render.volume_anchor(render.volume_label("23")) == "bilaga23"
    assert render.volume_anchor("Del 2") == "del2"
    assert render.volume_anchor("Bilaga 1-5") == "bilaga1-5"
    assert render.volume_anchor("Bilaga") == "bilaga"
