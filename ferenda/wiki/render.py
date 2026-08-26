"""Begreppssidan: the concept definition and what cites it.

Registered as this source's page renderer in `build.SOURCE_RENDERERS`;
`render` is the `(art, site) -> str` the generate driver calls.
"""

from markupsafe import Markup

from ..lib import catalog, tpl
from ..lib.page import (
    PANEL_CAP,
    doc_meta,
    document_body,
    ordered_sections,
    page_context,
    render_toc,
)
from ..lib.pinpoint import citation

ENV = tpl.environment("ferenda.wiki")

# Sections that are our own editorial writing linking to the term, not the
# corpus using it: other concept pages, the lagkommentar, curated external
# links. They are occurrences of a different kind, so the count of "how much of
# the law uses this term" leaves them out.
EDITORIAL_KEYS = frozenset({"begrepp", "kommentar", "vagledning"})

# What a group is called on a concept page, where the shared rail label says the
# wrong thing. "Lagrumshänvisningar hit" describes the margin it was written for
# -- "hit" means "to the paragraph you are reading". Here the same rows are the
# acts that give the term a legal definition, so they are named for what they
# are. Only the body listing is renamed; the rail keeps the shared vocabulary it
# shares with every other page.
GROUP_LABEL = {"sfs": "Legaldefinitioner"}

# The sources that reach a concept by *defining* the term: an SFS
# begreppsdefinition, an EU act's definitions article. The page prints what each
# of them says the term means (`_definitions`), so listing the same acts again
# as bare citations -- in the reading column or in the margin -- prints the list
# twice.
DEFINING_KEYS = frozenset({"sfs", "eurlex"})


def _definitions(uri, site):
    """What every act that defines this term says it means: the act's own
    sentence, and a citation that links to the provision stating it.

    Read from the catalog, which relate filled while it had the artifact open
    (`catalog.definition_sentences`) -- a term defined in a hundred acts would
    otherwise open a hundred artifacts, on every one of ~28,900 concept pages."""
    return [{"href": "%s#%s" % (act, anchor) if anchor else act,
             "citation": citation("%s#%s" % (act, anchor or ""), descriptive),
             "term": term, "sentence": sentence}
            for act, anchor, descriptive, term, sentence
            in catalog.concept_definitions(site.con, uri)]


def render(art, site):
    """A concept definition; its inbound panel shows everything (laws, cases,
    förarbeten, commentary, other concepts) that references the concept.

    Only 568 of ~28,900 concepts have a written description, and that is the
    design rather than a gap: the namespace is the corpus's own uncoordinated
    vocabulary -- a court's sökord, a statute's legaldefinition, a term of art --
    and knowing that "verksamhetsutövare" is defined by eight different laws is
    worth publishing with no description written. So a page with no description
    puts its occurrences in the *reading column*: it is an index of where the
    term is used, not an article whose text is missing. A described page keeps
    them in the context rail beside the prose, where they belong."""
    title = art.get("title") or catalog.local(art["uri"])
    meta = [("Kategori", ", ".join(art.get("categories") or []))]
    structure, toc, rail = document_body(art, site, key="body")
    has_description = bool(art.get("body"))
    definitions = _definitions(art["uri"], site)
    if definitions:
        rail.drop_document_sections(DEFINING_KEYS)
    groups, uses, island = [], 0, rail.island()
    if not has_description:
        # `document_body` closed the rail with `add_document`, which already
        # built these -- taking them again would run the catalog query twice
        sections = ordered_sections(rail.doc_sections)
        # the defining acts left the rail above, so they are counted from the
        # definitions instead -- an act that defines the term is a document the
        # term occurs in, and the lede would otherwise undercount by exactly them
        uses = sum(s.count for s in sections if s.key not in EDITORIAL_KEYS) \
            + len({d["href"].partition("#")[0] for d in definitions})
        # the rail's own markup carries accordion and scrollspy semantics that
        # mean nothing in a reading column, so take the order and bring our own
        groups = [{"key": s.key, "count": s.count, "html": Markup(s.html),
                   "label": GROUP_LABEL.get(s.key, s.label)} for s in sections]
        # the same content twice -- once in the column, once in the margin --
        # would make the rail a duplicate of what the reader is already reading
        island = ""
    return ENV.get_template("begrepp.html").render(page_context(
        title, "Begrepp", doc_meta(meta, art.get("source_url")),
        toc=render_toc(toc, title), eyebrow="Begrepp", island=island,
        structure=structure, has_description=has_description,
        definitions=definitions, definition_cap=PANEL_CAP,
        groups=groups, uses=uses))
