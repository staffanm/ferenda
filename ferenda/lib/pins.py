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
pinned hit ranks and renders like any other search hit. A target the catalog
does not confirm is still reported, apart from the hits (`resolve_query`'s
`recognized`), so a client can tell a well-formed citation of a document we do
not hold from a query that is no citation at all.
"""

import re

from . import catalog, layout, resolve, text
from .pinpoint import acronym, pinpoint_label

# how much of the resolved provision's own text to carry as the hit's snippet --
# enough to recognise the rule, short enough to sit on two lines in the palette
SNIPPET_CHARS = 240


def resolved_results(con, q, source=None, kind=None):
    """The resolver's hits for `q` that the catalog confirms, each shaped like
    a SearchResult dict (uri, url, identifier, title, display, source, kind,
    inbound_count, pin, fragments). Empty when `q` reads as no known citation,
    or names only documents the corpus does not hold -- `resolve_query` tells
    those two apart; this is its first half, for the callers (the search
    endpoints' leading pin) that only place hits."""
    return resolve_query(con, q, source, kind)[0]


def resolve_query(con, q, source=None, kind=None):
    """`(results, recognized)` for a citation-shaped query. `results` is
    `resolved_results`' list. `recognized` names the citations the resolver
    read but the catalog does not hold -- `{"uri", "source"}` per citation:
    the document uri the citation mints and the source it would belong to.
    An optional `invalid: true` marks deterministic non-existence under the
    configured publication rules. Otherwise existence remains unknown.
    A client can then tell a well-formed citation of a document we do not
    have ("C-744/24": not decided yet, or not harvested) from a query that is
    no citation at all ("blahonga"), where both lists are empty.

    Kept apart from `results` rather than flagged inside it: a row there is a
    document a client can fetch and link to, and a minted identifier with no
    document behind it, sitting among the hits, is an invitation to cite what
    we cannot show. Missing documents use their root URI. Invalid provisions
    of held statutes retain their fragment to identify the failed citation.
    A source filter
    applies to both lists; a kind filter only to the held rows, since an
    unheld citation has no kind to check."""
    out, recognized = [], []
    targets = resolve.resolve(q)
    for hit in catalog.citation_targets(con, q):
        if hit["uri"] not in [target["uri"] for target in targets]:
            targets.append(hit)
    for hit in targets:
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
            recognized.append({"uri": root, "source": hit["source"]})
            if resolve.absent_is_invalid(root):
                recognized[-1]["invalid"] = True
            continue
        _uri, src, kind_, label, title, _path, descriptive, _url = row
        if kind and kind_ != kind:
            continue
        art = catalog.artifact_for(con, _path) if frag else None
        if frag:
            frag = _canonical_provision_fragment(art, frag)
        if frag and _missing_provision(art, frag):
            recognized.append({"uri": root + "#" + frag, "source": src,
                               "invalid": True})
            continue
        # the same reader-facing heading the page and full-text hits show (short
        # name + acronym where the artifact has them, else the title) -- stored
        # on the documents row at relate, so no artifact load per resolved hit
        display = catalog.document_display(con, root) or title
        pin = _pin(art, root, frag) if frag else None
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
    return out, recognized


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


def _canonical_provision_fragment(art, frag):
    """Keep continuous paragraph numbering while checking chapter membership.

    Avtalslagen stores P1 inside K1. K1P1 therefore reaches P1, but K2P1 must
    not reach it: naming the wrong chapter is an invalid provision citation.
    """
    match = re.fullmatch(r"(K[0-9]+[a-z]?)(P[0-9]+[a-z]?)(.*)", frag)
    if match:
        chapter = text.fragment_node(art, match[1])
        if chapter and text.fragment_node({"structure": [chapter]}, match[2]):
            return match[2] + match[3]
    return frag


def _missing_provision(art, frag):
    """Prove a missing Swedish provision against the presented statute tree.

    Empty/unstructured artifacts cannot prove absence. Chapter and paragraf
    checks use the shared SFS/agency anchor grammar, including chapterless
    statutes. Finer pinpoints need sibling anchors at that level before an
    absent child is conclusive; some producers do not number stycken/points.
    """
    if not re.fullmatch(r"(?:K[0-9]+[a-z]?)?(?:P[0-9]+[a-z]?)?(?:S[0-9]+)?(?:N[0-9]+)?", frag):
        return False
    nodes = list(text.body_id_nodes(art))
    ids = {node["id"] for node in nodes}
    if frag in ids or not any(node.get("type") == "paragraf" for node in nodes):
        return False
    prefix = ""
    for part in re.findall(r"[KPSN][0-9]+[a-z]?", frag):
        target = prefix + part
        if not any(re.match(re.escape(target) + r"(?:[PSN]|$)", id_) for id_ in ids):
            return part[0] in "KP" or any(
                re.match(re.escape(prefix + part[0]) + r"[0-9]", id_) for id_ in ids)
        prefix = target
    return False


def _pin(art, root, frag):
    """The resolved provision as a Fragment: where it is, what it is called, and
    its own words -- `[]` for a fragment the presented body publishes no anchor
    for. One artifact read per citation-shaped query -- there is at most one
    pinned hit, and it is the query's answer."""
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
