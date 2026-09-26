"""Upphandlingsmålssidan: a kammarrätt decision and the database's metadata.

Registered as this source's page renderer (the `render=` field of its
`build.py` registration);
`render` is the `(art, site) -> str` the generate driver calls.
"""

from ..lib import labels, tpl
from ..lib.page import BANNERS, doc_meta, document_body, page_context, render_toc

ENV = tpl.environment("ferenda.kkvdomar")

DOKTYP = {"dom": "Dom", "beslut": "Beslut"}


def render(art, site):
    md = art.get("metadata", {})
    meta = [
        ("Domstol", md.get("domstol")),
        ("Målnummer", md.get("malnummer")),
        ("Avgörandedatum", art.get("avgorandedatum")),
        ("Avgörandetyp", DOKTYP.get(art.get("doctype"))),
        ("Ärendemening", md.get("arendemening")),
        ("Ärendetyp", md.get("arendetyp")),
        ("Utgång", md.get("utgang")),
        ("Leverantör/Sökande", md.get("sokande")),
        ("Upphandlande myndighet/enhet", md.get("motpart")),
    ]
    structure, toc, rail = document_body(art, site)
    lb = labels.document_labels("kkvdomar", art)
    # 14 of the 3,835 cases name no decision PDF: the database's record is all
    # there is, and a page of metadata rows alone reads as a page that failed
    # to load
    banner = "" if art.get("structure") else BANNERS.text_not_held(
        "avgörandet", art.get("source_url"), "Konkurrensverket")
    return ENV.get_template("kkvdomar.html").render(page_context(
        lb.short_title or lb.short_id, "Avgörande i upphandlingsmål",
        doc_meta(meta, art.get("source_url")), doc_uri=art["uri"],
        short_id=lb.short_id, description=site.snippet(art["uri"]),
        toc=render_toc(toc, lb.short_id), eyebrow=lb.short_id,
        summary_text=art.get("kortreferat"), island=rail.island(),
        structure=structure, banner=banner))
