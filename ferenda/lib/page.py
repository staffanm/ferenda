"""The shared page kit: everything a document page is built out of, regardless
of which source produced the document.

A source owns *its own* page renderer (``<source>/render.py``, a
``render(art, site) -> str``, its ``Source.render`` field); this
module owns what they all stand on:

  * :class:`Site` -- the render context. The catalog connection plus the set of
    document URIs that actually exist, so a citation to a document we do not
    have renders as plain text rather than a broken link, together with the
    cross-document layers (commentary, guidance, remiss feedback, FK kommentar,
    verified graphics) a page shows but does not own.
  * the node walk -- ``render_node`` / ``render_runs``, keyed on each artifact
    node's ``type``, so the SFS structure tree and the DV body render through
    one generic walk. ``render_runs`` is the hottest loop in the system and
    stays %-format Python (rule:markup-in-templates).
  * the context rail -- :class:`Rail` / :class:`RailSection`, which turn a
    paragraph's inbound citations into the JSON island the client swaps as you
    scroll. That round-trip (case -> paragraph -> back to every case on that
    paragraph) is the signature lagen.nu feature. The per-kind margin builders
    the rail calls live in ``lib/margins.py``.
  * the page shell -- ``page`` / ``page_context``, the ``dl.meta`` block and the
    TOC collector, so every source's template extends one chrome.

What varies per source lives in that source's own renderer. Where the shared kit
itself has to vary -- how a citing document is named and pinpointed
(``CITER_STYLE``), which inbound group it files under (``INBOUND_GROUPS``) -- the
variation is a table keyed by the source name *as it appears in the data*, never
an import of source code (rule:lib-never-imports-vertical). ``lib/render.py``
(site assembly: frontpage, folkrätt landing, faceted browse, feeds) imports this
module; the dependency never runs the other way.
"""
import hashlib
import json
import re
import sqlite3
import textwrap
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from html import escape
from urllib.parse import quote, urlsplit

from markupsafe import Markup

from . import (
    annstore,
    catalog,
    compress,
    facets,
    facsimile,
    hierarki,
    history,
    layout,
    margins,
)
from .catalog import BASE
from .pinpoint import human_fragment
from .text import runs_text
from .tpl import ENV
from .util import basefile_slug, split_numalpha


@dataclass
class Site:
    con: sqlite3.Connection
    known: set[str]                     # document root uris present
    aliases: dict[str, str] = field(default_factory=dict)       # variant begrepp uri -> canonical concept
    # (law_uri, anchor) -> [(author, prose)]; anchor is None for the act-level preamble
    commentary: dict[tuple[str, str | None], list[tuple[str | None, list[dict]]]] = field(default_factory=dict)
    guidance: dict[str, list[dict]] = field(default_factory=dict)              # act uri -> [{label, href, note?}]
    article_guidance: dict[tuple[str, str], list[dict]] = field(default_factory=dict)  # (law_uri, anchor) -> [{label, href, note?}]
    remiss_feedback: dict[tuple[str, str], list[dict[str, str | float]]] = field(default_factory=dict)  # (forarbete_uri, avsnitt_id) -> [{organisation, sentiment, quote, source_url}]
    remiss_overall: dict[str, list[dict[str, str | float]]] = field(default_factory=dict)               # forarbete_uri -> [{organisation, sentiment, quote, source_url}]
    # (sfs_uri, anchor) -> [(prop_uri, prop_label, page, text)], newest prop
    # first; anchor is None for a law-level FK comment
    fk: dict[tuple[str, str | None],
             list[tuple[str, str | None, int | None, str]]] = \
        field(default_factory=dict)
    # (document uri, stable grafik key) -> {sfs, page, bbox?, alt} from a
    # verified .graphics entry
    # layer -- what the reading view needs to place the dropped graphic's crop
    graphics: dict[tuple[str, str], dict] = field(default_factory=dict)
    # uris whose repeal date has passed -- dropped from inbound-link panels (I3);
    # a future (not-yet-in-force) repeal date is *not* here, so it still shows
    expired: set[str] = field(default_factory=set)
    # doc uri -> [(anchor, concept uri, ladder anchor id)] -- the provisions
    # that carry a regleringshierarki row, for the one-line rail entry per
    # concept (O5). Built once per Site, scoped by target_uris like `fk`
    hierarki: dict[str, list[tuple[str, str, str]]] = \
        field(default_factory=dict)
    # föreskrift uri -> its unambiguous upward chain, for the per-chapter
    # "Dessa föreskrifter fyller ut ..." rail line (PRD §8); absent where the
    # chain is missing or ambiguous (several delegation pins -- phase-3 residue)
    fyller_ut: dict[str, dict] = field(default_factory=dict)
    # the document-level norm chain, for the "Normkedja" metadata row on every
    # document on a chain: doc -> [(parent doc, parent level)], and
    # doc -> {child level: count} for the downward summary
    chain_up: dict[str, list[tuple[str, int]]] = field(default_factory=dict)
    chain_down: dict[str, dict[int, list[str]]] = field(default_factory=dict)
    # one-document memo for `catalog.caselaw_anchored` -- the statute-wide
    # case assignment is computed once per rendered statute (render_sfs primes
    # it with the consolidation's live anchor set), then read per anchor as
    # its rail panel is built; holds only the current document
    caselaw_memo: dict[str, dict[str, list[tuple[tuple, set]]]] = \
        field(default_factory=dict)
    # {document uri: how many places in the corpus cite it} -- the authority
    # signal a case-law rail orders by (D4), filled in per rail for the citers
    # actually on the page and memoized across the pages a worker renders.
    # Not preloaded: the whole-corpus map is a 13.5M-row pass (~9 s, 209k
    # entries), which an `only`-scoped one-page render must not pay.
    inbound_counts: dict[str, int] = field(default_factory=dict)
    # the build date, as the ISO string `temporal_fields` writes. One per render
    # pass, beside the `expired` set derived from the same moment -- a paragraf
    # node must not read the clock, and two nodes of one page must not disagree
    # about which variant is in force. Defaulted to today rather than "", which
    # every dated ikraftträdande would sort after, marking the whole corpus
    # pending: a Site built by hand (a test) must not render a wrong page.
    today: str = field(default_factory=lambda: date.today().isoformat())

    @classmethod
    def from_catalog(cls, con, target_uris=None):
        """`target_uris` scopes the expensive cross-content indexes to the pages
        actually in the plan (the `only`-scoped generate): the FK rail rows are
        queried per host statute and the remisser tree is walked only when a
        förarbete page is among the targets (remiss analyses render nowhere
        else). Everything these indexes feed is per-host (site_cross_digests,
        the rails), so a scoped Site renders its target pages -- and signs
        them -- byte-identically to the full build. None = the whole corpus."""
        commentary, guidance, article_guidance = _kommentar_indexes(con)
        if target_uris is None:
            remiss_feedback, remiss_overall = _remiss_indexes()
        else:
            sources = {s for (s,) in con.execute(
                "SELECT DISTINCT source FROM documents WHERE uri IN (%s)"
                % ",".join("?" * len(target_uris)), list(target_uris))
            } if target_uris else set()
            remiss_feedback, remiss_overall = (
                _remiss_indexes() if "forarbete" in sources else ({}, {}))
        return cls(con, {u for (u,) in con.execute("SELECT uri FROM documents")},
                   catalog.concept_aliases(con),
                   commentary, guidance, article_guidance,
                   remiss_feedback, remiss_overall, _fk_index(con, target_uris),
                   _graphics_index(),
                   catalog.expired_uris(con, date.today().isoformat()),
                   hierarki=hierarki.provision_index(con, target_uris),
                   fyller_ut=hierarki.fyller_ut_index(con, target_uris),
                   chain_up=margins._chain_up_index(con), chain_down=margins._chain_down_index(con),
                   today=date.today().isoformat())

    def load_inbound_counts(self, uris):
        """Fill `inbound_counts` for `uris` that are not memoized yet. A uri
        nothing cites is recorded as 0 rather than left absent, so the same page
        (or the next one this worker renders) does not re-query it."""
        want = [u for u in dict.fromkeys(uris) if u not in self.inbound_counts]
        if not want:
            return
        found = catalog.inbound_counts_for(self.con, want)
        self.inbound_counts.update({u: found.get(u, 0) for u in want})

    def resolve(self, uri):
        """Fold a begrepp link baked into an artifact onto its canonical concept
        uri (inflected/variant forms merged at relate time); other uris (and a
        non-begrepp uri) pass through unchanged."""
        base, sep, frag = uri.partition("#")
        return self.aliases.get(base, base) + sep + frag

    def has(self, uri):
        return catalog.strip_fragment(uri) in self.known


def _kommentar_indexes(con):
    """Build the three rail indexes the wiki value-add feeds in **one pass** over
    the kommentar artifacts (each is read + parsed once, not three times):

      * ``commentary`` -- {(law_uri, anchor): [(author, [prose blocks])]}, the
        content the rail shows side-by-side with the paragraph. Commentary is an
        annotation layer (no page of its own); each `== N kap M § ==` section maps
        onto the host node's anchor (`K{N}P{M}`, an EU `5.2`, …). Leading blocks
        before the first section are commentary on the act as a whole, keyed
        (law, None) and shown in the rail by default.
      * ``guidance`` -- {act_uri: [{label, href, note?}]}, the document-level
        `## Externa länkar` block shown at the top of the act (PRD Step 2).
      * ``article_guidance`` -- {(law_uri, anchor): [{label, href, note?}]}, the
        external links attached to a single node's rail (PRD Steps 3-4), from two
        render-only sources keyed identically: the hand-curated per-section
        `## Externa länkar` block in the artifact body, and the AI guidance
        linker's `.ann` layer (`lagen kommentar ai-annotate`, lib.annstore), kept
        separate from the hand-edited markdown but surfaced in the same rail.

    All three are render-only: external resources live outside the corpus, so they
    carry no inbound edge."""
    commentary, guidance, article_guidance = {}, {}, {}
    root = catalog.data_root(con)
    for (path,) in con.execute(
            "SELECT path FROM documents WHERE source = 'kommentar' AND path <> ''"):
        path = root / path
        art = compress.read_json(path)
        # wiki/parse stamps `annotates` (the host act uri) on every kommentar
        # artifact, so a missing key is a corrupt artifact, not an opt-out: fail
        # fast rather than silently drop the whole commentary from every statute
        # rail (rule:fail-fast).
        law = art["annotates"]
        author, body = art.get("author"), art.get("body", [])
        # leading blocks before the first section heading are commentary on the
        # act as a whole -- keyed (law, None), shown in the rail by default
        preamble = []
        for b in body:
            if b.get("type") == "sektion":
                break
            preamble.append(b)
        if preamble:
            commentary.setdefault((law, None), []).append((author, preamble))
        for b in body:
            if b.get("type") != "sektion":
                continue
            if b.get("children"):
                commentary.setdefault((law, b["id"]), []).append((author, b["children"]))
            if b.get("guidance"):        # per-section `## Externa länkar` (Step 3)
                article_guidance.setdefault((law, b["id"]), []).extend(b["guidance"])
        if art.get("guidance"):          # document-level `## Externa länkar` (Step 2)
            guidance.setdefault(law, []).extend(art["guidance"])
        # the AI linker layer (Step 4), in the curated store (lib.annstore) --
        # keyed by the kommentar's identity recovered from its minted uri
        # (BASE + "kommentar/" + basefile, wiki/parse), so it resolves
        # regardless of where the catalog's data_root put the artifact. The
        # prefix is that minted invariant: assert it rather than let a stray
        # uri map to a garbage path whose miss silently drops the layer from
        # the rail (rule:fail-fast)
        loc = catalog.local(art["uri"])
        assert loc.startswith("kommentar/"), \
            "kommentar row carries a non-kommentar uri: %s" % art["uri"]
        ann = annstore.path("kommentar", loc[len("kommentar/"):])
        if ann.exists():
            links = json.loads(ann.read_bytes()).get("guidanceLinks", {})
            for anchor, items in links.items():
                article_guidance.setdefault((law, anchor), []).extend(items)
    return commentary, guidance, article_guidance


def _fk_index(con, uris=None):
    """The per-paragraf författningskommentar rail index, from the catalog
    layer forarbete.fk resolved at relate time: {(sfs_uri, anchor):
    [(prop_uri, prop_label, page, text)]}, newest proposition first (the row
    order of `fk_kommentar_all`); anchor None keys a law-level comment.
    `uris` scopes the read to those host statutes (Site.from_catalog's
    targeted build)."""
    fk = {}
    for sfs_uri, anchor, prop_uri, label, _date, page, text in \
            catalog.fk_kommentar_all(con, uris):
        fk.setdefault((sfs_uri, anchor or None), []).append(
            (prop_uri, label, page, text))
    return fk


def _remiss_item(svar, scored):
    """One rail feedback item from an answer artifact `svar` (read as a raw dict,
    not the vertical's model -- lib stays source-agnostic) and a scored `.ann`
    object (the `overall` stance or a segment): the answering organisation, its
    sentiment/quote, and a `source_url` "Källa" link to that organisation's own
    answer PDF so a reader can open the actual remissvar."""
    return {"organisation": svar["organisation"], "sentiment": scored["sentiment"],
            "quote": scored["quote"], "source_url": svar["source_url"]}


def _remiss_indexes():
    """Build the two remiss rail indexes in **one pass** over the remisser artifact
    tree. Unlike the kommentar indexes this reads the *filesystem*, not the
    catalog: the remisser corpus is deliberately never `relate`d (no page, no
    catalog rows, no inbound edge), so its analyzed answers are found by walking
    the remisser artifact tree (``layout.artifacts``, one `<case-slug>/<org-slug>`
    artifact per answer) and picking up each answer's mirrored ``.ann`` layer from
    the curated store (lib.annstore; the `ai-analyze` sentiment layer). An answer
    with no ``.ann`` yet is simply unanalyzed -- skipped, no error; a *malformed*
    ``.ann`` is a broken environment invariant and its `json.JSONDecodeError`
    propagates.

      * ``remiss_feedback`` -- {(forarbete_uri, avsnitt_id): [item, …]}, one entry
        per analyzed segment, keyed on the *referred förarbete's* own minted uri
        plus the section id the segment cites, so that förarbete's section rail
        can show what each answer said about that section.
      * ``remiss_overall`` -- {forarbete_uri: [item, …]}, one entry per answer's
        document-level `overall` stance, for the förarbete's document-level panel."""
    remiss_feedback, remiss_overall = {}, {}
    host_uri = {}          # (typ, fa_basefile) -> referred förarbete's minted uri
    for path in layout.artifacts("remisser"):
        ann = annstore.for_artifact(path)
        if not ann.exists():
            continue                       # answer not analyzed yet -- nothing to show
        svar = compress.read_json(path)
        # v1 maps only the first cross-ref, matching ai_analyze.analyze (a remiss
        # almost always sends out exactly one SOU/Ds); cache the referred
        # förarbete's uri so N answers to the same document reopen it once.
        ref = svar["remitterat"][0]
        typ, fa_basefile = ref["typ"], ref["basefile"]
        key = (typ, fa_basefile)
        if key not in host_uri:
            # resolve_basefile settles the spelling against the tree: a
            # promemoria's identity is a diarienummer the publisher renders with
            # either case, or the landing slug it carries as an alternate
            fa_path = layout.artifact("forarbete", layout.resolve_basefile(
                "forarbete", "%s/%s" % (typ, basefile_slug(fa_basefile)),
                *(["%s/%s" % (typ, ref["slug"])] if ref.get("slug") else [])))
            host_uri[key] = compress.read_json(fa_path)["uri"]
        fa_uri = host_uri[key]

        layer = json.loads(ann.read_text())      # malformed .ann -> JSONDecodeError
        remiss_overall.setdefault(fa_uri, []).append(
            _remiss_item(svar, layer["overall"]))
        for seg in layer["segments"]:
            remiss_feedback.setdefault((fa_uri, seg["forarbete_id"]), []).append(
                _remiss_item(svar, seg))
    return remiss_feedback, remiss_overall


def _graphics_index():
    """{(document_uri, gap_key): entry} of publishable graphic crops.

    The host URI is explicit layer metadata, so this horizontal reader neither
    imports nor branches on an SFS vertical. `annstore.publishable` owns which
    entries qualify, so this reader and the crop endpoint cannot disagree: a
    model's guess stays out of the public render until the entry or the whole
    layer is verified, while a mechanically derived layer needs no such review.
    """
    index = {}
    for path, meta, gap_key, entry in annstore.layer_entries(".graphics"):
        if not annstore.publishable(meta, entry):
            continue
        uri = meta.get("uri")
        assert uri, "%s: publishable graphics layer has no meta.uri" % path
        index[(uri, gap_key)] = entry
    return index


def site_cross_digests(site):
    """{host_uri: digest} of every piece of CROSS-document content the Site
    renders onto a host's page: kommentar prose + its `.ann` guidance layer
    (``commentary``/``guidance``/``article_guidance``), remiss `.ann` analyses
    (``remiss_feedback``/``remiss_overall``) and the `.corr` correspondence rows
    (both the old-law "motsvaras numera av" margin and the new-law
    corresponding-cases margin). A page's own freshness signature covers only
    its own artifact + sidecars, and the dependency digest only its link *sets*
    -- so without this fold, editing any of these layers never re-renders the
    host page they appear on (rule:artifact-is-truth: the artifact edit must
    reach every page it renders on, not wait for --force). The caller folds the
    digest into each page's dependency digest; a host absent here contributes
    nothing (and a layer's *removal* changes the fold, invalidating the page)."""
    acc = {}

    def feed(host, index, key, value):
        # one canonical line per index entry; sorted at digest time so dict
        # iteration order never enters the fingerprint
        acc.setdefault(host, []).append(
            json.dumps([index, key, value], ensure_ascii=False, sort_keys=True))

    for (law, anchor), v in site.commentary.items():
        feed(law, "commentary", anchor, v)
    for law, v in site.guidance.items():
        feed(law, "guidance", None, v)
    for (law, anchor), v in site.article_guidance.items():
        feed(law, "article_guidance", anchor, v)
    for (fa_uri, avsnitt), v in site.remiss_feedback.items():
        feed(fa_uri, "remiss_feedback", avsnitt, v)
    for fa_uri, v in site.remiss_overall.items():
        feed(fa_uri, "remiss_overall", None, v)
    # a .graphics entry renders on its own statute's page (host = the sfs uri),
    # but it lives outside the artifact, so fold it in or a layer edit (a newly
    # verified crop) never reaches the page it appears on
    for (sfs_uri, gap_key), v in site.graphics.items():
        feed(sfs_uri, "graphics", gap_key, v)
    # a correspondence row touches its two endpoint pages -- and, because the
    # new-law margin walks the chain transitively (corresponding_cases_margin:
    # 2025:400 -> 2001:453 -> 1980:620), every page whose law is a transitive
    # *successor* of the row's new side: editing the 1980:620 layer must
    # re-render the 2025:400 page that now shows its case law
    # a regleringshierarki row renders on its provision's page (the rail
    # line) and on its concept's page (the ladder); the fyller-ut line renders
    # on the föreskrifter standing on a delegation clause -- all read from
    # derived tables the links-only dependency digest never sees
    for row in site.con.execute(
            "SELECT concept, doc_uri, anchor, also, level, kind, role, label, "
            "chain_root, via, source, stated, upphavd, via_amended "
            "FROM regleringshierarki"):
        feed(row[1], "hierarki", row[2], list(row))
        feed(row[0], "hierarki", row[2], list(row))
    for fs, l, lp, u, upin in site.con.execute(
            "SELECT nc.lower_uri, de.lower_uri, de.lower_pin, de.upper_uri, "
            "de.upper_pin FROM delegation_edge de JOIN norm_chain nc "
            "ON nc.upper_uri = de.lower_uri AND nc.upper_pin = de.lower_pin "
            "WHERE nc.lower_level = 3"):
        feed(fs, "fyller_ut", lp, [l, lp, u, upin])
    # the Normkedja metadata row renders the doc-level chain on both endpoint
    # pages, so an edge change must re-render both
    for lower, upper in site.con.execute(
            "SELECT DISTINCT lower_uri, upper_uri FROM norm_chain "
            "UNION SELECT DISTINCT lower_uri, upper_uri FROM delegation_edge"):
        feed(lower, "normkedja", upper, 1)
        feed(upper, "normkedja", lower, 1)
    # an amending act's `.ann` maps its recitals onto the *amended* act's
    # article numbers (eurlex ai-annotate over an unpacked amending act), and
    # the renderer draws those links on the amended act's page -- and on every
    # /konsolidering/ lydelse page of it, which are extra pages with the same
    # per-uri freshness -- so authoring or editing one must re-render them
    # all. The amends edge is the catalog's rpubl:andrar, which comes off the
    # amending act's CELLAR notice; the renderer joins the consolidation's
    # own FAM.COMP register, and the two can disagree in principle -- an
    # amending act FAM.COMP names but whose notice carries no amends relation
    # escapes this fold (accepted: the notice relation is what the catalog
    # holds, and reading every eurlex artifact's register here would cost the
    # whole tree per generate). The layer bytes come from the store.
    amended = {}
    for from_uri, to_uri in site.con.execute(
            "SELECT DISTINCT from_uri, to_uri FROM links "
            "WHERE predicate = 'rpubl:andrar'"):
        amended.setdefault(from_uri, []).append(to_uri)
    for p in annstore.tree("eurlex").rglob("*.ann"):
        hosts = amended.get(BASE + "celex/" + p.stem.replace("_", "/"))
        if hosts:
            digest = hashlib.sha256(p.read_bytes()).hexdigest()
            for host in hosts:
                feed(host, "amending_ann", p.stem, digest)
                celex = host.rsplit("/celex/", 1)[-1]
                for _v, vuri in history.versions("eurlex", celex):
                    feed(vuri, "amending_ann", p.stem, digest)
    corr_rows = site.con.execute(
        "SELECT new_uri, old_uri, relation, scope, prop_uri, ikrafttrader "
        "FROM correspondence").fetchall()
    successors = {}     # old law base -> {new law bases}
    for row in corr_rows:
        successors.setdefault(catalog.strip_fragment(row[1]), set()).add(
            catalog.strip_fragment(row[0]))
    for row in corr_rows:
        hosts, frontier = set(), {catalog.strip_fragment(row[0])}
        while frontier:
            hosts |= frontier
            frontier = {s for base in frontier
                        for s in successors.get(base, ())} - hosts
        hosts.add(catalog.strip_fragment(row[1]))
        for host in hosts:
            feed(host, "corr", row[1], list(row))
    return {host: hashlib.sha256("\x1e".join(sorted(lines)).encode()).hexdigest()
            for host, lines in acc.items()}


# --------------------------------------------------------------------------
# uri -> local href / output path
# --------------------------------------------------------------------------

def _split_uri(uri):
    base, _, frag = uri.partition("#")
    return catalog.local(base), frag


# the uri -> output-path / public-route rule now lives in lib.layout (the single
# home for on-disk and on-web location rules)
doc_relpath = layout.page_relpath


# the namespaces keyed by a publisher's printed number (celex/, coe/, …): a
# uri in one may name a document the corpus does not hold, and the publisher
# then has a page to link out to
_EXT_PREFIXES = tuple(BASE + ns + "/" for ns in sorted(layout.EXT_NAMESPACES))
CELEX = BASE + "celex/"
COE = BASE + "coe/"
EURLEX = "https://eur-lex.europa.eu/legal-content/SV/TXT/?uri=CELEX:%s"
COE_TREATY = ("https://www.coe.int/en/web/conventions/full-list2"
              "?module=treaty-detail&treatynum=%s")


def _is_external(uri):
    """Whether a URI is in one of the publisher-number namespaces (EU acts by
    CELEX, treaties by CETS/UNTS number, …). The site does not necessarily
    hold the document; when it doesn't, the link goes to the publisher rather
    than becoming a dead local link."""
    return uri.startswith(_EXT_PREFIXES)


def href(uri):
    if not uri.startswith(BASE):
        return uri  # already-absolute external
    _, frag = _split_uri(uri)
    return layout.page_url(uri) + ("#" + frag if frag else "")


def _external_href(uri):
    """Where a publisher-number reference we don't host resolves -- EUR-Lex
    for a CELEX (the EU act on the official site), else the uri itself."""
    if uri.startswith(CELEX):
        return EURLEX % catalog.local(uri)[len("celex/"):].split("#")[0]
    if uri.startswith(COE):
        return COE_TREATY % catalog.local(uri)[len("coe/"):].split("#")[0]
    return uri


# a minted fragment id decomposes into K(ap)/§/mom/stycke/punkt/mening segments
# (the FRAGMENT_LETTERS scheme); render it the way a lawyer would pinpoint it
# `human_fragment` is imported from lib/pinpoint above; the transform moved
# there so the serving layer (lib/pins) can name a provision without importing
# the renderer. Used below by the citer/rail labelling.


def describe_citer(from_uri, anchor, label, title, source):
    """Human label for an inbound entry: the citing document's name plus the
    pinpoint where the citation sits -- "Skollag (2010:800) 2 kap. 16 § 5 st"
    for a statute, the referat/identifier for a case/förarbete. Commentary
    shows its author (the paragraph is the one being read, so no pinpoint)."""
    if source == "kommentar":
        # the anchor is the commented paragraph; showing it makes the many
        # sections of one commentary distinct (and useful) on a concept page
        pin = human_fragment(anchor)
        if pin:
            return "Kommentar " + pin
        return "Kommentar" + (" – %s" % title if title and title != "Kommentar"
                              else "")
    name = (title or label) if source == "sfs" else label
    pin = human_fragment(anchor) if source in ("sfs", "forarbete") else ""
    return name + (" " + pin if pin else "")


# The inbound rail's accordion rows, in display order. Ranking lives in
# RAIL_SECTION_ORDER, keyed by these same slugs.
INBOUND_ORDER = ("sfs", "forarbete", "foreskrift", "dv", "avg", "rs", "guidance",
                 "lawreview", "hudoc", "icc", "icj", "eu-caselaw", "eu-forslag",
                 "eurlex", "coe", "icrc", "untc", "begrepp")

# What a group is called where that is *not* simply what the source is called
# (`facets.SOURCE_LABELS`). Each entry here is a deliberate rename away from the
# source label: the statute group keeps lagen.nu's long-standing "Lagrumshänvisningar
# hit", which says the direction out loud; the two treaty/act groups name what
# cites rather than the corpus it comes from; the eurlex group and the two
# pseudo-sources it splits into (INBOUND_KIND_GROUPS) carry names no source
# label covers; and the tidskriftsartikel group names the citing document --
# "Artiklar" -- rather than the corpus it is mined from (the source label
# "Tidskriftsartiklar"). Every group not listed here takes the source's own
# name unchanged.
_INBOUND_LABEL = {"sfs": "Lagrumshänvisningar hit",
                  "eurlex": "EU-rätt",
                  "icrc": "Humanitärrättsliga fördrag",
                  "eu-caselaw": "EU-domstolens praxis",
                  "eu-forslag": "Generaladvokatens förslag till avgörande",
                  "lawreview": "Artiklar"}

INBOUND_GROUPS = [(slug, _INBOUND_LABEL.get(slug) or facets.SOURCE_LABELS[slug])
                  for slug in INBOUND_ORDER]

# One source is normally one group, but the eurlex corpus holds two kinds of
# citing document that a reader keeps apart: the acts, and the Court's own case
# law. Folded together they read as one undifferentiated "EU-rätt" pile -- the
# VAT directive is cited by 581 judgments and 232 generaladvokat opinions
# against 138 acts, and it is the judgments a reader opening artikel 132 came
# for. So the eurlex group splits by the catalogued document *kind*, and the
# case-law half inherits `eu-caselaw`'s rank, above the citation graph. Keying
# on catalog metadata (a kind value in the data) is what keeps this
# source-agnostic -- lib imports no vertical (rule:lib-never-imports-vertical).
# A generaladvokat's förslag is not a ruling, so it stays its own group rather
# than lending judgment-strength authority to an opinion.
INBOUND_KIND_GROUPS = {("eurlex", "judgment"): "eu-caselaw",
                       ("eurlex", "opinion"): "eu-forslag"}

# the groups whose members are case law, ordered newest-first below
CASELAW_GROUPS = frozenset(INBOUND_KIND_GROUPS.values())


def inbound_group(source, kind):
    """The rail accordion slug a citing document belongs to -- its source,
    except where the source's kinds split (see `INBOUND_KIND_GROUPS`)."""
    return INBOUND_KIND_GROUPS.get((source, kind), source)

# förarbete precedence in the inbound panel and the "Förarbeten" section:
# propositions first, then SOU, Ds/PM, lagrådsremiss, betänkanden -- each block
# then ordered oldest-first (older preparatory work is the more foundational).
FORARB_KIND_PRIORITY = {"prop": 0, "sou": 1, "ds": 2, "pm": 2, "lr": 3, "bet": 4}

PINPOINT_CAP = 5   # source pinpoints listed on a collapsed citer line before "m.fl."
PANEL_CAP = 20     # citing docs shown per group before the "+N fler" disclosure


def forarb_sort_key(kind, date, label):
    """Ordering of a förarbete in the panel and the preparatory-works section:
    by kind precedence, then oldest-first (an undated entry sorts last), then
    label. One key so the two listings never disagree."""
    return (FORARB_KIND_PRIORITY.get(kind, 9), date or "9999-99-99", label)


def forarbete_pinpoint(anchor, page=None):
    """A förarbete node id -> (human pinpoint, the anchor to link): "a14.3" ->
    "avsnitt 14.3", "sid39" -> "s. 39". A "-N" clash suffix on the avsnitt id is
    dropped.

    A generated "sec7" -- a heading that carries no section number, so there is
    no avsnitt to name -- falls back to the printed page the citation sits on,
    which is how a reader would cite it anyway. The page is a real anchor of its
    own: `render_forarbete` emits `id="sid{N}"` at every page break, so the
    link goes to `#sid39` rather than to the unnameable heading (S4). Without a
    page there is nothing citable to say, so the line names the document
    alone."""
    if anchor.startswith("sid"):
        return "s. " + anchor[3:], anchor
    if re.match(r"a\d", anchor):
        return "avsnitt " + re.sub(r"-\d+$", "", anchor[1:]), anchor
    return ("s. %d" % page, "sid%d" % page) if page else ("", anchor)


def _paragraf_pinpoint(anchor, _page=None):
    return human_fragment(anchor), anchor


def _whole_document_pinpoint(anchor, _page=None):
    return "", anchor


def _descriptive_name(_kind, label, title, descriptive):
    return descriptive or title or label


def _forarbete_name(kind, label, title, _descriptive):
    if kind == "lr":
        return "Lagrådsremiss: %s" % title if title and title != label \
            else "Lagrådsremiss"
    return "%s: %s" % (label, title) if title and title != label else label


@dataclass(frozen=True)
class _CiterStyle:
    """How one source's documents are written on an inbound citer line: what
    names the document, what names a spot inside it, and how the two join.
    Everything `_citer_line` varies by source, as data (`CITER_STYLE`) --
    lib may not import a source, so a source's idiosyncrasy lives here as a
    table entry rather than as a name check in the middle of the renderer."""
    # (anchor, printed page or None) -> (human pinpoint, the anchor to link)
    pinpoint: Callable[[str, int | None], tuple[str, str]]
    # (kind, label, title, descriptive) -> the document's display name
    name: Callable[[str, str, str, str | None], str]
    # between the name and a lone pinpoint: a statute pinpoint completes the
    # citation ("… 22 § 2 st"), a förarbete locator is an aside on where in the
    # document it sits (", avsnitt 6.7")
    sep: str
    # whether several pinpoints share one leading category word, written once
    # ("avsnitt 3, 5 och 7") instead of repeated on each
    shared_word: bool
    # whether the line links the citer's own publisher page (the row's
    # `source_url`) instead of a page on this site -- True only for a source
    # whose documents the site does not render (a tidskriftsartikel)
    external: bool = False


# Most sources cite whole-document, under the short *descriptive* citing form
# `labels.descriptive_label` stamped in the catalog: "räntelagen" not "Räntelag
# (1975:635)", "JO 2024 s. 246" not the decision's long title (I1). An older
# catalog with no `descriptive` column falls back to the full title.
DEFAULT_CITER_STYLE = _CiterStyle(_whole_document_pinpoint, _descriptive_name,
                                 " ", False)

# Sources whose documents are divided into chapters and paragrafer and mint the
# SFS fragment syntax (`K2P3`, `P5S1`) for them, so a citing spot inside one is
# nameable as "2 kap. 3 §". Föreskrifter are built that way by design
# (foreskrift/structure) -- the statutory layer and the agency layer under it
# pinpoint identically, and a föreskrift row that named no place was throwing
# away an anchor the catalog already held.
_PARAGRAF_STYLE = _CiterStyle(_paragraf_pinpoint, _descriptive_name, " ", False)

# A förarbete is pinpointed by avsnitt or printed page, and named by its number
# carrying its full title ("Prop. 2025/26:116: En ny funktion …") -- a
# lagrådsremiss by title alone ("Lagrådsremiss: …"), it having no number.
_FORARBETE_STYLE = _CiterStyle(forarbete_pinpoint, _forarbete_name, ", ", True)

# A tidskriftsartikel is cited by its title, completed by its author and its
# minimal citation ("En leverantörs … Systembolaget (Rickard Bergflo,
# JP 2009 s. 37)"): the descriptive column carries the author
# (`labels._lawreview`), the label the short_id. An article without an author
# carries the citation itself in that column, and the line shows the
# completion bare ("Title (JP 2009 s. 37)").
def _lawreview_name(kind, label, title, descriptive):
    if not title or title == label:
        return label
    if descriptive and descriptive != label:
        return "%s (%s, %s)" % (title, descriptive, label)
    return "%s (%s)" % (title, label)


_LAWREVIEW_STYLE = _CiterStyle(_whole_document_pinpoint, _lawreview_name, " ", False,
                              external=True)

CITER_STYLE = {"sfs": _PARAGRAF_STYLE, "foreskrift": _PARAGRAF_STYLE,
               "forarbete": _FORARBETE_STYLE, "lawreview": _LAWREVIEW_STYLE}


def _citer_style(source):
    return CITER_STYLE.get(source, DEFAULT_CITER_STYLE)


def _citer_pinpoint(source, anchor, page=None):
    """The human pinpoint for a citing document's source anchor, with the anchor
    the pinpoint should link to: an avsnitt/page for a förarbete, a chapter/§ for
    a statute or a föreskrift; other sources cite whole-doc."""
    if not anchor:
        return "", anchor
    return _citer_style(source).pinpoint(anchor, page)


def citer_name(source, kind, label, title, descriptive=None):
    """The preferred display name for a citing/preparatory document in an inbound
    panel -- see the `CITER_STYLE` entries for what each source is named by."""
    return _citer_style(source).name(kind, label, title, descriptive)


def swedish_join(parts):
    """["a","b","c"] -> "a, b och c" (the last item joined with "och")."""
    if len(parts) <= 1:
        return "".join(parts)
    return ", ".join(parts[:-1]) + " och " + parts[-1]


# Sources whose descriptive citing form is a bare designation that names nothing
# -- "MCFFS 2026:8" tells a reader neither the issuing agency's subject nor the
# rule, and "8-140522-2026" is a diarienummer and nothing else. For those, the
# document's own title rides along as a subtitle, the way `_bemyndigande_margin`
# already writes it, so the panel says what the citer *is* rather than only what
# it is called. A source whose citing form is itself meaningful ("räntelagen",
# "NJA 2023 s. 560") stays bare: repeating the long title behind a recognised
# citation is noise.
SUBTITLED_SOURCES = ("foreskrift", "rs")


def _citer_subtitle(source, display, title):
    """The `<span class="prov">` naming a citer whose designation does not, for
    the sources where the designation alone is opaque. Empty when the source
    reads fine bare, or when the title *is* the designation (an unparsed
    föreskrift whose title fell back to its number -- repeating it would say
    the same thing twice)."""
    if source not in SUBTITLED_SOURCES or not title or title == display:
        return ""
    return ' <span class="prov">%s</span>' % escape(" ".join(title.split()))


def _citer_line(row):
    """One collapsed "<li>" for a citing document: its full-title name (linking
    to the document) followed by up to PINPOINT_CAP distinct source pinpoints,
    then " m.fl." if more. Förarbete pinpoints share a category word ("avsnitt
    3, 5 och 7" -- written once, each number linking its own anchor); other
    sources' pinpoints are each rendered whole as a single link.

    A citer with exactly one pinpoint is written as one link naming the place it
    cites from -- "Förordning med instruktion för Statens jordbruksverk 22 § 2
    st" -> `/2009:1464#P22S2` -- not a link to the regulation beside a link to
    the stycke (S3). Two adjacent links to the same document offer the reader a
    choice they have no basis to make, and the pinpoint is the better landing."""
    from_uri, label, title, source, kind, _date, anchors, descriptive, source_url = row
    style = _citer_style(source)
    display = style.name(kind, label, title, descriptive)
    subtitle = _citer_subtitle(source, display, title)
    # an external-style citer (a tidskriftsartikel) has no page on this site:
    # its line links to the journal's own url for the article (the row's
    # `source_url`, the catalog's Källa). Every other source's `source_url`
    # is the publisher page its *local* page already names, so only the
    # style's say-so may divert the line off-site.
    if style.external:
        # both journals record a source_url for every article; a row without
        # one is a broken artifact, not a case to fall back from
        assert source_url, "external citer %s carries no source_url" % from_uri
        url = source_url
    else:
        url = href(from_uri)
    name = '<a href="%s">%s</a>' % (escape(url), escape(display))
    pins, seen = [], set()
    for entry in (anchors.split(",") if anchors else []):
        anchor, _, page = entry.rpartition("@")   # "sec17@39"; page may be empty
        pin, target = _citer_pinpoint(source, anchor,
                                     int(page) if page else None)
        if pin and pin not in seen:        # dedupe on the human pinpoint
            seen.add(pin)
            pins.append((pin, target))
    if not pins:
        return "<li>%s%s</li>" % (name, subtitle)
    pins.sort(key=lambda p: split_numalpha(p[0]))
    shown, overflow = pins[:PINPOINT_CAP], len(pins) > PINPOINT_CAP

    def link(anchor, text):
        return '<a href="%s">%s</a>' % (
            escape(href(from_uri + "#" + anchor)), escape(text))

    if len(pins) == 1:
        return "<li>%s%s</li>" % (
            link(pins[0][1], display + style.sep + pins[0][0]), subtitle)
    words = {pin.split(" ")[0] for pin, _ in shown}
    if style.shared_word and len(words) == 1 and " " in shown[0][0]:
        word = escape(shown[0][0].split(" ", 1)[0])       # "avsnitt" / "s."
        body = word + " " + swedish_join(
            [link(a, pin.split(" ", 1)[1]) for pin, a in shown])
    else:
        body = swedish_join([link(a, pin) for pin, a in shown])
    return "<li>%s, %s%s%s</li>" % (name, body, " m.fl." if overflow else "",
                                    subtitle)


# --------------------------------------------------------------------------
# table of contents (a sticky, scrollspy-driven outline of a document)
# --------------------------------------------------------------------------

class Toc:
    """Collects a document's headings as it is rendered, so the body's anchor
    ids and the TOC's links agree by construction. A heading without a node id
    (DV/förarbete) is given a generated, stable-per-page anchor."""

    def __init__(self):
        self.entries = []                # (anchor, text, level)
        self._n = 0
        # how much deeper the headings collected right now sit than their own
        # `level` says. A DV case is a stack of court instances that each
        # re-use the same fixed headers, so the instans opens a depth and the
        # DOMSKÄL/DOMSLUT inside it nest under that court instead of listing
        # beside it -- flat, the panel read DOMSKÄL three times and DOMSLUT
        # three times, with no way to tell HD's from tingsrättens (D6).
        self.depth = 0

    def add(self, node_id, text, level):
        if not node_id:
            self._n += 1
            node_id = "sec%d" % self._n
        if text.strip():
            self.entries.append((node_id, text, level + self.depth))
        return node_id


def plain(runs):
    """Heading text for the TOC: inline runs flattened to plain text."""
    return runs_text(runs).strip()


MIN_TOC = 3   # below this many headings a TOC adds clutter, not navigation


def render_toc(toc, top_id):
    """The TOC nav, headed by the document's own short id (`top_id`) as a link
    back to the frontmatter. Every source names its own identifier -- the
    eyebrow is not always it (a wiki page's eyebrow is "Begrepp", an unnamed
    DV case's is the court), so the label is passed rather than derived."""
    if len(toc.entries) < MIN_TOC:
        return ""
    return _META.toc(toc.entries, top_id)


# --------------------------------------------------------------------------
# inline runs + inbound annotation
# --------------------------------------------------------------------------

INBOUND_CAP = 40   # citing docs shown expanded in the predecessor-case
                   # rail; the rest are rendered too, collapsed


# the emphasis a run's `style` flags render as, innermost last. One character
# per flag, since `_emphasise` reads the string a character at a time -- the
# flags are minted by `pdftext.run_style`, so an unknown one is a bug here
# rather than input to tolerate. Superscript is not among them: a footnote
# marker is its own run kind, tagged by the footnote pass.
STYLE_TAGS = {"b": "strong", "i": "em"}


def _emphasise(html, style):
    """`html` wrapped in the tags a run's style flags name. Empty style is the
    common case and passes through untouched."""
    for flag in reversed(style or ""):
        html = "<%s>%s</%s>" % (STYLE_TAGS[flag], html, STYLE_TAGS[flag])
    return html


def render_runs(runs, site):
    if isinstance(runs, str):
        return escape(runs)
    out = []
    for run in runs:
        if isinstance(run, str):
            out.append(escape(run))
            continue
        if "uri" not in run:
            # a plain text run the document emphasised (lib.lagrum._styled)
            out.append(_emphasise(escape(run["text"]), run.get("style")))
            continue
        if run.get("kind") == "footnote":
            # an inline footnote marker -> superscript link to the endnote, with
            # a matching id the endnote's ↩ links back to
            n = escape(run["text"])
            out.append('<sup class="fnref" id="fnref-%s">'
                       '<a href="#fn-%s">%s</a></sup>' % (n, n, n))
            continue
        uri = site.resolve(run["uri"])     # fold a begrepp variant onto its canon
        if site.has(uri):
            # a document we host (incl. EU acts we've parsed) -- local link.
            # Hover preview (the target paragraph, a defined term's definition)
            # is popover.js's job, built from the rendered target page itself --
            # no title attribute, which would fight the popover with a native
            # tooltip. A "term" run is an in-act use of a defined term:
            # underlined, same hover affordance.
            cls = ' class="term"' if run.get("kind") == "term" else ""
            link = ('<a%s href="%s">%s</a>'
                    % (cls, escape(href(uri)), escape(run["text"])))
        elif _is_external(uri):
            # a publisher-number reference we don't host -- out to the
            # publisher (EUR-Lex for a CELEX); becomes a local link once we
            # parse it
            link = ('<a class="ext" href="%s" rel="external">%s</a>'
                    % (escape(_external_href(uri)), escape(run["text"])))
        elif uri.startswith(BASE):
            # a lagen.nu document with no page yet -- show the text, not a
            # link that would 404. Becomes live once that doc is parsed.
            link = ('<span class="noref" title="%s">%s</span>'
                    % (escape(catalog.local(uri)), escape(run["text"])))
        else:
            link = ('<a class="ext" href="%s" rel="external">%s</a>'
                    % (escape(uri), escape(run["text"])))
        out.append(_emphasise(link, run.get("style")))
    return "".join(out)


def _inbound_groups(site, uris, exclude_from=(), exclude_before=None,
                    whole_document=False):
    """Inbound entries grouped into accordion sections by what kind of document
    cites (Lagrumshänvisningar / Förarbeten / Rättsfall / EU-domstolens praxis),
    one collapsed line per citing document (its pinpoints listed inline). `uris`
    is the one target -- or the several sharing one panel (a paragraf and its
    first stycke) -- whose citers collapse together. The grouping is
    `inbound_group`: the citer's source, split by kind where one source carries
    several. Förarbeten are ordered prop→sou→ds→lagrådsremiss→bet,
    oldest-first, case law newest-first, everything else by name; each group
    shows PANEL_CAP docs, the rest behind a "+N fler" disclosure.
    `exclude_from` drops citers already shown elsewhere (a
    statute's own preparatory works); `exclude_before` drops citers dated
    before the anchor's beteckning last changed meaning (they refer to the
    provision that carried the label then, and surface on its successor's
    renumbered_refs_margin instead -- undated citers stay); `whole_document`
    additionally drops the citing spots that pinpoint into the document, whose
    citation the target paragraf's own rail already shows (S2). Returns one
    `RailSection` per group -- one accordion row for Rättsfall, one for
    Förarbeten -- empty when nothing (left) cites `uri`."""
    rows = catalog.inbound_collapsed(site.con, uris, exclude_from,
                                     whole_document=whole_document)
    # a citer that is itself a repealed act (repeal date passed) no longer
    # states law, so it drops out of the inbound panel (I3); a not-yet-in-force
    # repeal is not in site.expired and so stays
    rows = [r for r in rows if r[0] not in site.expired]
    if exclude_before:
        rows = [r for r in rows if not (r[5] and r[5] < exclude_before)]
    bucket = {}
    for row in rows:
        bucket.setdefault(inbound_group(row[3], row[4]), []).append(row)
    for slug, items in bucket.items():
        if slug == "forarbete":
            items.sort(key=lambda r: forarb_sort_key(r[4], r[5], r[1]))
        elif slug in CASELAW_GROUPS:
            # case law reads newest-first, the order eu_caselaw_margin already
            # uses: these citers are named by case number ("C-136/17"), which
            # sorts alphabetically into an order that means nothing. Undated
            # citers trail their dated peers; the label breaks ties so two runs
            # over an unchanged corpus emit the same page.
            items.sort(key=lambda r: (r[5] or "", r[1]), reverse=True)
        elif slug == "dv":
            site.load_inbound_counts(r[0] for r in items)
            # Swedish case law reads most-cited first: how often the corpus
            # cites a case is the closest computable stand-in for how much it
            # settles. Alphabetical by name put AD 1998 nr 7 above Fruktkniven
            # (NJA 2023 s. 560) as the lead case on misshandel -- a labour-court
            # decision ahead of the HD judgment that drew the line (D4). The
            # date and label break ties so two runs over an unchanged corpus
            # emit the same page.
            items.sort(key=lambda r: (site.inbound_counts.get(r[0], 0),
                                      r[5] or "", r[1]), reverse=True)
        else:
            items.sort(key=lambda r: (r[2] or r[1] or "").lower())
    groups = [(slug, heading) for slug, heading in INBOUND_GROUPS
              if slug in bucket]
    groups += [(s, s) for s in bucket if s not in dict(INBOUND_GROUPS)]
    return [RailSection(slug, heading, len(bucket[slug]),
                        _capped_list([_citer_line(row) for row in bucket[slug]]))
            for slug, heading in groups]


def _capped_list(lines, cap=None, word="fler"):
    """A panel's "<li>" lines as a list showing `cap` items, the rest behind a
    "+N <word>" disclosure -- the one home for the cap idiom.

    A cap decides what is *collapsed*, never what is published: every line is
    written into the page. A rail that drops items outright hides exactly the
    ones a reader is least likely to find elsewhere -- the EU case-law rail
    sorts newest-first, so its old hard cap silently withheld the foundational
    judgments (LOU 17 kap. had 41 cases per paragraf and published 5)."""
    # resolved at call time, not bound as a default: the module constant is
    # monkeypatched in tests, and a default argument would freeze it
    cap = PANEL_CAP if cap is None else cap
    return _RAIL.capped_list([Markup(line) for line in lines], cap, word)


def document_inbound(site, uri, exclude_from=()):
    """Document-level inbound: who cites the law/case/förarbete as a whole
    (the bare uri). Surfaces the citations no paragraph annotation shows.
    `exclude_from` omits citers listed elsewhere (a statute's own förarbeten,
    which get their own preparatory-works section above).

    Rail sections, not a panel in the reading column: this is context on the
    document, exactly like a paragraf's citations are context on the paragraf,
    and a full-width box of a few hundred citations sat between the reader and
    the statute's first § (S5)."""
    return _inbound_groups(site, [uri], exclude_from, whole_document=True)


# --------------------------------------------------------------------------
# rail sections (one accordion row per kind of context)
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class RailSection:
    """One kind of context attached to one node. The rail renders it as an
    accordion row; the client's collapsed one-line stub is built from the same
    label and count, so the two can never disagree (C3/C4)."""

    key: str        # stable type slug -- ordering, css hook, client grouping
    label: str      # what the accordion row is called
    count: int      # items in it, shown beside the label and in the stub line
    html: str       # the body; headingless, the <summary> carries the label
    # a flat section renders its body inline under its label, no accordion --
    # for context light enough that a fold would cost more than it saves
    # (a recital's one relevant-article link)
    flat: bool = False


# Which context a reader wants first when a node carries several kinds. The
# ordering is editorial and follows the shape lagen.nu has always used: our own
# commentary, then the lagstiftare's own, then case law, then the citation
# graph, then the machinery (transposition, delegation, renumbering). Exactly
# the first section present opens; the rest stay one click away (C3). A key not
# listed here (a source with no assigned rank) sorts last, by label.
RAIL_SECTION_ORDER = (
    "kommentar", "fk", "dv", "avg", "rs", "hudoc", "icc", "icj", "eu-caselaw",
    "eu-forslag",
    "aldre-rattsfall", "sfs", "forarbete", "foreskrift", "bemyndigande",
    "eurlex", "guidance", "lawreview", "coe", "icrc", "untc", "begrepp",
    "genomfor", "regleringshierarki", "fyller-ut", "remiss",
    "vagledning", "andringar", "skal", "tidigare-beteckning", "motsvarighet")


def _rail_rank(section):
    order = RAIL_SECTION_ORDER
    return (order.index(section.key) if section.key in order else len(order),
            section.label)


def merge_rail_sections(sections):
    """Fold sections of the same kind into one accordion row, in first-seen
    order. Two arise only where a panel covers several nodes (a paragraf and its
    folded first stycke, C2) and both carry, say, commentary -- two rows with the
    same heading would read as a rendering bug. Rows that differ by label (one
    "Äldre rättsfall …" per predecessor provision) stay separate."""
    merged = {}
    for sec in sections:
        prev = merged.get((sec.key, sec.label))
        merged[(sec.key, sec.label)] = RailSection(
            sec.key, sec.label,
            (prev.count if prev else 0) + sec.count,
            (prev.html if prev else "") + sec.html,
            flat=sec.flat)
    return list(merged.values())


def ordered_sections(sections):
    """The sections folded and ranked exactly as the rail shows them, for a page
    that renders them somewhere else. The rail's own markup carries accordion
    and scrollspy semantics (one row open, `data-sec` targets) that mean nothing
    outside the margin, so a page rendering these in its reading column brings
    its own markup and takes only the order."""
    return sorted(merge_rail_sections(sections), key=_rail_rank)


def render_rail_sections(sections):
    """A panel body: every section as an accordion row -- except the flat
    ones, shown inline -- with the highest-priority foldable row open and the
    rest collapsed (partials/rail.html; scrollspy keeps at most one open
    thereafter). The `data-label`/`data-n` attributes are what the client
    reads to compose a location's collapsed stub line, so the summary never
    drifts from the panel it summarises."""
    ordered = sorted(merge_rail_sections(sections), key=_rail_rank)
    first_foldable = next((s for s in ordered if not s.flat), None)
    return _RAIL.rail_sections(
        [{"key": sec.key, "label": sec.label, "count": sec.count,
          "html": Markup(sec.html), "flat": sec.flat,
          "open": sec is first_foldable}
         for sec in ordered])


class Rail:
    """Collects each paragraph's context panel (who cites it, and which EU
    article it transposes) as a document is rendered, keyed by the node's anchor
    id. Serialized to a JSON island the client swaps into the right rail as the
    reader scrolls -- the Gravitas "Kontext för …" rail. The link/href logic
    stays in Python; the client only moves pre-rendered HTML. A node carries a
    ``data-rail`` attribute (nodes.html `rail_attr`) iff it has an entry here,
    so the scrollspy knows which elements drive the rail."""

    def __init__(self, site, doc_uri):
        self.site = site
        self.doc_uri = doc_uri
        # the document-level "Om dokumentet" panel holds only the curated layers
        # (Externa länkar, Kommentar, …); the dl.meta facts and the "Källa" source
        # link live under the h1 in the frontmatter (C1), not here.
        self.data = {}
        # panel id -> (heading, [node ids it covers]) -- more than one where a
        # first stycke, and the first punkt under it, folded into their paragraf
        # (see `add`)
        self.covers = {}
        # every folded node id -> the panel id that took it over, so the *next*
        # level down can find its host even though its own parent no longer has
        # a panel of its own (1 § 1 st 1 p folds onto 1 §, not onto 1 § 1 st)
        self.host_of = {}
        # anchor -> [(verb, nr, prop identifiers)]: the provision's own change
        # history from the SFSR register (amendment_index; render_sfs primes
        # it, every other vertical leaves it empty)
        self.amendment_index = {}
        # what `add_document` built, so a page can render the same sections in
        # its own body without paying for them a second time
        self.doc_sections = []

    def add(self, nid, pinpoint="", extra=()):
        """Record node `nid`'s rail panel if it has commentary, anything cites it,
        it transposes an EU article, or it carries editorial `extra` sections
        (the EU article<->recital links). Idempotent per id; no-op for
        context-less nodes.

        A *first stycke* whose paragraf already has a panel folds into it rather
        than opening a second one (C2): the two describe the same provision, and
        two rail targets one line apart could not both be read -- scrolling
        between them just swapped the rail back and forth. The walk always
        reaches the paragraf first, so the fold is a rebuild of its panel over
        both anchors, and the citers of each collapse into one line per document.
        A first stycke whose paragraf carries nothing keeps its own panel.

        The *first punkt* of that first stycke folds in the same way and for the
        same reason, one level deeper. Where a paragraf opens with a lead-in line
        and then a numbered list -- 1 kap. 20 § YGL, 1 kap. 4 § YGL -- the first
        punkt begins a line or two below the § itself, which is exactly the
        unreadable pair the stycke fold exists to prevent. Folding it leaves the
        first *separate* rail at "1 st 2 p", far enough down the page to be a
        target of its own. It folds onto the paragraf, not onto the stycke, since
        by then the stycke has no panel of its own -- `host_of` is what carries
        that across the two levels."""
        if not nid or nid in self.data:
            return
        host, heading = nid, "Kontext%s" % (
            ' för <b>%s</b>' % escape(pinpoint) if pinpoint else "")
        anchors = [nid]
        # "…S1" onto its paragraf, "…S1N1" onto whatever took that stycke over
        parent = nid[:-2] if nid.endswith(("S1", "N1")) else None
        parent = self.host_of.get(parent, parent)
        if parent in self.data:
            host = parent
            heading, covered = self.covers[host]
            anchors = covered + [nid]
        uris = [self.doc_uri + "#" + a for a in anchors]
        # everything keyed on a single node is collected per folded anchor (a
        # stycke can carry its own commentary); the citation graph is queried
        # over all of them at once, so a document citing both is one line
        sections = [s for anchor, uri in zip(anchors, uris, strict=True)
                    for s in (
                        self._commentary(anchor)
                        + self._fk(anchor)
                        + self._guidance(
                            self.site.article_guidance.get((self.doc_uri, anchor)))
                        + self._remiss(
                            self.site.remiss_feedback.get((self.doc_uri, anchor)),
                            "Avsnittet")
                        + margins.genomfor_margin(self.site, self.doc_uri, anchor)
                        + margins.eu_caselaw_margin(self.site, self.doc_uri, anchor)
                        + margins._bemyndigande_margin(self.site, uri)
                        + margins.corresponding_cases_margin(self.site, uri)
                        + margins.renumbered_refs_margin(self.site, uri)
                        + margins.corresponds_margin(self.site, uri)
                        + self._andringar(anchor))]
        sections += margins.regleringshierarki_margin(self.site, self.doc_uri,
                                              anchors)
        sections += margins.fyller_ut_margin(self.site, self.doc_uri, anchors)
        sections += list(extra) + _inbound_groups(
            self.site, uris,
            exclude_before=margins._reassigned_before(self.site, uris[0]))
        if not sections:
            return
        self.data[host] = self._panel(heading, sections)
        self.covers[host] = (heading, anchors)
        for anchor in anchors:
            self.host_of[anchor] = host

    def document_sections(self, exclude_from=(), inbound=True):
        """The document-level context as a section list: the act's curated
        external links (Externa länkar), any commentary on the document as a
        whole, and who cites the document as such. Split out from
        `add_document` because a page can want this content in its *body*
        rather than in the rail -- a begrepp page with no written description is
        nothing but its occurrences, so putting them in the margin leaves the
        reading column empty (see `wiki/render.py`)."""
        sections = (
            self._guidance(self.site.guidance.get(self.doc_uri))
            + self._commentary(None)
            + self._fk(None)
            # the "most interesting feedback" for the whole SOU/Ds. v1
            # deliberately renders every overall stance as-is; a later pass
            # can rank by |sentiment| to surface only the strongest.
            + self._remiss(self.site.remiss_overall.get(self.doc_uri),
                           "Betänkandet"))
        if inbound:
            sections += document_inbound(self.site, self.doc_uri, exclude_from)
        return sections

    def add_document(self, exclude_from=(), inbound=True):
        """The document-level rail panel (key ''), shown when no single paragraph
        is in focus (at the top of the document) -- `document_sections` put in
        the margin (S5 -- context on the document, exactly as a paragraf's
        citations are context on the paragraf). `exclude_from` omits citers the
        page shows in another role (a statute's own preparatory works);
        `inbound=False` suppresses the citation sections on a page that is not
        the citable document -- a historical consolidation, since citations
        always target the current one."""
        sections = self.document_sections(exclude_from, inbound)
        # kept for a page that renders this content itself: `document_sections`
        # runs a catalog query and builds every citer line, and a concept page
        # with no description needs the same list in its body. Recomputing it
        # there ran the whole thing twice for ~28,300 pages.
        self.doc_sections = sections
        if sections:
            self.data[""] = self._panel("Om dokumentet", sections)

    def drop_document_sections(self, keys):
        """Rebuild the document-level panel without the sections in `keys`.

        For a page that renders those sections itself, in its reading column: a
        concept page shows what each act says the term means, and leaving the
        same acts listed in the margin prints the list twice."""
        self.doc_sections = [s for s in self.doc_sections if s.key not in keys]
        if self.doc_sections:
            self.data[""] = self._panel("Om dokumentet", self.doc_sections)
        else:
            self.data.pop("", None)

    @staticmethod
    def _panel(heading, sections):
        return ('<div class="rail-h">%s</div>%s'
                % (heading, render_rail_sections(sections)))

    def _remiss(self, items, subject):
        """Remiss (referral) feedback on a node -- what each answering organisation
        said about this section (or, in `add_document`, the SOU/Ds as a whole),
        from the `.ann` sentiment layer. Render-only: the remiss corpus has no page
        of its own, so each item links out to the organisation's own answer PDF
        (`source_url`, a "Källa" link, always `rel="external"` -- a remiss PDF is
        never a BASE-prefixed internal url). Everything shown (organisation, quote)
        is PDF/LLM-derived and `html.escape`d, exactly like `_guidance`."""
        if not items:
            return []
        return [RailSection("remiss", "Remissvar", len(items),
                            _RAIL.remiss_list(
                                [{"organisation": it["organisation"],
                                  "sentiment": Markup(
                                      margins._sentiment_span(it["sentiment"])),
                                  "quote": it["quote"],
                                  "source_url": it["source_url"]}
                                 for it in items],
                                margins._remiss_summary(items, subject)))]

    def _guidance(self, items):
        """A list of curated external links -- the wiki annotation's `## Externa
        länkar` block (Commission FAQs, guidance PDFs, call-for-evidence pages, …) --
        used both for the act's document-level panel (Step 2) and for a single
        article's context panel (Step 3). Render-only: these resources live outside
        the corpus, so they carry no inbound edge. A lagen.nu-absolute href renders
        internal, any other an external link."""
        if not items:
            return []
        # a guidance link carries either a `desc` (the guidance section's own
        # text, e.g. the FAQ question -- shown after the link as ": ...") or a
        # `note` (provenance for a hand-curated link -- shown as "— ...")
        return [RailSection("vagledning", "Externa länkar", len(items),
                            _RAIL.guidance_list(
                                [{"href": href(g["href"]),
                                  "external": not g["href"].startswith(BASE),
                                  "label": g["label"], "desc": g.get("desc"),
                                  "note": g.get("note")}
                                 for g in items]))]

    def _fk(self, nid):
        """The författningskommentar prose propositioner wrote for the paragraph
        `nid` (or None for the law as a whole): each prop's comment opens the
        section (initial text, ellipsized on a word boundary), with the
        proposition as a provenance link pinpointing the FK page. The official
        sibling of the wiki `_commentary` -- authored by the lagstiftare, not our
        editors -- so it stays its own section."""
        entries = self.site.fk.get((self.doc_uri, nid))
        if not entries:
            return []
        out = []
        for prop_uri, label, page, text in entries:
            target = prop_uri + ("#sid%d" % page if page else "")
            src = (Markup('<a href="%s">%s</a>') % (href(target), label)
                   if label and self.site.has(prop_uri)
                   else Markup(escape(label or "")))
            out.append({"lead": textwrap.shorten(text.split("\n")[0], 300,
                                                 placeholder=" …"),
                        "src": src})
        return [RailSection("fk", "Författningskommentar", len(out),
                            _RAIL.fk_list(out))]

    def _commentary(self, nid):
        """The wiki commentary for the paragraph `nid` (or `None` for the law as a
        whole), rendered as a rail section (its prose + author byline) -- shown
        side-by-side with what it comments on, in place of a separate kommentar
        page."""
        entries = self.site.commentary.get((self.doc_uri, nid))
        if not entries:
            return []
        items = [{"prose": Markup("".join(
                      "<p>%s</p>" % render_runs(c["text"], self.site)
                      for c in blocks if c.get("text"))),
                  "author": author}
                 for author, blocks in entries]
        return [RailSection("kommentar", "Kommentar", len(items),
                            _RAIL.commentary_list(items))]

    def _andringar(self, anchor):
        """The provision's own change history (S1): the register posts whose
        Omfattning names this anchor -- "Ändrad: SFS 2011:864 (Prop.
        2010/11:158)" -- each linking the amending act's entry in the
        bottom-of-page register and, where we host it, the proposition. The
        legacy Ändringar accordion, minus its silent drop of amendments
        without a registered proposition."""
        entries = self.amendment_index.get(anchor)
        if not entries:
            return []
        items = []
        for verb, nr, props in entries:
            prov = (' <span class="prov">(%s)</span>'
                    % Markup(", ").join(prop_link(self.site, f) for f in props)
                    if props else "")
            items.append('<li>%s: <a href="#%s">SFS %s</a>%s</li>'
                         % (verb, escape(register_anchor(nr)), escape(nr), prov))
        return [RailSection("andringar", "Ändringar", len(items),
                            _capped_list(items, ANDRINGAR_CAP, "till"))]

    def island(self):
        """The ``<script type=application/json>`` island, or '' if no paragraph
        has context. ``</`` is escaped so the payload can't break out of the
        surrounding HTML."""
        if not self.data:
            return ""
        payload = json.dumps(self.data, ensure_ascii=False).replace("</", "<\\/")
        return ('<script type="application/json" id="lagen-context">%s</script>'
                % payload)


# --------------------------------------------------------------------------
# generic node renderer (artifact type -> HTML)
# --------------------------------------------------------------------------

def _strip_self_ref(runs, nid):
    """A container's title ("1 kap. Lagens tillämpningsområde") carries its own
    designator as a leading reference run that the citation engine linked back
    to this very container (`#K1`) -- a pointless self-link. Flatten any run
    targeting the container's own id to plain text; leave real cross-references
    alone."""
    if not isinstance(runs, list):
        return runs
    return [run["text"] if isinstance(run, dict)
            and run.get("uri", "").rpartition("#")[2] == nid
            else run
            for run in runs]


def _renest_punkter(children):
    """Rebuild the list nesting the NF flattens away. ``nf.flatten_list`` emits a
    stycke's list items in document order as flat ``punkt`` siblings, but their
    ids still encode the hierarchy: a sub-item ``K1P2S1N12Na`` sits under
    ``K1P2S1N12``. Return the children with each sub-item moved into its parent
    punkt's ``children`` so the caller emits it as a nested <ol>. A non-punkt
    child (a tabell) or an id-less punkt breaks the run and stays at top level.

    The ``"N"`` separator is a load-bearing cross-layer contract: ``sfs.nf``'s
    ``flatten_list`` mints each sub-item id as ``<parent-id>N<ordfrag>`` (see the
    ``extend(pairs, "N", …)`` there), so a child is exactly a punkt whose id is
    ``parent_id + "N" + …``. This decodes that grammar; it must track the minter."""
    roots = []
    stack = []  # (id, node copy) of the punkt ancestors currently open
    for c in children:
        if c.get("type") != "punkt" or not c.get("id"):
            roots.append(c)
            stack.clear()
            continue
        while stack and not c["id"].startswith(stack[-1][0] + "N"):
            stack.pop()
        node = dict(c, children=list(c.get("children", [])))
        (stack[-1][1]["children"] if stack else roots).append(node)
        stack.append((c["id"], node))
    return roots


_GRAFIK_LABEL = {
    "bilaga": "Bilaga", "bild": "Bild", "karta": "Karta", "figur": "Figur",
    "formel": "Formel", "symbol": "Symbol", "specialtecken": "Specialtecken",
    "forteckning": "Förteckning", "tabell": "Tabell", "vagmarke": "Vägmärke",
    "blankett": "Blankett", "formular": "Formulär"}


def register_anchor(nr):
    """Fragment id of an amendment's entry in the statute's own amendment
    register section (the ``L{nr}`` ids the andringar renderer mints); spaces
    in old s.-numbered identifiers normalize to underscores, as there."""
    return "L" + nr.replace(" ", "_")


def _grafik_crop(entry, doc_uri, gap_key, alt):
    """The `<img>` for one located graphic: the /api/v1/sfs-graphic crop of the
    provenance-correct published PDF (geometry lives server-side in the layer,
    so the src is just uri+node), lazily loaded. `v` hashes source, page and
    bbox so every content-changing re-verification gets a fresh immutable URL --
    and *both* render resolutions with them, because the response is cached
    `immutable` for a year: raising either constant behind an unchanged URL
    would reach nobody who had already loaded the page, and no CDN edge at all.
    The large one belongs here too even though this URL is the thumbnail's: the
    lightbox mints its own by appending `stor=1` to this very string, so one
    identity selects both renders (`assets/grafik.js`)."""
    versioned = {"sfs": entry["sfs"], "page": entry["page"],
                 "bbox": entry.get("bbox"),
                 "dpi": [facsimile.CROP_DPI, facsimile.CROP_DPI_LARGE]}
    ver = hashlib.sha256(json.dumps(versioned, sort_keys=True).encode()).hexdigest()[:12]
    src = "/api/v1/sfs-graphic?uri=%s&node=%s&v=%s" % (
        quote(doc_uri, safe=""), quote(gap_key, safe=""),
        quote(ver, safe=""))
    return NODES.grafik_img(src, alt)


def render_grafik(node, site, doc_uri):
    """A graphic/formula/map the published SFS carries but the consolidated text
    drops. When the `.graphics` layer has placed this gap, emit the crop as a
    `<figure>` with source attribution; otherwise fall back to an honest
    placeholder naming the source SFS. Keys on the generic ``grafik`` node type
    and reads the layer off `site` -- no source import (rule:lib-never-imports-vertical)."""
    nid = node.get("key") or node.get("id", "")
    label = _GRAFIK_LABEL.get(node.get("sort"), "Grafik")
    entry = site.graphics.get((doc_uri, nid))
    if not entry:
        sfs = node.get("satt_av")
        where = ("SFS %s" % sfs) if sfs else "den tryckta författningen"
        return NODES.grafik_saknas(nid, label, where)
    alt = entry.get("alt") or ("%s ur SFS %s" % (label, entry["sfs"]))
    return NODES.grafik_figure(nid, _grafik_crop(entry, doc_uri, nid, alt),
                                label, register_anchor(entry["sfs"]),
                                entry["sfs"])


LANGUAGE_LABELS = {
    "de": "Deutsch",
    "en": "English",
    "fr": "Français",
    "sv": "Svenska",
    "tr": "Türkçe",
}


def _parallel_versions(node):
    return {version["language"]: version for version in node["versions"]}


def _convention_cell(version, site, tag):
    return NODES.konvention_cell(version["language"], tag,
                                  Markup(render_runs(version.get("text", []),
                                                     site)))


def _convention_row(node, site, css, tag, languages):
    versions = _parallel_versions(node)
    return NODES.konvention_row(css, Markup("").join(
        _convention_cell(versions[language], site, tag)
        for language in languages))


def _convention_paragraphs(node, site, languages):
    rows = []
    for paragraph in node.get("paragraphs", []):
        assert paragraph.get("type") == "konventionsstycke", \
            "unknown convention paragraph node %r" % paragraph.get("type")
        rows.append(_convention_row(paragraph, site, "konvention-paragraph", "p",
                                    languages))
    return "".join(rows)


def _toc_label(versions, languages):
    # Swedish is the site language and the natural TOC label; fall back to the
    # last run for a parallel corpus that happens not to include Swedish.
    return plain((versions.get("sv") or versions[languages[-1]]).get("text", []))


def article_label(node):
    """A treaty provision's rail label: "Artikel 5", or the provision's own
    heading where it has no ordinal. An unnumbered provision is real -- a
    preamble, a final clause, and UNCLOS's Annex I, which is a list of 17
    species and no article at all -- and the heading is then all that names it.
    `coe` labels its own, because it tells an artikel from a sektion."""
    ordinal = node.get("ordinal")
    return "Artikel %s" % ordinal if ordinal else plain(node.get("text", []))


def provision_section(node, site, doc_uri, toc, rail, label):
    """One treaty provision as an addressable section: its heading enters the TOC
    and the rail (under `label`, the only thing that differs between the
    instrument sources -- coe distinguishes artikel from sektion, icrc falls back
    to the heading text for an unnumbered one), and its children render through
    the generic node walk."""
    aid = node.get("id")
    anchor = toc.add(aid, plain(node.get("text", [])), 1)
    rail.add(aid, label)
    children = "".join(render_node(child, site, doc_uri, toc, rail)
                       for child in node.get("children", []))
    return NODES.artikel_section(
        aid if aid and aid in rail.data else None, anchor,
        Markup(render_runs(node.get("text", []), site)), Markup(children))


def _render_konventionsinstrument(node, site, toc, languages):
    versions = _parallel_versions(node)
    toc.add(node.get("id"), _toc_label(versions, languages), 2)
    title = _convention_row(node, site, "konvention-title", "h3", languages)
    ingress = _convention_paragraphs(node, site, languages)
    provisions = []
    for child in node.get("children", []):
        child_versions = _parallel_versions(child)
        toc.add(child.get("id"), _toc_label(child_versions, languages), 3)
        kind = child["type"]
        assert kind in {"konventionsavdelning", "konventionsartikel"}, \
            "unknown convention appendix node %r" % kind
        css = "konvention-section" if kind == "konventionsavdelning" \
            else "konvention-article"
        heading = _convention_row(child, site, css + "-heading", "h4", languages)
        paragraphs = _convention_paragraphs(child, site, languages)
        provisions.append(NODES.konvention_section(
            css, child.get("id"), heading, Markup(paragraphs)))
    return NODES.konvention_instrument(node.get("id"), title, Markup(ingress),
                                        Markup("").join(provisions))


def _render_konventionsbilaga(node, site, doc_uri, toc, rail):
    languages = node.get("languages")
    assert languages, "convention appendix must declare its languages"
    instruments = Markup("").join(
        _render_konventionsinstrument(child, site, toc, languages)
        for child in node.get("children", []))
    return NODES.konventionsbilaga(
        len(languages),
        [(language, LANGUAGE_LABELS.get(language, language))
         for language in languages],
        instruments)


def _temporal_notice(node):
    """The entry-into-force banner for a temporal variant the consolidated
    source prints alongside its in-force sibling ('' for a plain node). The
    value is either an ISO date or the source's verbatim authorization phrase
    ("den dag som regeringen bestämmer") -- shown as-is; interpreting a later
    "ikrafttr. av"-decree is the reader's (or a future pass's) job."""
    parts = ["%s: %s" % (label, node[key]) for label, key in
             (("Upphör att gälla", "upphor"), ("Träder i kraft", "ikrafttrader"))
             if node.get(key)]
    if not parts:
        return ""
    return NODES.temporal_notice(" — ".join(parts))


# an ISO date, as `temporal_fields` writes one; anything else is the source's
# verbatim authorization phrase ("den dag som regeringen bestämmer"), which
# names no moment and so can never say a variant is out of force
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _iso(node, key):
    """`node[key]` when it is an ISO date, else '' -- the source's verbatim
    authorization phrase names no moment, so there is nothing for the client
    to re-evaluate."""
    value = node.get(key)
    return value if isinstance(value, str) and _ISO_DATE.fullmatch(value) else ""


def temporal_state(node, today):
    """Whether a temporal variant is out of force at `today` -- 'expired'
    (its upphör date has passed), 'pending' (its ikraftträdande date has not
    arrived), else ''.

    A consolidated statute prints an amended provision as two sibling
    variants, and around the boundary both are on the page: BrB 3 kap. 6 §
    stood as "/Upphör att gälla: 2026-08-01/ … lägst fem år" directly above
    "/Träder i kraft: 2026-08-01/ … lägst sex år", the expired one first and
    the markers in small grey italics. A reader could not tell which penalty
    was law today. The state is stamped on the node so both the stylesheet and
    `versions.js` can dim what is not in force -- server-side against the
    build date, which a nightly rebuild keeps within a day, then corrected in
    the browser against the reader's own clock."""
    upphor, ikraft = _iso(node, "upphor"), _iso(node, "ikrafttrader")
    if upphor and upphor <= today:
        return "expired"
    if ikraft and ikraft > today:
        return "pending"
    return ""


def _render_rad(node, site, doc_uri, image_column):
    """One table row. `image_column` says the whole table carries a road-sign
    image column, which this row must fill even when it has no sign of its own
    (the header row, and a row the published PDF draws nothing for).

    A row flagged `th` emits header cells. `lib/artifact.scanned_nodes` has been
    writing that flag all along (rs, avg, guidance, lawreview) and this walk
    ignored it, so those column headers rendered as ordinary `td`; föreskrift's
    ordförklaringar table is the first artifact whose header row is *read* as
    one. Honouring it changes rendered markup for the sources that already carry
    the flag -- 4 of 400 sampled rs artifacts, ~29 documents corpus-wide."""
    cells = [_render_cell(c, site, node, i)
             for i, c in enumerate(node.get("cells", []))]
    g = node.get("grafik")
    grafik_cell = ""
    if g:  # a dropped road-sign image (2007:90): the sign beside its code
        gid = g.get("key") or g.get("id", "")
        entry = site.graphics.get((doc_uri, gid))
        if entry:
            alt = entry.get("alt") or ("Vägmärke %s" % g.get("code", ""))
            grafik_cell = NODES.grafik_cell(
                gid, _grafik_crop(entry, doc_uri, gid, alt))
        else:  # unlocalized: the honest gap beside the code
            grafik_cell = NODES.grafik_saknas_cell(gid, g.get("code", ""))
    elif image_column:
        grafik_cell = NODES.grafik_spacer_cell()
    # a pending/expiring row variant carries the temporal marker itself
    # (a 2007:90 road-sign row amended with deferred entry into force);
    # printed as a marker row above, spanning the full width
    # a definition row carries an id (sfs.nf mints one for the row that defines
    # a term, so a term link can anchor the definition rather than the sentence
    # announcing the list); no pilcrow -- it is a target, not a citable unit
    return NODES.rad(_temporal_notice(node),
                     len(node.get("cells", [])) + (1 if grafik_cell else 0),
                     grafik_cell, cells,
                     "th" if node.get("th") else "td",
                     node.get("id"))


def _render_cell(runs, site, row, i):
    """One cell: its runs, plus the rows and columns it spans.

    A cell holding several things -- an OJ annex cell lists three kinds of
    marknadsaktör as three dash items -- carries them as separate lines, which
    the parser writes as newlines and the cell prints as line breaks. Every
    other source's cells are single runs of text with no newline in them, so
    this is inert for them."""
    def span(key):
        spans = row.get(key) or []
        return spans[i] if i < len(spans) else 1

    return {"html": Markup(render_runs(runs, site).replace("\n", "<br>")),
            "rowspan": span("rowspan"), "colspan": span("colspan")}


def render_tabell(node, site, doc_uri, toc, rail):
    """A `tabell` node -> one `<table>`: its `rad` children as rows, anything
    else through the ordinary node walk, and its own `text` as the caption.

    Public because eurlex walks its own document tree (`eurlex/render.py`) and
    reads the same node pair -- one table renderer for every source that writes
    one (rule:second-use-goes-to-lib)."""
    # a road-sign table gains a leading image column. Its header row carries
    # no sign of its own, so it needs the column too -- without it "Märke"
    # and "Närmare föreskrifter" sit one column left of the cells they name.
    image_column = any(c.get("grafik") for c in node.get("children", []))
    return NODES.tabell(Markup("".join(
        _render_rad(c, site, doc_uri, image_column)
        if c.get("type") == "rad"
        else render_node(c, site, doc_uri, toc, rail)
        for c in node.get("children", []))),
        Markup(render_runs(node.get("text") or [], site)))


def render_node(node, site, doc_uri, toc, rail, drop_marker=False):
    t = node.get("type")
    nid = node.get("id")

    if t == "konventionsbilaga":
        return _render_konventionsbilaga(node, site, doc_uri, toc, rail)

    if t == "tabell":
        return render_tabell(node, site, doc_uri, toc, rail)
    if t == "rad":
        # a row reached outside its table: it can only speak for itself, and
        # `image_column` is unread whenever the row carries a sign of its own
        return _render_rad(node, site, doc_uri, False)
    if t == "grafik":
        return render_grafik(node, site, doc_uri)
    if t == "avskiljare":
        return "<hr>"
    if t == "allmanna_rad":
        # a föreskrift's allmänt råd: advisory text under the paragraf it
        # explains. The documents state its status themselves ("Allmänna råd
        # har en annan juridisk status än föreskrifter. De är inte tvingande"),
        # so the råd is set apart and labelled with the heading the page
        # printed -- which often names the provision ("Allmänna råd till 3 §").
        # No TOC entry: a chaptered föreskrift prints one råd per §, and listing
        # them would bury the headings the reader navigates by.
        # `text` is the heading the page printed ("Allmänna råd", or "Allmänna
        # råd till 3 §"); the parser always writes it, and a råd left without
        # one is demoted to a rubrik rather than reaching here
        return NODES.allmanna_rad(
            Markup(render_runs(node["text"], site)),
            Markup("".join(render_node(c, site, doc_uri, toc, rail)
                           for c in node.get("children", []))))
    if t == "lista":
        items = "".join(render_node(c, site, doc_uri, toc, rail)
                        for c in node.get("children", []))
        # a numbered list keeps its numbering: the wiki commentary writes out
        # seven criteria for övergång av verksamhet (1982:80 6 b §), and which
        # criterion is the fourth is part of what the text says
        tag = "ol" if node.get("ordered") else "ul"
        return "<%s class=\"punkter\">%s</%s>" % (tag, items, tag)
    if t == "rubrik":
        text = node.get("text", [])
        anchor = toc.add(nid, plain(text), node.get("level") or 1)
        lvl = min(node.get("level") or 2, 5) + 1
        return NODES.rubrik(lvl, anchor, Markup(render_runs(text, site)))

    # the node's context (who cites it + which EU article it transposes) is
    # routed to the scroll-driven rail, not floated inline; the element is tagged
    # data-rail so the client knows it drives the rail. Leaf rubrik/tabell/rad/
    # lista nodes above carry no context.
    rail.add(nid, human_fragment(nid))

    if "text" in node:  # stycke/punkt/listelement/upphavd/moment (may nest)
        # the paragraf's own number now hangs in the gutter (drop_marker), so the
        # first stycke no longer repeats it inline; sub-stycken/punkter keep theirs
        marker = None if drop_marker else (node.get("beteckning") or node.get("ordinal"))
        is_listitem = t in ("punkt", "listelement")
        # a numbered list item carries the source's trailing dot ("1." not "1")
        # and hangs its marker in a gutter column (CSS), so it needs no inline
        # separator space; an inline stycke/moment marker keeps its trailing space
        if marker and is_listitem and str(marker).isdigit():
            marker = "%s." % marker
        # a stycke/punkt often introduces a list -- render its punkt/lista children
        # (previously dropped, so numbered lists vanished from the page). The NF
        # flattens nested lists into document order (nf.flatten_list); rebuild the
        # nesting the item ids still encode (K1P2S1N12Na under K1P2S1N12) so a
        # sub-list (a/b/c under a numbered point) renders as a nested <ol>.
        kids = _renest_punkter(node.get("children", []))
        inner = ""
        if kids:
            inner = "".join(render_node(c, site, doc_uri, toc, rail) for c in kids)
            if any(c.get("type") == "punkt" for c in kids):
                inner = '<ol class="punkter">%s</ol>' % inner
        # a `redaktionell` node is a publisher's editorial note (a repeal
        # notice, "text finns bara i tryckt version"): rendered like the stycke
        # it replaced -- same marker, same links -- but subdued, so the reader
        # sees at a glance that it is the publisher speaking and not the
        # statute (sfs/redaktionell.py). A sub-list nests inside its list item
        # (<li>…<ol>…</ol></li>); a stycke's list follows the closed paragraph
        # (<p>…</p><ol>…</ol>) -- the macro keys that on the tag.
        return NODES.block("li" if is_listitem else "p", nid,
                            t == "redaktionell",
                            nid if nid in rail.data else None,
                            str(marker) if marker else None,
                            "" if is_listitem else " ",
                            Markup(render_runs(node["text"], site)),
                            Markup(inner))

    # container: paragraf, kapitel, avdelning, bilaga, overgangsbestammelse, ...
    if t in ("kapitel", "avdelning", "underavdelning"):
        label = {"kapitel": "kap.", "avdelning": "Avd.",
                 "underavdelning": "Avd."}[t]
        # the container's own title is its first child: a level-1 rubrik reading
        # "1 kap. Lagens tillämpningsområde" whose leading "1 kap." designator the
        # citation engine self-links back here. Adopt that rubrik AS the single
        # chapter heading -- under the container's id (the #K1 anchor target and
        # TOC entry), self-link flattened -- rather than emitting a bare-number
        # "1 kap." kaprubrik plus the redundant rubrik that repeats it.
        kids = node.get("children", [])
        title = (kids[0] if kids and kids[0].get("type") == "rubrik"
                 and (kids[0].get("level") or 1) == 1 else None)
        rail_id = nid if nid and nid in rail.data else None
        if title is not None and plain(title.get("text", [])):
            # anchor the heading at the container's id (or a minted secN when the
            # container is id-less) and point the TOC there; capturing toc.add's
            # return keeps the heading id and the TOC anchor in lockstep, as the
            # rubrik branch does -- an id-less container would otherwise emit no
            # heading id while the TOC still linked its minted secN anchor
            anchor = toc.add(nid, plain(title.get("text", [])), 1)
            head = NODES.kaprubrik(anchor, Markup(render_runs(
                _strip_self_ref(title.get("text", []), nid), site)))
            body = kids[1:]
        else:
            # no usable title (empty rubrik) -- keep the bare designator heading
            head = NODES.kaprubrik_bare(
                nid, ("%s %s" % (node.get("ordinal", ""), label)).strip())
            body = kids[1:] if title is not None else kids
        children = "".join(render_node(c, site, doc_uri, toc, rail) for c in body)
        return NODES.kapitel(t, rail_id, head, _temporal_notice(node),
                              Markup(children),
                              temporal_state(node, site.today),
                              _iso(node, "upphor"), _iso(node, "ikrafttrader"))

    if t == "paragraf":
        # hanging §-numeral in the gutter; the first stycke drops its inline number
        kids = node.get("children", [])
        children = "".join(
            render_node(c, site, doc_uri, toc, rail,
                        drop_marker=(i == 0 and c.get("type")
                                     in ("stycke", "redaktionell")))
            for i, c in enumerate(kids))
        # the §-symbol belongs with the numeral ("1 §") in the gutter; the
        # permalink anchor keeps its own (pilcrow) glyph
        ordinal = node.get("ordinal", "")
        return NODES.paragraf(nid or "",
                               nid if nid and nid in rail.data else None,
                               "%s §" % ordinal if ordinal else "§",
                               _temporal_notice(node), Markup(children),
                               temporal_state(node, site.today),
                               _iso(node, "upphor"),
                               _iso(node, "ikrafttrader"))

    # a bilaga's notice follows its heading (first child rubrik), matching the
    # source's "Bilaga 1 /Träder i kraft I:.../" heading line
    notice = _temporal_notice(node)
    kids = node.get("children", [])
    rendered = [render_node(c, site, doc_uri, toc, rail) for c in kids]
    if notice and kids and kids[0].get("type") == "rubrik":
        rendered.insert(1, notice)
    else:
        rendered.insert(0, notice)
    return NODES.generic_section(t or "node", nid,
                                  nid if nid and nid in rail.data else None,
                                  Markup("".join(rendered)))


def document_body(art, site, key="structure"):
    """A document's body walked once into `(structure, toc, rail)`: the rendered
    HTML, the headings collected while rendering it, and the rail the walk filled
    (closed with its document-level sections). The three come back together
    because they are one pass -- the TOC's anchors are the ids the body emitted,
    and a rail whose `add_document` was forgotten silently loses every
    document-level citation panel. `key` names the artifact's body array, which
    is ``structure`` for a document with a formal structure and ``body`` for a
    wiki page's prose."""
    toc = Toc()
    rail = Rail(site, art["uri"])
    structure = Markup("".join(render_node(node, site, art["uri"], toc, rail)
                               for node in art.get(key, [])))
    rail.add_document()
    return structure, toc, rail


# --------------------------------------------------------------------------
# page shells
# --------------------------------------------------------------------------

# the partial macro libraries, exposed as template modules so Python-side
# helpers can render a fragment (a banner, a panel, a rail row) exactly as an
# extending template would
_META = ENV.get_template("partials/meta.html").module
BANNERS = ENV.get_template("partials/banners.html").module
PANELS = ENV.get_template("partials/panels.html").module
_RAIL = ENV.get_template("partials/rail.html").module
NODES = ENV.get_template("nodes.html").module
def page_context(title, kind, meta, *, toc="", eyebrow=None, subtitle=None,
                 summary="", summary_text=None, island="", solo=False,
                 body_class="", head="", own_h1=False, title_html=None,
                 mark=False, **extra):
    """The page-shell context every render goes through (page.html and the
    sources/*.html templates extending it). `meta`/`toc`/`summary`/`island`/
    `head` are pre-rendered HTML and are wrapped as Markup here; `title`/
    `eyebrow`/`subtitle`/`summary_text` are plain text the template escapes.
    `own_h1` says the body brings its own <h1> (the browse pages' listing
    heading), so the frontmatter must not emit a second one. `mark` puts the
    lagen.nu mark in the frontmatter's left margin -- the frontpage, which is
    the site speaking as itself rather than showing a document. `extra` carries a
    source template's own variables (pre-rendered fragments should already be
    Markup)."""
    return dict(title=title, kind=kind, meta=Markup(meta), toc=Markup(toc),
                eyebrow=eyebrow, subtitle=subtitle, summary=Markup(summary),
                summary_text=summary_text, island=Markup(island), solo=solo,
                body_class=body_class, head=Markup(head), own_h1=own_h1,
                mark=mark,
                title_html=Markup(title_html) if title_html is not None else None,
                **extra)


# the site name set as the brand: the frontpage prints its own title, and both
# frontpage builders pass this as `title_html`. It is not derived from the
# title string -- a page that happens to be called "lagen.nu" is not the site
BRAND = Markup("lagen<em>.nu</em>")


def page(title, kind, meta, body, toc="", eyebrow=None, subtitle=None,
         summary="", island="", solo=False, body_class="",
         head="", own_h1=False, title_html=None, mark=False):
    """Assemble a page (templates/page.html: masthead, frontmatter, grid,
    mobile toolbar). Document pages use the 3-column grid (TOC · reading
    column · context rail); `solo` pages (frontpage, browse indexes) drop the
    side columns for a single centered column. `body_class` adds a modifier to
    the <body> (e.g. " expired" for a repealed statute -- subdued reading column
    + a fixed watermark). `summary` (already-wrapped HTML, e.g. a
    `<p class="sammanfattning">`) sits in the frontmatter between the title and
    `meta`, not in the reading column -- pass it instead of prepending to `body`
    when a source wants its abstract to read before the metadata block."""
    return ENV.get_template("page.html").render(page_context(
        title, kind, meta, toc=toc, eyebrow=eyebrow, subtitle=subtitle,
        summary=summary, island=island, solo=solo, body_class=body_class,
        head=head, own_h1=own_h1, title_html=title_html, mark=mark,
        body=Markup(body)))


def prop_link(site, ident):
    """A förarbete identifier from the register, linked when it is a
    proposition we host (the old registerpost linked only propositioner)."""
    m = re.match(r"Prop\. (\d{4}/\d{2,4}):(\S+)$", ident)
    if m:
        uri = BASE + "prop/%s:%s" % (m.group(1), m.group(2))
        if site.has(uri):
            return Markup('<a href="%s">%s</a>') % (layout.page_url(uri),
                                                    ident)
    return Markup(escape(ident))


ANDRINGAR_CAP = 5    # register rows shown expanded in a provision's rail
def footnote_items(footnotes, site, *, backref=True):
    """Artifact footnotes -> template items for the endnote list. Every source
    keys the printed marker as "mark" (a marker need not be a number).

    `backref` says the body carries a matching inline marker to return to. DV's
    does (its source prints "[N]", which the parser turns into a footnote run);
    a letterhead PDF's does not -- poppler renders the superscript glued into
    the prose, so there is nothing to anchor -- and those list with the marker
    the document printed instead of a back-link that would go nowhere."""
    return [{"n": str(fn["mark"]), "html": Markup(render_runs(fn["text"], site)),
             "backref": backref}
            for fn in footnotes]


def versions_panel(base_uri, own_version, versions, *, label=str, notes=None):
    """The compare-lydelser panel every versioned source carries (SFS's
    consolidations, an EU act's consolidated wordings): the <select> versions.js
    turns into the on-demand diff view (``?diff=<version>``, served by
    /api/v1/document/diff), newest first, with the wording being read left out.
    Empty when the wording read is the only one known.

    `versions` is `history.versions`'s (version id, uri) list. `label` spells a
    version id the way the source cites it (SFS prefixes "SFS ", an EU
    consolidation date reads as itself); `notes` annotates an option with what
    the source knows about that version (SFS: the ikraft date and proposition).
    """
    versions = [(v, u) for v, u in versions if v != own_version]
    if not versions:
        return ""
    notes = notes or {}
    return PANELS.versions_panel(
        [{"value": v, "label": label(v), "note": notes.get(v, "")}
         for v, _uri in reversed(versions)],          # newest first
        "denna" if own_version else "aktuell",
        base_uri, own_version or "",
        label(own_version) if own_version else "")


def _meta_dl(pairs):
    """The dl.meta rows (partials/meta.html): a plain value is escaped by the
    macro, an already-rendered one (a linked identifier row) arrives as
    Markup and passes through."""
    return _META.meta_dl([(k, v) for k, v in pairs if v])


def doc_meta(pairs, source_url):
    """The dl.meta block shown under a document's h1: the source's (label, value)
    rows, then the authoritative-source "Källa" link as the last row (C1)."""
    if source_url:
        host = urlsplit(source_url).netloc or "källan"
        pairs = list(pairs) + [("Källa", Markup(
            '<a class="ext" href="%s" rel="external">%s</a>')
            % (source_url, host))]
    return _meta_dl(pairs)


def _doc_title(site, uri):
    row = site.con.execute("SELECT title FROM documents WHERE uri = ?",
                           (uri,)).fetchone()
    return row[0] if row else None


def ref_link(site, uri, name_unknown=None, name=None):
    """A link to a referenced document for a föreskrift's outbound metadata
    (bemyndigande -> SFS paragraf, genomför -> EU directive): the statute
    paragraf pinpointed and named, or the CELEX out to EUR-Lex; a plain span
    for an SFS we have not parsed.

    `name_unknown` names a target the catalog does not hold. Without it such a
    target falls back to `catalog.local`, i.e. its slug -- and a reader told
    that this regulation repeals "rpsfs/2011:16" has been shown the URL, not the
    citation. The caller supplies it because only the source knows how its own
    designations are spelled (lib stays source-agnostic).

    `name` names a target the catalog *does* hold, overriding its title -- the
    calling source's own compact form of a document it links constantly (hudoc
    naming the convention "EKMR" rather than the treaty's full official title).

    The external branch fires only for a target the site does not host: the
    publisher-number namespaces also hold documents we parse and serve (the
    CoE treaties), and short-circuiting on the namespace alone rendered every
    hudoc judgment's
    article references as raw fragment ids ("005#A8") linking out to the
    Treaty Office instead of to our own article anchors."""
    base, _, frag = uri.partition("#")
    known = site.has(base)
    if _is_external(uri) and not known:
        return Markup('<a class="ext" href="%s" rel="external">%s</a>') % (
            _external_href(uri), catalog.local(uri).rsplit("/", 1)[-1])
    pin = human_fragment(frag)
    if known:
        name = (name and name(base)) or margins._law_title(site, base)
    else:
        name = (name_unknown and name_unknown(base)) or margins._law_title(site, base)
    label = ("%s %s" % (pin, name)).strip() if pin else name
    return (Markup('<a href="%s">%s</a>') % (href(uri), label)
            if known
            else Markup('<span class="noref">%s</span>') % label)


def ref_list(site, heading, uris, name_unknown=None, name=None):
    return PANELS.ref_list(heading, [ref_link(site, u, name_unknown, name)
                                     for u in uris or []])
