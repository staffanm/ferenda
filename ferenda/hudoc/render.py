"""Europadomstolssidan: the judgment body and its article metadata.

Registered as this source's page renderer (the `render=` field of its
`build.py` registration);
`render` is the `(art, site) -> str` the generate driver calls.
"""

from markupsafe import Markup

from ..lib import datasets, labels, tpl
from ..lib.margins import ext_link
from ..lib.page import (
    doc_meta,
    document_body,
    page_context,
    ref_list,
    render_toc,
)

ENV = tpl.environment("ferenda.hudoc")

# how a judgment's article references name their treaty: the curated Swedish
# short form ("artikel 8 EKMR"), not the Treaty Office's full official title
TREATY_NAMES = labels.treaty_names(datasets.COE_NAMES)


def _treaty_name(base):
    entry = TREATY_NAMES.get(base.rsplit("/", 1)[-1], {})
    return entry.get("abbr") or entry.get("label")


def _summary_link(summary):
    """The Court's own Case-Law Information Note on this case, as an outbound
    link. The note is a page of plain account where the judgment is forty pages
    of reasoning, so it is the reader's way in -- but it says what this page
    already says, so it is a link and not a document (`summaries.py`)."""
    if not summary:
        return None
    return ext_link(summary["url"], "Europadomstolens egen sammanfattning")


def render(art, site):
    md = art.get("metadata", {})
    lb = labels.document_labels("hudoc", art)
    # the application number is the eyebrow now, so it needs no dl row of its own
    meta = [
        ("Domstol", md.get("publisher")),
        ("Avgörandedatum", art.get("avgorandedatum")),
        ("ECLI", art.get("ecli")),
        ("Artiklar", ", ".join(md.get("articles", [])) or None),
        ("Sammanfattning", _summary_link(art.get("summary"))),
    ]
    structure, toc, rail = document_body(art, site)
    return ENV.get_template("hudoc.html").render(page_context(
        lb.short_title or art.get("itemid"), "Europadomstolen",
        doc_meta(meta, art.get("source_url")), doc_uri=art["uri"], short_id=lb.short_id,
        description=site.snippet(art["uri"]),
        toc=render_toc(toc, lb.short_id),
        eyebrow=lb.short_id,
        summary_text=("; ".join(md["conclusions"])
                      if md.get("conclusions") else None),
        island=rail.island(),
        refs=Markup(ref_list(site, "Berörda konventionsartiklar",
                              [ref["uri"] for ref in art.get("references", [])],
                              name=_treaty_name)),
        structure=structure))
