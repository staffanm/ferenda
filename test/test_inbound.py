"""The per-document inbound-citation artifact (ferenda/lib/inbound.py):
what goes in a document's file, and in what order.

The order is the point of most of these. A citation panel is always read as a
page of itself, so whichever rows sort first *are* the answer for most callers.
"""

import json

from ferenda.lib import catalog, compress, inbound


def _catalog(tmp_path, links, docs):
    con = catalog.connect(tmp_path / "catalog.sqlite")
    for uri, source, kind, label, date in docs:
        con.execute("INSERT INTO documents (uri, source, kind, label, title, "
                    "path, date) VALUES (?,?,?,?,?,?,?)",
                    (uri, source, kind, label, label, uri.rsplit("/", 1)[-1], date))
    for from_uri, anchor, to_uri in links:
        con.execute("INSERT INTO links (from_uri, from_anchor, predicate, "
                    "to_uri, to_root) VALUES (?,?,'dcterms:references',?,?)",
                    (from_uri, anchor, to_uri, to_uri.split("#")[0]))
    con.commit()
    return con


def test_order_leads_with_case_law_and_follows_the_rail():
    """The file is ordered by the website's rail sections, restricted to those
    that carry citations -- so `dv` (Rättsfall) leads and the citation graph
    (`sfs`) follows the practice, rather than either coming first because its
    source name happens to sort early."""
    assert inbound.ORDER[0] == "dv"
    assert inbound.ORDER.index("dv") < inbound.ORDER.index("sfs")
    assert inbound.ORDER.index("sfs") < inbound.ORDER.index("forarbete")
    # only citation-bearing sections: commentary and the amendment register are
    # ranked in the rail but are not inbound citations
    assert "kommentar" not in inbound.ORDER and "andringar" not in inbound.ORDER


def test_case_law_is_newest_first_and_undated_sorts_last(tmp_path):
    """Recent practice first is the rail's convention. The undated case is the
    one that bit: complementing the characters of an empty date yields an empty
    string, which sorts *before* every real date -- so without an explicit flag
    the handful of undated notisfall would head every statute's case-law
    section."""
    con = _catalog(
        tmp_path,
        [("https://x/old", "P1", "https://x/law"),
         ("https://x/new", "P1", "https://x/law"),
         ("https://x/undated", "P1", "https://x/law")],
        [("https://x/law", "sfs", "lag", "SFS 1962:700", "1962-01-01"),
         ("https://x/old", "dv", "case", "NJA 1999", "1999-01-01"),
         ("https://x/new", "dv", "case", "NJA 2020", "2020-01-01"),
         ("https://x/undated", "dv", "case", "NJA notis", None)])
    got = [r["label"] for r in inbound.citations(con, "https://x/law")]
    assert got == ["NJA 2020", "NJA 1999", "NJA notis"], got
    con.close()


def test_the_file_covers_the_document_and_every_provision_in_it(tmp_path):
    """`to_root`, not `to_uri`: a citation to 3 kap. 1 § belongs in
    brottsbalken's file. Answering from `to_uri` is what made a whole-law query
    return only the citations naming the act and none of the ones reaching into
    it."""
    con = _catalog(
        tmp_path,
        [("https://x/a", "P1", "https://x/law"),
         ("https://x/b", "P1", "https://x/law#K3P1")],
        [("https://x/law", "sfs", "lag", "SFS 1962:700", "1962-01-01"),
         ("https://x/a", "dv", "case", "NJA 2020", "2020-01-01"),
         ("https://x/b", "dv", "case", "NJA 2021", "2021-01-01")])
    rows = inbound.citations(con, "https://x/law")
    assert {r["target"] for r in rows} == {"https://x/law", "https://x/law#K3P1"}
    con.close()


def test_a_documents_own_links_into_itself_are_not_inbound(tmp_path):
    """A statute's internal "enligt 3 §" cross-references are its own outbound
    navigation. They are 41% of all links, so leaving them in would swamp every
    file with the document citing itself."""
    con = _catalog(
        tmp_path,
        [("https://x/law", "K1", "https://x/law#K3P1"),
         ("https://x/a", "P1", "https://x/law#K3P1")],
        [("https://x/law", "sfs", "lag", "SFS 1962:700", "1962-01-01"),
         ("https://x/a", "dv", "case", "NJA 2020", "2020-01-01")])
    assert [r["uri"] for r in inbound.citations(con, "https://x/law")] == \
        ["https://x/a"]
    con.close()


def _row(**over):
    row = {"target": "https://lagen.nu/1962:700", "uri": "https://x/a",
           "anchor": "P1", "page": None, "predicate": "dcterms:references",
           "label": "NJA 2020", "title": "T", "source": "dv", "kind": "case",
           "date": "2020-01-01"}
    return row | over


LAW = "https://lagen.nu/1962:700"


def test_write_and_read_round_trip(tmp_path):
    rows = [_row()]
    assert inbound.write(tmp_path, LAW, rows) is True
    assert inbound.read(tmp_path, LAW) == rows
    # stored through the artifact compression policy, like the artifact tree
    assert compress.exists(inbound.path(tmp_path, LAW))
    assert json.loads(compress.read_bytes(inbound.path(tmp_path, LAW)))["total"] == 1


def test_the_file_is_found_from_the_uri_alone(tmp_path):
    """Keyed by the page relpath, so the serving layer reads no catalog row to
    locate it -- and a fragment finds its document's file, which is the one that
    holds its citations."""
    inbound.write(tmp_path, LAW, [_row()])
    assert inbound.path(tmp_path, LAW).name == "1962:700.json"
    assert inbound.path(tmp_path, LAW + "#K3P1") == inbound.path(tmp_path, LAW)
    assert inbound.read(tmp_path, LAW + "#K3P1") == [_row()]


def test_a_uri_too_long_to_be_a_filename_still_gets_a_file(tmp_path):
    """A begrepp uri *is* its concept name, and the ones the citation extraction
    got wrong are whole sentences. They never became pages, so the site never hit
    this -- but they are cited, so the uncatalogued-target pass writes them, and
    a 255-byte component limit turned that into `OSError: File name too long`
    partway through a corpus run."""
    sentence = "https://lagen.nu/begrepp/" + "_".join(["Inkomst_av_näringsverksamhet"] * 12)
    p = inbound.path(tmp_path, sentence)
    assert len(p.name.encode()) <= 200
    assert inbound.write(tmp_path, sentence, [_row()]) is True
    assert inbound.read(tmp_path, sentence) == [_row()]
    # two long names sharing a prefix stay distinct (the digest is of the uri)
    assert inbound.path(tmp_path, sentence + "_och_annat").name != p.name


def test_nothing_is_written_for_an_uncited_document(tmp_path):
    """Just under half the corpus has no inbound citations; a file each would be
    121 624 of them. Absent reads as empty."""
    assert inbound.write(tmp_path, LAW, []) is False
    assert not compress.exists(inbound.path(tmp_path, LAW))
    assert inbound.read(tmp_path, LAW) == []


def test_a_document_that_stops_being_cited_loses_its_file(tmp_path):
    """Absence is only authoritative if it is *reached*. Writing nothing for an
    empty set while leaving the old file in place would serve the previous
    build's citations forever -- the one way a derived tree goes quietly wrong."""
    inbound.write(tmp_path, LAW, [_row()])
    assert inbound.write(tmp_path, LAW, []) is False
    assert not compress.exists(inbound.path(tmp_path, LAW))
    assert inbound.read(tmp_path, LAW) == []


def test_a_file_written_for_another_uri_is_not_served_as_this_ones(tmp_path):
    """`page_relpath` is not injective -- two begrepp slugs can land on one name,
    and generate drops the loser's page rather than clobbering the winner's.
    Serving the winner's citations under the loser's uri would be a wrong answer
    where no answer is the honest one."""
    inbound.write(tmp_path, LAW, [_row()])
    other = "https://lagen.nu/1949:105"
    inbound.path(tmp_path, LAW).parent.mkdir(parents=True, exist_ok=True)
    compress.write_bytes(inbound.path(tmp_path, other),
                         compress.read_bytes(inbound.path(tmp_path, LAW)),
                         encodings=compress.ARTIFACT_ENCODINGS)
    assert inbound.read(tmp_path, other) == []


def test_scope_tree_on_a_paragraf_reaches_its_stycken_but_not_the_next_paragraf():
    """"Who cites 3 kap. 1 §" has to reach `#K3P1S2` (467 164 links target a
    stycke) without swallowing `#K3P10`, which is a different paragraf whose
    anchor merely starts the same way."""
    rows = [_row(target=LAW + t) for t in
            ("#K3P1", "#K3P1S2", "#K3P1S1N2", "#K3P1M2", "#K3P10", "#K3P2")]
    assert [r["target"].rsplit("#", 1)[1] for r in inbound.scoped(rows, LAW + "#K3P1")] \
        == ["K3P1", "K3P1S2", "K3P1S1N2", "K3P1M2"]


def test_an_inserted_paragraf_is_a_sibling_not_a_subtree():
    """18 a § is its own paragraf beside 18 §, not part of it -- Swedish statutes
    insert provisions by lowercase suffix. A prefix rule that only excluded
    digits put 18 a §'s citations under 18 § (measured on 2016:1145, where the
    143 rows for 1 kap. 18 § quietly included every citation of 1 kap. 18 a §)."""
    rows = [_row(target=LAW + t) for t in ("#K1P18", "#K1P18S1", "#K1P18a",
                                           "#K1P18aS1")]
    assert [r["target"].rsplit("#", 1)[1]
            for r in inbound.scoped(rows, LAW + "#K1P18")] == ["K1P18", "K1P18S1"]
    # and 18 a § has its own subtree, reached from its own anchor
    assert [r["target"].rsplit("#", 1)[1]
            for r in inbound.scoped(rows, LAW + "#K1P18a")] == ["K1P18a", "K1P18aS1"]


def test_scope_tree_reads_the_eu_and_treaty_grammars_too():
    """The EU grammar separates with dots (`9.2` -> `9.2.S2` stycke, `9.2.a`
    point) and the treaty one with uppercase segments (`A5P1` -> `A5P1La`
    litera). Both are subtrees; `9.20` is a different article."""
    eu = "https://lagen.nu/celex/32016R0679#"
    rows = [_row(target=eu + t) for t in ("9.2", "9.2.S2", "9.2.a", "9.20")]
    assert [r["target"].rsplit("#", 1)[1] for r in inbound.scoped(rows, eu + "9.2")] \
        == ["9.2", "9.2.S2", "9.2.a"]
    coe = "https://lagen.nu/coe/echr#"
    rows = [_row(target=coe + t) for t in ("A5P1", "A5P1La", "A5P10")]
    assert [r["target"].rsplit("#", 1)[1] for r in inbound.scoped(rows, coe + "A5P1")] \
        == ["A5P1", "A5P1La"]


def test_scope_tree_on_a_document_is_the_whole_file():
    rows = [_row(target=LAW), _row(target=LAW + "#K3P1")]
    assert inbound.scoped(rows, LAW) == rows


def test_scope_exact_on_a_document_is_the_act_as_such():
    """The narrow question the endpoint used to answer, kept available: the
    citations naming the act itself, none of the ones reaching into it."""
    rows = [_row(target=LAW), _row(target=LAW + "#K3P1")]
    assert inbound.exact(rows, LAW) == [rows[0]]
