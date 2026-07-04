"""The remiss (referral) feedback rail on a förarbete page.

The remisser corpus is never published as its own pages -- its only surface is a
context rail on the *referred* SOU/Ds: `_remiss_indexes` walks the remisser
artifact tree, reads each analyzed answer's `.ann` sidecar, and keys the
feedback onto the förarbete's own uri + section id so `render_forarbete` can show
it. No live LLM (and hence no real `.ann`) is available in the sandbox, so this
builds a synthetic answer + `.ann` + host förarbete under `tmp_path`.
"""

import json

from accommodanda.lib import catalog, layout, render


def _scenario(tmp_path, monkeypatch):
    """A one-answer synthetic corpus: a host SOU with a matching avsnitt id, and a
    remissvar artifact + `.ann` referring to it. Returns the förarbete uri."""
    monkeypatch.setattr(layout, "REMISSER_ROOT", tmp_path / "remisser")
    monkeypatch.setitem(layout.ARTIFACT_ROOT, "remisser", tmp_path / "remisser")
    monkeypatch.setitem(layout.ARTIFACT_ROOT, "forarbete", tmp_path / "forarbete")

    fa_uri = "https://lagen.nu/sou/2020:1"
    fa_path = layout.artifact("forarbete", "sou/2020-1")
    fa_path.parent.mkdir(parents=True, exist_ok=True)
    fa_path.write_text(json.dumps({
        "uri": fa_uri, "identifier": "SOU 2020:1", "title": "En utredning",
        "type": "sou", "source_url": "https://regeringen.se/sou/2020-1",
        "structure": [
            {"type": "avsnitt", "id": "a14.3.4", "level": 1, "num": "14.3.4",
             "text": ["14.3.4 Om ansvaret"], "page": 42,
             "children": [{"type": "stycke", "page": 42,
                           "text": ["Utredningen föreslår ett delat ansvar."]}]}]}))

    svar_path = layout.artifact("remisser", "en-utredning/kammarkollegiet")
    svar_path.parent.mkdir(parents=True, exist_ok=True)
    svar_path.write_text(json.dumps({
        "basefile": "en-utredning/kammarkollegiet",
        "case_basefile": "en-utredning", "organisation": "Kammarkollegiet",
        "case_titel": "Remiss av SOU 2020:1",
        "remitterat": [{"typ": "sou", "basefile": "2020:1"}],
        "source_url": "https://regeringen.se/svar/kammarkollegiet.pdf",
        "full_text": ["Kammarkollegiet tillstyrker förslaget om delat ansvar."]}))
    svar_path.with_suffix(".ann").write_text(json.dumps({
        "overall": {"sentiment": 0.5, "quote": "Kammarkollegiet tillstyrker"},
        "segments": [{"forarbete_id": "a14.3.4", "sentiment": 0.6,
                      "quote": "delat ansvar"}]}))
    return fa_uri, fa_path


def test_remiss_indexes_keys_feedback_on_forarbete_uri(tmp_path, monkeypatch):
    fa_uri, _ = _scenario(tmp_path, monkeypatch)
    # con is unused (remisser is never in the catalog); walk the filesystem
    feedback, overall = render._remiss_indexes()

    assert list(feedback) == [(fa_uri, "a14.3.4")]
    item = feedback[(fa_uri, "a14.3.4")][0]
    assert item == {"organisation": "Kammarkollegiet", "sentiment": 0.6,
                    "quote": "delat ansvar",
                    "source_url": "https://regeringen.se/svar/kammarkollegiet.pdf"}

    assert list(overall) == [fa_uri]
    assert overall[fa_uri][0]["organisation"] == "Kammarkollegiet"
    assert overall[fa_uri][0]["quote"] == "Kammarkollegiet tillstyrker"


def test_remiss_indexes_skips_unanalyzed_answer(tmp_path, monkeypatch):
    fa_uri, _ = _scenario(tmp_path, monkeypatch)
    # a second answer with no .ann yet is unanalyzed -- silently skipped
    other = layout.artifact("remisser", "en-utredning/domstolsverket")
    other.write_text(json.dumps({
        "basefile": "en-utredning/domstolsverket",
        "case_basefile": "en-utredning", "organisation": "Domstolsverket",
        "case_titel": "Remiss av SOU 2020:1",
        "remitterat": [{"typ": "sou", "basefile": "2020:1"}],
        "source_url": "https://regeringen.se/svar/domstolsverket.pdf",
        "full_text": ["Domstolsverket avstyrker."]}))
    feedback, overall = render._remiss_indexes()
    assert len(overall[fa_uri]) == 1        # only the analyzed answer contributes


def test_remiss_html_escapes_org_and_quote():
    rail = render.Rail(render.Site(None, set()), "https://lagen.nu/sou/2020:1")
    html = rail._remiss_html([{"organisation": "A & B <Org>", "sentiment": -0.8,
                               "quote": "de <säger> \"nej\"",
                               "source_url": "https://x/svar.pdf"}])
    assert "Remissvar" in html
    assert "A &amp; B &lt;Org&gt;" in html          # organisation escaped
    assert "de &lt;säger&gt;" in html               # quote escaped
    assert "sentiment-neg" in html                  # negative sentiment class
    assert 'rel="external"' in html and "svar.pdf" in html


def test_forarbete_avsnitt_carries_remiss_rail(tmp_path, monkeypatch):
    fa_uri, fa_path = _scenario(tmp_path, monkeypatch)
    db = str(tmp_path / "catalog.sqlite")
    catalog.rebuild(db, "forarbete", [fa_path])
    con = catalog.connect(db)
    site = render.Site.from_catalog(con)
    assert (fa_uri, "a14.3.4") in site.remiss_feedback   # index picked up

    html = render.render_forarbete(json.loads(fa_path.read_text()), site)
    # the avsnitt heading is now wired to the scroll-driven rail
    assert 'data-rail="a14.3.4"' in html
    island = json.loads(
        html.split('id="lagen-context">', 1)[1].split("</script>", 1)[0])
    assert "Remissvar" in island["a14.3.4"]
    assert "Kammarkollegiet" in island["a14.3.4"]
    assert "delat ansvar" in island["a14.3.4"]
    # the document-level "most interesting" overall panel at the top
    assert "Kammarkollegiet tillstyrker" in island[""]
