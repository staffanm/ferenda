"""Editorial notes standing where statute text would be.

Two layers, like the graphics ones: the pure detector in ``sfs.redaktionell``
and its wiring into ``nf.to_normalform`` (a note stycke keeps its id, text and
beteckning but is retyped ``redaktionell``). Model trees are built directly so
the cases stay corpus-independent; the real shapes are 1919:878 and 1994:1283
(repeal notices) and the 23 acts whose whole body is "/Författningens text finns
bara i tryckt version/".
"""

from accommodanda.lib import diff
from accommodanda.lib.page import plain
from accommodanda.sfs import redaktionell
from accommodanda.sfs.model import Forfattning, Paragraf, Stycke
from accommodanda.sfs.nf import inline_references, to_normalform

BASEFILE = "2001:1"


def test_detects_the_print_only_gap():
    assert redaktionell.editorial(
        "/Författningens text finns bara i tryckt version/") \
        == ("endast-tryckt", None)
    # the noun varies across the 23 occurrences
    assert redaktionell.editorial(
        "/Bilagan finns bara i tryckt version/")[0] == "endast-tryckt"


def test_detects_a_repeal_notice_and_names_the_repealing_sfs():
    assert redaktionell.editorial("Har upphävts genom förordning (2024:1330).") \
        == ("upphavd", "2024:1330")
    assert redaktionell.editorial("Har upphävts genom föreskrifter (2024:1084).") \
        == ("upphavd", "2024:1084")
    # the variant naming the paragraf it replaced (1919:878)
    assert redaktionell.editorial("4 § har upphävts genom lag (1982:1101)") \
        == ("upphavd", "1982:1101")


def test_reads_the_publishers_malformed_repeal_notices():
    # 36 of the corpus's 306 notices have unbalanced or absent parentheses, or
    # no act type. Requiring the tidy form drops exactly the provisions this
    # module exists to type, so the tolerance is deliberate (see RE_UPPHAVD).
    for text, sfs in [
            ("Har upphävts genom förordning 2006:1412).", "2006:1412"),
            ("har upphävts genom förordning (1996:1302.", "1996:1302"),
            ("Har upphävts genom lag 2025:729", "2025:729"),
            ("27 § har upphävts genom förordning 1994:1306.", "1994:1306")]:
        assert redaktionell.editorial(text) == ("upphavd", sfs), text


def test_leaves_statute_text_alone():
    assert redaktionell.editorial("Denna lag gäller yrkesmässig trafik.") is None
    # prose *about* a repeal is a rule, not a note -- it runs past the bound and
    # does not start with the notice
    assert redaktionell.editorial(
        "Bestämmelserna i 4 § har upphävts genom lag (1982:1101), men "
        "äldre föreskrifter gäller fortfarande för sådan trafik som "
        "påbörjats före ikraftträdandet.") is None
    # a marker embedded in a longer stycke is not the whole note
    assert redaktionell.editorial(
        "Avgiften bestäms enligt bilagan. /Bilagan finns bara i tryckt "
        "version/ Avgiften betalas i förskott.") is None


def test_a_note_stycke_is_retyped_but_keeps_everything_else():
    doc = Forfattning(children=[Paragraf(
        ordinal="4", children=[Stycke("Har upphävts genom lag (1982:1101).")])])
    para = to_normalform(doc, BASEFILE)["structure"][0]
    node = para["children"][0]
    assert node["type"] == "redaktionell"
    assert node["sort"] == "upphavd"
    assert node["satt_av"] == "1982:1101"
    # the reader still sees the notice, and the paragraf still names itself
    assert plain(node["text"]) == "Har upphävts genom lag (1982:1101)."
    assert node["beteckning"] == "4 \xa7"
    # the anchor is the stycke's own, so an existing link still resolves
    assert node["id"] and node["id"].startswith("P4")


def test_ordinary_statute_text_stays_a_stycke():
    doc = Forfattning(children=[Paragraf(
        ordinal="1", children=[Stycke("Denna lag gäller yrkesmässig trafik.")])])
    para = to_normalform(doc, BASEFILE)["structure"][0]
    assert para["children"][0]["type"] == "stycke"


def test_a_note_still_appears_in_the_version_diff():
    # regressed on the first cut: `lib.diff._LEAF` did not list the new type, so
    # the node fell to the container branch and `blocks` emitted nothing -- a
    # repealed paragraf vanished entirely from "jämför med tidigare lydelser"
    doc = Forfattning(children=[Paragraf(
        ordinal="4", children=[Stycke("Har upphävts genom lag (1982:1101).")])])
    para = to_normalform(doc, BASEFILE)["structure"][0]
    blocks = diff.blocks([para])
    assert [b["kind"] for b in blocks] == ["redaktionell"]
    assert blocks[0]["marker"] == "4 \xa7"
    assert "upphävts" in blocks[0]["text"]


def test_a_note_still_carries_its_inline_links():
    # also regressed on the first cut: `inline_references` matched only
    # `kind == "stycke"`, so a retyped node fell to the container branch and its
    # *own* runs were never scanned -- dropping the link to the repealing SFS
    # that nf.py documents as surviving the retype. The node is written out the
    # way the real 1919:878 artifact carries it (reference runs already inlined,
    # which needs the register the synthetic model tree above has no access to).
    node = {"type": "redaktionell", "id": "S1", "sort": "upphavd",
            "satt_av": "1982:1101",
            "text": [{"predicate": "dcterms:references", "text": "4 §",
                      "uri": "https://lagen.nu/1919:878#P4"},
                     " har upphävts genom lag (",
                     {"predicate": "dcterms:references", "text": "1982:1101",
                      "uri": "https://lagen.nu/1982:1101"}, ")"]}
    uris = [uri for _frag, _pred, uri, _text in inline_references([node])]
    assert "https://lagen.nu/1982:1101" in uris
    assert "https://lagen.nu/1919:878#P4" in uris
