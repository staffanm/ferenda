"""Where a förordning gets its authority: the two ingress formulas that place it
under the lag it serves.

A förordning is subordinate legislation, but nothing in an SFS artifact says so
-- its ``rdf:type`` is ``KonsolideradGrundforfattning``, the same as a lag's, and
the register carries no instrument type. The relation to the parent act has to be
read from the text, where Swedish drafting states it in two fixed forms:

* the **bemyndigandeupplysning** -- "Denna förordning är meddelad med stöd av
  1. 1 kap. 8 § cybersäkerhetslagen (2025:1506) i fråga om 4 §, 2. 1 kap. 15 §
  första stycket samma lag i fråga om 38 § 1 och 2, …". Each punkt names an
  empowering provision of the lag *and* the provisions of this förordning issued
  under it, so the relation is provision-precise in both directions. This is the
  strong form, and the one the norm chain walks.

* the **kompletterar ingress** -- "Denna förordning innehåller kompletterande
  bestämmelser till säkerhetsskyddslagen (2018:585)". Document-level only, but it
  is what an older förordning (2021:955) states where it states anything. A *lag*
  states this form too, against an EU act: "Denna lag kompletterar
  Europaparlamentets och rådets förordning (EU) 2016/679" (dataskyddslagen) --
  the family of Swedish laws detailing a directly applicable EU förordning.

Neither is universal: of 8,179 förordningar, 654 carry the first and ~50 the
second. That is why they cannot decide *whether* a document is a förordning
(`labels.sfs_is_statute` does that, from the title, with full coverage) -- they
say which lag a förordning answers to, which the title cannot.

A punkt of the bemyndigandeupplysning is recognised structurally, not by parsing
its prose: it holds references to another act *and* references into this
document. The first are the delegation, the second what it authorises. Anything
naming only regeringsformen is a förordning issued under the government's own
residual power (8 kap. 7 § RF) -- a true fact, and one that says there is no
parent lag to point at, so it yields no edge.
"""

import re

from ..lib.catalog import BASE, strip_fragment
from ..lib.text import runs_text

# 8 kap. RF: the government's own norm-giving power. A förordning meddelad with
# stöd av *only* regeringsformen has no delegating lag above it.
REGERINGSFORMEN = BASE + "1974:152"

# only a förordning/kungörelse is *meddelad* -- a lag is enacted, never issued
# under a delegation, so the upplysning formula stays förordning-only
RE_UPPLYSNING = re.compile(
    r"[Dd]enna\s+(?:förordning|kungörelse)\s+(?:är|har)\s+meddelad")
# a lag states the kompletterar relation too: dataskyddslagen (2018:218) 1 kap.
# 1 § opens "Denna lag kompletterar Europaparlamentets och rådets förordning
# (EU) 2016/679" -- the GDPR shape, an EU förordning with directive-like leeway
# detailed by Swedish law. 16 of 300 sampled gällande lagar open this way, and
# with the förordning-only alternation the whole family was invisible to the
# norm chain (0 lagar carried the edge).
RE_KOMPLETTERAR = re.compile(
    r"[Dd]enna\s+(?:lag|balk|förordning|kungörelse)\s+innehåller\s+"
    r"(?:kompletterande|verkställighets)\w*\s+bestämmelser\s+till")
# the bare-verb form of the same statement ("Denna lag kompletterar ...")
RE_KOMPLETTERAR_VERB = re.compile(
    r"[Dd]enna\s+(?:lag|balk|förordning|kungörelse)\s+kompletterar\b")
# an EU act's full title cites the acts it repeals or amends ("förordning (EU)
# 2016/679 ... och om upphävande av direktiv 95/46/EG"), and the citation
# engine links that embedded act too. A reference whose preceding text ends in
# this tail is part of the cited act's own name, not a complemented act.
RE_TITLE_EMBED = re.compile(
    r"(?:upphävande|ändring)\s+av\s+(?:rådets\s+|kommissionens\s+)?"
    r"(?:direktiv|förordning|beslut|rambeslut)?\s*$")


def _links(node):
    """The reference runs of one node's text, as (uri, text)."""
    return [(run["uri"], run.get("text") or "")
            for run in node.get("text") or []
            if isinstance(run, dict) and run.get("uri")
            and run.get("predicate", "").endswith("references")]


def _kompletterar_links(node):
    """The act references of a kompletterar sentence, minus any embedded in a
    cited act's own title (RE_TITLE_EMBED): dataskyddslagen's ingress cites the
    GDPR by full name, and that name itself cites direktiv 95/46/EG."""
    out, preceding = [], ""
    for run in node.get("text") or []:
        if isinstance(run, dict):
            if (run.get("uri") and run.get("predicate", "").endswith("references")
                    and not RE_TITLE_EMBED.search(preceding)):
                out.append(run["uri"])
            preceding = ""
        else:
            preceding = run
    return out


def _walk(nodes, out=None):
    """Every node of a structure tree, parents before children."""
    if out is None:
        out = []
    for node in nodes or ():
        if isinstance(node, dict):
            out.append(node)
            _walk(node.get("children"), out)
    return out


def extract(structure, self_uri):
    """``{"bemyndigande": [...], "kompletterar": [...]}`` for one förordning.

    ``bemyndigande`` is one entry per delegation, ``{"lagrum": <uri into the
    empowering act>, "provisions": [<uris into this förordning>]}``; a punkt that
    names no provisions of its own (the whole förordning is issued under it)
    carries an empty list. ``kompletterar`` is the acts the ingress says this
    förordning completes, document uris, no fragment.

    Both are empty for a förordning that states neither formula and for one
    whose only authority is regeringsformen; ``bemyndigande`` is always empty
    for a lag, but a lag that says it complements an EU act carries the
    ``kompletterar`` edge. An empty result is the document being silent, never
    a failure to look."""
    nodes = _walk(structure)
    bemyndigande, kompletterar = [], []
    for node in nodes:
        text = runs_text(node.get("text") or [])
        if RE_KOMPLETTERAR.search(text) or RE_KOMPLETTERAR_VERB.search(text):
            kompletterar += [strip_fragment(uri)
                             for uri in _kompletterar_links(node)
                             if strip_fragment(uri)
                             not in (self_uri, REGERINGSFORMEN)]
        if not RE_UPPLYSNING.search(text):
            continue
        # the clause's delegations are its punkter -- this node's own links when
        # it states a single one inline, else the numbered children below it
        for punkt in [node] + _walk(node.get("children")):
            outward = [uri for uri, _ in _links(punkt)
                       if strip_fragment(uri)
                       not in (self_uri, REGERINGSFORMEN)]
            if not outward:
                continue
            inward = [uri for uri, _ in _links(punkt)
                      if strip_fragment(uri) == self_uri]
            bemyndigande.append({"lagrum": outward[0],
                                 "provisions": sorted(set(inward))})
    return {"bemyndigande": _dedupe(bemyndigande),
            "kompletterar": sorted(set(kompletterar))}


def _dedupe(entries):
    """One entry per empowering provision, its provision lists merged, ordered by
    the lagrum uri so two runs over an unchanged artifact agree."""
    merged = {}
    for entry in entries:
        prev = merged.setdefault(entry["lagrum"], set())
        prev.update(entry["provisions"])
    return [{"lagrum": lagrum, "provisions": sorted(provisions)}
            for lagrum, provisions in sorted(merged.items())]
