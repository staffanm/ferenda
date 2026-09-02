"""Citation-shaped query resolution, shaped as search hits -- the one
implementation behind both the REST `/api/v1/search` endpoint and the MCP
`search`/`resolve_citation` tools.

A query that *is* a citation -- a law nickname/abbr + pinpoint ("avtalslagen
36", "BrB 12:1"), an EU act + article ("GDPR art 32") or a case nickname
("Instagrambilden") -- maps to one exact, fragment-deep target that full-text
can't reach (the name is nowhere in the document). `resolve.resolve` proposes
the target(s); each is confirmed against the catalog (so an alias for a
not-yet-parsed document doesn't surface) and honours the same source/kind
filter, and the document's own label/title/inbound_count are attached so a
pinned hit ranks and renders like any other search hit.
"""

import re

from . import catalog, layout, resolve, text
from .pinpoint import acronym, pinpoint_label

# how much of the resolved provision's own text to carry as the hit's snippet --
# enough to recognise the rule, short enough to sit on two lines in the palette
SNIPPET_CHARS = 240


def resolved_results(con, q, source=None, kind=None):
    """The resolver's hits for `q`, each shaped like a SearchResult dict
    (uri, url, identifier, title, display, source, kind, inbound_count, pin,
    fragments). Empty when `q` reads as no known citation."""
    out = []
    for hit in resolve.resolve(q):
        if source and hit["source"] != source:
            continue
        root, _, frag = hit["uri"].partition("#")
        row = catalog.document(con, root)
        if not row and hit["source"] == "sfs":
            # a bare SFS number can name a page-number law ("SFS 1904:48" ->
            # 1904:48_s.1); the page suffix is only knowable from the catalog
            row = catalog.document_by_prefix(con, root + "_s.")
            if row:
                root = row[0]
        if not row:
            continue
        _uri, src, kind_, label, title, _path, descriptive, _url = row
        if kind and kind_ != kind:
            continue
        # the same reader-facing heading the page and full-text hits show (short
        # name + acronym where the artifact has them, else the title) -- stored
        # on the documents row at relate, so no artifact load per resolved hit
        display = catalog.document_display(con, root) or title
        pin = _pin(con, _path, root, frag) if frag else None
        if pin and hit.get("reason"):
            # a named span's own rationale outranks the provision's own words
            # as the hit's snippet -- a reader who typed "cookielagen" wants
            # to know why 9 kap. 28 § LEK carries that name, not to read the
            # paragraf itself (they can already follow the pin there)
            pin["highlight"] = [hit["reason"]]
        out.append({
            "uri": root, "url": layout.page_url(root),
            "identifier": label, "title": title, "display": display,
            # the acronym is the whole name line for a hit that spends its
            # second line on the pinpoint: "EKMR", where the display heading
            # ("Convention for the Protection of Human Rights and Fundamental
            # Freedoms") would fill the row and say nothing the pin does not
            "abbr": acronym(display) or acronym(descriptive) or None,
            "source": src, "kind": kind_,
            "score": None, "inbound_count": catalog.document_inbound_count(con, root),
            "highlight": [],
            # A pinned hit answers a *pinpoint*, so it says which provision it
            # landed on and shows that provision's own words. Without them the
            # reader saw "Brottsbalk (1962:700)" for "4 kap. 4 § brottsbalken"
            # and had no way to tell the pin had worked at all (Q2).
            #
            # `pin`, not `fragments`: the pin IS the answer and the hit links
            # there, while a full-text hit's `fragments` are passages inside a
            # document that stays the link target. Both used to arrive as
            # `fragments`, and the client could not tell a resolved provision
            # from a place the words happened to occur -- so "dataförordningen"
            # linked into article 47 of the EU Data Act.
            "pin": pin,
            "fragments": [],
        })
    return out


# an article heading that opens with the article's own designation, as a treaty
# article's does ("Article 6 - Right to a fair trial") where an EU act keeps the
# two apart ("Säkerhet i samband med behandlingen" under "Artikel 32")
_DESIGNATION = re.compile(r"(?:article|artikel|art\.)\s*\d", re.I)


def _pin_label(frag, heading):
    """What names the resolved provision: the pinpoint as a reader cites it
    ("4 kap. 5 §", "artikel 32"), and the heading the document prints over it
    where there is one -- "artikel 32 - Säkerhet i samband med behandlingen",
    which says what the article is about where the bare number does not.

    A heading that already opens with its own designation stands alone: "artikel
    6 - Article 6 - Right to a fair trial" says the number twice. An anchor with
    no citation grammar (a förarbete's "sec745") is named by its heading only."""
    label = pinpoint_label(frag)
    if not heading:
        return label
    if not label or _DESIGNATION.match(heading):
        return heading
    return "%s - %s" % (label, heading)


def _pin(con, path, root, frag):
    """The resolved provision as a Fragment: where it is, what it is called, and
    its own words -- `[]` for a fragment the presented body publishes no anchor
    for. One artifact read per citation-shaped query -- there is at most one
    pinned hit, and it is the query's answer."""
    art = catalog.load_artifact(catalog.data_root(con), path)
    body = text.anchor_text(art, frag)
    return {
        "uri": root + "#" + frag, "pinpoint": frag,
        "label": _pin_label(frag, text.provision_heading(art, frag)),
        "highlight": ([body[:SNIPPET_CHARS].rstrip() + "…"
                       if len(body) > SNIPPET_CHARS else body] if body else []),
    }


def merge_pinned(pinned, results, total, limit):
    """Lead the full-text `results` with the `pinned` (citation-resolved) hits:
    the resolved target is the answer to a citation-shaped query, so it goes
    first; any full-text row for the same document is dropped (the pinned hit
    is more precise) and `total` counts only the pinned documents full-text
    didn't already find. Returns the merged (results, total), capped at
    `limit`. Shared by the REST /search endpoint and the MCP search tool."""
    if not pinned:
        return results, total
    roots = {p["uri"] for p in pinned}
    kept = [r for r in results if r["uri"] not in roots]
    total += sum(p["uri"] not in {r["uri"] for r in results} for p in pinned)
    return (pinned + kept)[:limit], total
