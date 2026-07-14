"""Render parsed artifacts to a static, interlinked HTML site -- the `generate`
phase (REWRITE.md §6).

Two things make it the derived layer rather than a dumb pretty-printer:

  * outbound links are live -- every inline citation run becomes an <a> to the
    cited document's own page (and exact paragraph), so a case links into the
    statute it cites;
  * inbound links are annotated -- each statute paragraph's context (the cases
    and laws that cite *it*, queried from the catalog) is collected into a JSON
    island and shown in a right-hand rail that the client swaps as you scroll.
    That round-trip (case -> paragraph -> back to every case on that paragraph)
    is the signature lagen.nu feature.

The artifact JSON is the contract: a single generic node walk renders both the
SFS structure tree and the DV body, keyed on each node's `type`. Inbound links
are surfaced at two granularities: per *paragraph* (the scroll-driven context
rail, fed by `Rail`) and per *document* (a panel for citations to the whole law
or case -- the 27% of citations that carry no #fragment, and all case inbound).

A `Site` carries the catalog plus the set of document URIs that actually exist,
so a citation to a document we don't have (yet) renders as plain text rather
than a broken link.
"""

import functools
import hashlib
import json
import re
import sqlite3
import textwrap
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date
from html import escape
from pathlib import Path
from urllib.parse import quote

from fastapi.testclient import TestClient

from ..api import app as api_service
from . import (
    annstore,
    casenaming,
    catalog,
    coe,
    compress,
    datasets,
    eucasenaming,
    facets,
    feeds,
    history,
    lagrum,
    layout,
)
from .catalog import BASE
from .eu_structure import flatten as eurlex_flatten
from .eu_structure import subarticle_key
from .markdown import begrepp_uri
from .text import runs_text
from .util import basefile_slug, split_numalpha

# the browser-facing static chrome (stylesheet, client scripts, robots.txt),
# shipped verbatim by render_aggregates -- real .css/.js files rather than
# embedded strings, so editors and linters see them as what they are
ASSETS = Path(__file__).parent / "assets"


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

    @classmethod
    def from_catalog(cls, con):
        commentary, guidance, article_guidance = _kommentar_indexes(con)
        remiss_feedback, remiss_overall = _remiss_indexes()
        return cls(con, {u for (u,) in con.execute("SELECT uri FROM documents")},
                   catalog.concept_aliases(con),
                   commentary, guidance, article_guidance,
                   remiss_feedback, remiss_overall, _fk_index(con),
                   _graphics_index())

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
        art = json.loads(compress.read_bytes(path))
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


def _fk_index(con):
    """The per-paragraf författningskommentar rail index, from the catalog
    layer forarbete.fk resolved at relate time: {(sfs_uri, anchor):
    [(prop_uri, prop_label, page, text)]}, newest proposition first (the row
    order of `fk_kommentar_all`); anchor None keys a law-level comment."""
    fk = {}
    for sfs_uri, anchor, prop_uri, label, _date, page, text in \
            catalog.fk_kommentar_all(con):
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
        svar = json.loads(compress.read_bytes(path))
        # v1 maps only the first cross-ref, matching ai_analyze.analyze (a remiss
        # almost always sends out exactly one SOU/Ds); cache the referred
        # förarbete's uri so N answers to the same document reopen it once.
        typ, fa_basefile = svar["remitterat"][0]["typ"], svar["remitterat"][0]["basefile"]
        key = (typ, fa_basefile)
        if key not in host_uri:
            fa_path = layout.artifact(
                "forarbete", "%s/%s" % (typ, basefile_slug(fa_basefile)))
            host_uri[key] = json.loads(compress.read_bytes(fa_path))["uri"]
        fa_uri = host_uri[key]

        layer = json.loads(ann.read_text())      # malformed .ann -> JSONDecodeError
        remiss_overall.setdefault(fa_uri, []).append(
            _remiss_item(svar, layer["overall"]))
        for seg in layer["segments"]:
            remiss_feedback.setdefault((fa_uri, seg["forarbete_id"]), []).append(
                _remiss_item(svar, seg))
    return remiss_feedback, remiss_overall


def _graphics_index():
    """{(document_uri, gap_key): entry} of verified graphic crops.

    The host URI is explicit layer metadata, so this horizontal reader neither
    imports nor branches on an SFS vertical. Generated candidates remain out of
    the public render until either the entry or whole layer is verified.
    """
    index = {}
    for path in (p for p in annstore.entries() if p.suffix == ".graphics"):
        layer = json.loads(path.read_text())
        meta = layer.get("meta") or {}
        layer_verified = meta.get("status") == annstore.VERIFIED
        eligible = [(gap_key, entry) for gap_key, entry in layer.items()
                    if (gap_key != "meta" and "page" in entry
                        and (layer_verified or entry.get("verified")))]
        if eligible:
            uri = meta.get("uri")
            assert uri, "%s: publishable graphics layer has no meta.uri" % path
            for gap_key, entry in eligible:
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

def split_uri(uri):
    base, _, frag = uri.partition("#")
    return catalog.local(base), frag


# the uri -> output-path / public-route rule now lives in lib.layout (the single
# home for on-disk and on-web location rules)
doc_relpath = layout.page_relpath


EXT = BASE + "ext/"                          # the "external reference" namespace
CELEX = BASE + "ext/celex/"
COE = BASE + "ext/coe/"
EURLEX = "https://eur-lex.europa.eu/legal-content/SV/TXT/?uri=CELEX:%s"
COE_TREATY = ("https://www.coe.int/en/web/conventions/full-list2"
              "?module=treaty-detail&treatynum=%s")


def is_external(uri):
    """A lagen.nu `ext/` URI identifies a document the site doesn't host
    (EU acts via CELEX, …) -- it resolves to an external service, not a page."""
    return uri.startswith(EXT)


def href(uri):
    if not uri.startswith(BASE):
        return uri  # already-absolute external
    _, frag = split_uri(uri)
    return layout.page_url(uri) + ("#" + frag if frag else "")


def external_href(uri):
    """Where an ``ext/`` reference we don't host resolves -- EUR-Lex for a
    CELEX (the EU act on the official site), else the uri itself."""
    if uri.startswith(CELEX):
        return EURLEX % catalog.local(uri)[len("ext/celex/"):].split("#")[0]
    if uri.startswith(COE):
        return COE_TREATY % catalog.local(uri)[len("ext/coe/"):].split("#")[0]
    return uri


# a minted fragment id decomposes into K(ap)/§/mom/stycke/punkt/mening segments
# (the FRAGMENT_LETTERS scheme); render it the way a lawyer would pinpoint it
FRAG_LABEL = {"K": "kap.", "P": "§", "O": "mom.", "S": "st", "N": "p", "M": "men."}
_FRAG_SEG = re.compile(r"([KPOSNM])([0-9a-zåäö]+)")


def human_fragment(frag):
    """A fragment id -> a human pinpoint: "K2P16S5" -> "2 kap. 16 § 5 st";
    "sid39" -> "s. 39"; change markers ("L1988:187") and unknowns -> ""."""
    if not frag:
        return ""
    if frag.startswith("sid"):
        return "s. " + frag[3:]
    coe = re.fullmatch(
        r"A((?:\d+[A-Za-z]?|[IVXLCDM]+)(?:\.\d+)?)(?:-(\d+))?"
        r"(?:P(\d+)(?:-(\d+))?)?(?:L([a-z])(?:-(\d+))?)?", frag)
    if coe:
        parts = ["artikel %s" % coe.group(1)]
        if coe.group(3):
            parts.append("punkt %s" % coe.group(3))
        if coe.group(5):
            parts.append("led %s" % coe.group(5))
        instance = coe.group(6) or coe.group(4) or coe.group(2)
        if instance:
            parts.append("variant %s" % instance)
        return " ".join(parts)
    segs = _FRAG_SEG.findall(frag)
    return " ".join("%s %s" % (val, FRAG_LABEL[letter]) for letter, val in segs)


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


# inbound panel section order + heading; commentary first (it's the closest
# reading aid to a paragraph), then the machine-extracted sources, then concepts
INBOUND_GROUPS = [("sfs", "Författningar"), ("forarbete", "Förarbeten"),
                  ("foreskrift", "Myndighetsföreskrifter"),
                  ("dv", "Rättsfall"), ("hudoc", "Europadomstolens praxis"),
                  ("eurlex", "EU-rätt"), ("coe", "Europarådets fördrag"),
                  ("begrepp", "Begrepp")]

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


def forarbete_pinpoint(anchor):
    """A förarbete node id -> a human pinpoint: "a14.3" -> "avsnitt 14.3",
    "sid39" -> "s. 39". A generated "sec7" (a heading carrying no section
    number) has no pinpoint; a "-N" clash suffix on the avsnitt id is dropped."""
    if anchor.startswith("sid"):
        return "s. " + anchor[3:]
    if re.match(r"a\d", anchor):
        return "avsnitt " + re.sub(r"-\d+$", "", anchor[1:])
    return ""


def citer_pinpoint(source, anchor):
    """The human pinpoint for a citing document's source anchor: an avsnitt/page
    for a förarbete, a chapter/§ for a statute; other sources cite whole-doc."""
    if not anchor:
        return ""
    if source == "forarbete":
        return forarbete_pinpoint(anchor)
    if source == "sfs":
        return human_fragment(anchor)
    return ""


def citer_name(source, kind, label, title):
    """The preferred display name for a citing/preparatory document. A förarbete
    carries its full title on its number ("Prop. 2025/26:116: En ny funktion …")
    -- a lagrådsremiss by title alone ("Lagrådsremiss: …"), it having no number;
    every other source already stores a human title (the law name, the referat)."""
    if source == "forarbete":
        if kind == "lr":
            return "Lagrådsremiss: %s" % title if title and title != label \
                else "Lagrådsremiss"
        return "%s: %s" % (label, title) if title and title != label else label
    return title or label


def _swedish_join(parts):
    """["a","b","c"] -> "a, b och c" (the last item joined with "och")."""
    if len(parts) <= 1:
        return "".join(parts)
    return ", ".join(parts[:-1]) + " och " + parts[-1]


def _citer_line(row):
    """One collapsed "<li>" for a citing document: its full-title name (linking
    to the document) followed by up to PINPOINT_CAP distinct source pinpoints,
    then " m.fl." if more. Förarbete pinpoints share a category word ("avsnitt
    3, 5 och 7" -- written once, each number linking its own anchor); other
    sources' pinpoints are each rendered whole as a single link."""
    from_uri, label, title, source, kind, _date, anchors = row
    name = '<a href="%s">%s</a>' % (escape(href(from_uri)),
                                    escape(citer_name(source, kind, label, title)))
    pins, seen = [], set()
    for anchor in (anchors.split(",") if anchors else []):
        pin = citer_pinpoint(source, anchor)
        if pin and pin not in seen:        # dedupe on the human pinpoint
            seen.add(pin)
            pins.append((pin, anchor))
    if not pins:
        return "<li>%s</li>" % name
    pins.sort(key=lambda p: split_numalpha(p[0]))
    shown, overflow = pins[:PINPOINT_CAP], len(pins) > PINPOINT_CAP

    def link(anchor, text):
        return '<a href="%s">%s</a>' % (
            escape(href(from_uri + "#" + anchor)), escape(text))

    words = {pin.split(" ")[0] for pin, _ in shown}
    if source == "forarbete" and len(words) == 1 and " " in shown[0][0]:
        word = escape(shown[0][0].split(" ", 1)[0])       # "avsnitt" / "s."
        body = word + " " + _swedish_join(
            [link(a, pin.split(" ", 1)[1]) for pin, a in shown])
    else:
        body = _swedish_join([link(a, pin) for pin, a in shown])
    return "<li>%s, %s%s</li>" % (name, body, " m.fl." if overflow else "")


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

    def add(self, node_id, text, level):
        if not node_id:
            self._n += 1
            node_id = "sec%d" % self._n
        if text.strip():
            self.entries.append((node_id, text, level))
        return node_id


def plain(runs):
    """Heading text for the TOC: inline runs flattened to plain text."""
    return runs_text(runs).strip()


MIN_TOC = 3   # below this many headings a TOC adds clutter, not navigation


def render_toc(toc):
    if len(toc.entries) < MIN_TOC:
        return ""
    items = "".join('<a href="#%s" class="lvl%d">%s</a>'
                    % (escape(anchor), min(level, 3), escape(text))
                    for anchor, text, level in toc.entries)
    return ('<nav class="toc"><div class="toc-h">Innehåll</div>'
            '<div class="toc-list">%s</div></nav>' % items)


# --------------------------------------------------------------------------
# inline runs + inbound annotation
# --------------------------------------------------------------------------

INBOUND_CAP = 40   # max citing docs listed before "+N fler"


def render_runs(runs, site):
    if isinstance(runs, str):
        return escape(runs)
    out = []
    for run in runs:
        if isinstance(run, str):
            out.append(escape(run))
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
            out.append('<a%s href="%s">%s</a>'
                       % (cls, escape(href(uri)), escape(run["text"])))
        elif is_external(uri):
            # an ext/ reference we don't host -- out to the external service
            # (EUR-Lex for a CELEX); becomes a local link once we parse it
            out.append('<a class="ext" href="%s" rel="external">%s</a>'
                       % (escape(external_href(uri)), escape(run["text"])))
        elif uri.startswith(BASE):
            # a lagen.nu document with no page yet -- show the text, not a
            # link that would 404. Becomes live once that doc is parsed.
            out.append('<span class="noref" title="%s">%s</span>'
                       % (escape(catalog.local(uri)), escape(run["text"])))
        else:
            out.append('<a class="ext" href="%s" rel="external">%s</a>'
                       % (escape(uri), escape(run["text"])))
    return "".join(out)


def _inbound_groups(site, uri, exclude_from=(), exclude_before=None):
    """Inbound entries grouped into per-source sections (Författningar /
    Förarbeten / Rättsfall), one collapsed line per citing document (its
    pinpoints listed inline). Förarbeten are ordered prop→sou→ds→lagrådsremiss→
    bet, oldest-first; each group shows PANEL_CAP docs, the rest behind a "+N
    fler" disclosure. `exclude_from` drops citers already shown elsewhere (a
    statute's own preparatory works); `exclude_before` drops citers dated
    before the anchor's beteckning last changed meaning (they refer to the
    provision that carried the label then, and surface on its successor's
    renumbered_refs_margin instead -- undated citers stay). Returns the inner
    HTML, or None when nothing (left) cites `uri`."""
    rows = catalog.inbound_collapsed(site.con, uri, exclude_from)
    if exclude_before:
        rows = [r for r in rows if not (r[5] and r[5] < exclude_before)]
    if not rows:
        return None
    bucket = {}
    for row in rows:
        bucket.setdefault(row[3], []).append(row)   # row[3] = source
    for source, items in bucket.items():
        if source == "forarbete":
            items.sort(key=lambda r: forarb_sort_key(r[4], r[5], r[1]))
        else:
            items.sort(key=lambda r: (r[2] or r[1] or "").lower())
    groups = [(src, heading) for src, heading in INBOUND_GROUPS if src in bucket]
    groups += [(s, s) for s in bucket if s not in dict(INBOUND_GROUPS)]
    html = ""
    for src, heading in groups:
        inner = _capped_list([_citer_line(row) for row in bucket[src]])
        html += ('<div class="ingroup %s"><div class="ingroup-h">%s</div>%s</div>'
                 % (src, escape(heading), inner))
    return html


def _capped_list(lines):
    """A panel's "<li>" lines as a list showing PANEL_CAP items, the rest
    behind a "+N fler" disclosure -- the one home for the panel-cap idiom."""
    inner = "<ul>%s</ul>" % "".join(lines[:PANEL_CAP])
    if len(lines) > PANEL_CAP:
        inner += ('<details class="more"><summary>+%d fler</summary>'
                  '<ul>%s</ul></details>'
                  % (len(lines) - PANEL_CAP, "".join(lines[PANEL_CAP:])))
    return inner


def document_inbound(site, uri, exclude_from=()):
    """Document-level inbound: who cites the law/case/förarbete as a whole
    (the bare uri). Surfaces the citations no paragraph annotation shows.
    `exclude_from` omits citers listed elsewhere (a statute's own förarbeten,
    which get their own preparatory-works section above)."""
    groups = _inbound_groups(site, uri, exclude_from)
    return ('<section class="inbound-doc"><h2>Hänvisat till av</h2>%s</section>'
            % groups) if groups else ""


# a shared FORARBETEN recognizer, built lazily per process, to turn a förarbete
# identifier ("Prop. 2017/18:89") into its document uri -- reusing the citation
# engine's minting instead of a second, drifting parser. namedlaws is irrelevant
# to förarbete numbers, so an empty map suffices.
_FORARB_PARSER = None


@functools.lru_cache(maxsize=None)
def forarbete_identifier_uri(identifier):
    """The document uri a förarbete identifier mints to (prop/sou/ds), or None
    for a form the engine does not host (betänkanden, riksdagsskrivelser).
    The lru_cache is what bounds the reuse: the lazily-built parser is reset and
    run at most once per distinct identifier, never on every call."""
    global _FORARB_PARSER
    if _FORARB_PARSER is None:
        _FORARB_PARSER = lagrum.LagrumParser(
            {}, basefile="0000:000", parse_types=[lagrum.FORARBETEN])
    _FORARB_PARSER.reset()
    refs = _FORARB_PARSER.parse_text(identifier, context={})
    return refs[0].uri if refs else None


def forarbeten_section(site, art):
    """The statute's own preparatory works, top-billed above the citation panel.
    Every förarbete of the grundförfattning and every ändringsförfattning is
    listed once (prop→sou→ds→lagrådsremiss→bet, oldest-first): the ones we host
    link to their page under the preferred full-title label, the rest (a
    betänkande/riksdagsskrivelse we do not host) show as their bare identifier.

    Returns `(html, own_uris)` -- `own_uris` are the hosted förarbete uris,
    excluded from the citation panel so a creating proposition reads as a
    preparatory work here, not as a generic inbound reference below."""
    idents, seen = [], set()
    for amendment in art.get("amendments", []):
        for ident in amendment.get("forarbeten", []):
            if ident not in seen:
                seen.add(ident)
                idents.append(ident)
    entries, own_uris = [], set()
    for ident in idents:
        uri = forarbete_identifier_uri(ident)
        meta = catalog.document_meta(site.con, uri) if uri else None
        if meta and site.has(uri):
            kind, label, title, dt = meta
            own_uris.add(uri)
            html = '<a href="%s">%s</a>' % (
                escape(href(uri)), escape(citer_name("forarbete", kind, label, title)))
        else:                       # unhosted (bet./rskr.) -> bare identifier
            # kind from the identifier prefix ("Bet. …" -> bet) so it still sorts
            # into its precedence block; date unknown, so it trails its dated peers
            kind, label, dt, html = (ident.split(" ")[0].rstrip(".").lower(),
                                     ident, None, escape(ident))
        entries.append((forarb_sort_key(kind, dt, label), html))
    if not entries:
        return "", own_uris
    entries.sort(key=lambda e: e[0])
    lis = "".join("<li>%s</li>" % html for _, html in entries)
    return ('<section class="forarbeten"><h2>Förarbeten</h2><ul>%s</ul></section>'
            % lis), own_uris


def _ext_link(url, label):
    """The `.ext` external-reference anchor markup, shared by every
    out-of-corpus link (EUR-Lex CELEX pages, guidance links, …)."""
    return '<a class="ext" href="%s" rel="external">%s</a>' % (escape(url), escape(label))


def _directive_link(site, directive, target=None):
    """Link to an EU act referenced by `directive`: our own hosted page (at
    `target`, defaulting to the act itself) when we've parsed it, else out to
    EUR-Lex via its CELEX. Shared by the genomför-EU margin (statute paragraf
    -> directive article) and the genomförande section (proposition ->
    directive article) -- both name the directive the same way (its catalogued
    title, falling back to the bare CELEX) and fall back to EUR-Lex
    identically."""
    target = target or directive
    celex = catalog.local(directive).rsplit("/", 1)[-1]
    # the reader-facing short heading ("NIS 2-direktivet"), as the act's own page
    # shows it, keeps the genomför margin compact; fall back to the full official
    # title (unparsed act: no stored heading) then the bare CELEX
    label = (catalog.document_display(site.con, directive)
             or _doc_title(site, directive) or celex)
    if site.has(directive):
        return '<a href="%s">%s</a>' % (escape(href(target)), escape(label))
    return _ext_link(external_href(directive), label)


def genomfor_margin(site, sfs_uri, anchor):
    """Statute-paragraf margin: the EU directive article(s) this paragraf
    transposes (genomför), with the proposition as provenance (§7d). The mirror
    of the directive article's inbound, which shows this statute paragraf."""
    rows = catalog.genomfor_for(site.con, sfs_uri, anchor)
    if not rows:
        return ""
    items = []
    for directive, article, prop_uri, prop_label, pinpoint, partial in rows:
        dlink = _directive_link(site, directive, directive + "#" + article)
        prov = ('<a href="%s">%s</a>' % (escape(href(prop_uri)), escape(prop_label))
                if prop_label and site.has(prop_uri) else escape(prop_label or ""))
        items.append('<li>genomför%s artikel %s i %s%s</li>'
                     % (" delvis" if partial else "", escape(pinpoint or article),
                        dlink, ' <span class="prov">(%s)</span>' % prov if prov else ""))
    return ('<aside class="genomfor"><div class="inbound-h">Genomför EU-rätt</div>'
            '<ul>%s</ul></aside>' % "".join(items))


def bemyndigande_margin(site, uri):
    """Statute-paragraf margin: the agency föreskrifter issued (meddelade) with
    stöd av this paragraf -- the inbound side of the bemyndigande edge, mirror of
    each föreskrift's outbound 'Bemyndigande'. So the paragraf that delegates
    rule-making power lists the regulations made under it. The föreskrift links to
    its own page where present, else shows as text (an fs we have not parsed)."""
    rows = catalog.bemyndigande_inbound(site.con, uri)
    if not rows:
        return ""
    items = []
    for from_uri, label, title in rows:
        name = label or catalog.local(from_uri)
        link = ('<a href="%s">%s</a>' % (escape(href(from_uri)), escape(name))
                if site.has(from_uri) else '<span class="noref">%s</span>'
                % escape(name))
        sub = (' <span class="prov">%s</span>' % escape(title)
               if title and title != name else "")
        items.append("<li>%s%s</li>" % (link, sub))
    return ('<aside class="bemyndigande"><div class="inbound-h">Föreskrifter '
            'meddelade med stöd av denna paragraf</div><ul>%s</ul></aside>'
            % "".join(items))


def _law_title(site, base):
    """A law's display title from the catalog, whitespace-collapsed (SFS titles
    can carry a trailing CR/LF), falling back to its local id."""
    return " ".join((_doc_title(site, base) or catalog.local(base)).split())


def _corr_phrase(relation, scope):
    """How an old paragraf's margin names its successor, from the correspondence's
    relation/scope: "motsvaras numera huvudsakligen av", "har förts över till"."""
    if relation == "overfort":
        return "har förts över till"
    return {"delvis": "motsvaras numera delvis av",
            "i_huvudsak": "motsvaras numera huvudsakligen av",
            "i_sak": "motsvaras numera i sak av"}.get(scope, "motsvaras numera av")


def corresponds_margin(site, uri):
    """Old (repealed) statute paragraf margin: the new-law paragraf that now
    corresponds to this one, from the `.corr` correspondence layer -- "Denna
    paragraf motsvaras numera huvudsakligen av <ny paragraf>". The new side does
    not show the mirror line: that the new paragraf corresponds to the old one is
    already plain from its författningskommentar."""
    # same-law renumbering ('betecknas') edges are not supersessions -- the
    # old beteckning is a live provision today; renumbered_refs_margin's job
    rows = [r for r in catalog.correspondence_for_old(site.con, uri)
            if r[1] != "betecknas"]
    if not rows:
        return ""
    items, seen = [], set()
    for new_uri, relation, scope, _prop, _ikraft in rows:
        if new_uri in seen:        # one line per successor paragraf, not per stycke
            continue
        seen.add(new_uri)
        base = new_uri.split("#")[0]
        label = ("%s %s" % (human_fragment(new_uri.partition("#")[2]),
                            _law_title(site, base))).strip()
        link = ('<a href="%s">%s</a>' % (escape(href(new_uri)), escape(label))
                if site.has(base) else escape(label))
        items.append('<li>Denna paragraf %s %s</li>'
                     % (_corr_phrase(relation, scope), link))
    return ('<aside class="motsvarighet"><div class="inbound-h">Motsvarighet'
            '</div><ul>%s</ul></aside>' % "".join(items))


CORR_DEPTH = 3      # how many re-enactments back the case-law margin reaches


def corresponding_cases_margin(site, uri):
    """New statute paragraf margin: the legal cases (rättsfall) that cite the
    old, repealed provisions this one corresponds to -- one section per
    predecessor, headed "Äldre rättsfall för motsvarande bestämmelse (<the
    predecessor provision, linked>)", so a reader of the new law finds the
    case law decided under it. The correspondence chain is walked
    *transitively* (socialtjänstlagen 2025:400 -> 2001:453 -> 1980:620,
    breadth-first, CORR_DEPTH re-enactments deep): each generation's case law
    cites its own generation's provision. The correspondences are read from
    the `.corr` layers; the cases are the generic inbound on each old
    paragraf, filtered to case law."""
    out, seen = [], set()
    frontier = [uri]
    for _hop in range(CORR_DEPTH):
        nxt = []
        for at in frontier:
            for old_uri, rel, _scope, _prop, _ikraft in \
                    catalog.correspondence_for_new(site.con, at):
                if rel == "betecknas":
                    # same-law renumbering: renumbered_refs_margin's job
                    continue
                if old_uri in seen:  # one section per old paragraf, not per stycke
                    continue
                seen.add(old_uri)
                nxt.append(old_uri)
                rows = [r for r in catalog.inbound(site.con, old_uri,
                                                   limit=INBOUND_CAP + 1)
                        if r[4] == "dv"]
                if not rows:
                    continue
                base = old_uri.split("#")[0]
                old_label = ("%s %s" % (
                    human_fragment(old_uri.partition("#")[2]),
                    _law_title(site, base))).strip()
                cite = ('<a href="%s">%s</a>'
                        % (escape(href(old_uri)), escape(old_label))
                        if site.has(base) else escape(old_label))
                links = "".join(
                    '<li><a href="%s">%s</a></li>'
                    % (escape(href(from_uri + ("#" + a if a else ""))),
                       escape(describe_citer(from_uri, a, label, title, source)))
                    for from_uri, a, label, title, source in rows[:INBOUND_CAP])
                out.append('<div class="rail-sec"><div class="rail-sec-h">'
                           'Äldre rättsfall för motsvarande bestämmelse (%s)'
                           '</div><ul>%s</ul></div>' % (cite, links))
        frontier = nxt
        if not frontier:
            break
    return "".join(out)


def _reassigned_before(site, uri):
    """The date this anchor's beteckning last changed meaning: the newest
    same-law renumbering that gave the label to another provision (a
    'betecknas' edge FROM it). References dated earlier mean the *old*
    provision and must not appear in this anchor's own inbound panel -- they
    surface on the successor's renumbered_refs_margin instead. None when the
    label was never reassigned (or the register lacks the date)."""
    return max((ik for _new, rel, _s, _p, ik in
                catalog.correspondence_for_old(site.con, uri)
                if rel == "betecknas" and ik), default=None)


def renumbered_refs_margin(site, uri):
    """New-beteckning paragraf margin: the references made to this provision
    under its *previous* beteckning(ar), from the same-law 'betecknas'
    correspondence edges (SFSR omfattning): "Hänvisningar till tidigare
    beteckning 4 kap. 4 §" under RF 4 kap. 6 §. A reference to the old label
    counts only when its document predates the renumbering's entry into force
    (and postdates the label's previous reassignment, if any) -- later
    references to that label mean the provision now carrying it.

    Chains of renumberings compose, but each hop must stay on this
    provision's own lineage: the provision arrived at the current label via
    the *latest* 'betecknas' edge strictly before the hop's upper bound, and
    only that edge's old label is a previous beteckning of it. An edge at or
    after the bound describes the label's *next* occupant (RF 2010:1408 moves
    12 kap. -> 13 kap. and 13 kap. -> 15 kap. on the same date: from 15 kap.
    the 13->15 hop must not continue through 12->13, whose references belong
    on the 13 kap. pages). A dateless edge (old registers) cannot be
    interpreted and ends its chain."""
    out = []
    frontier = [(uri, None)]        # (anchor uri, upper date bound so far)
    for _hop in range(CORR_DEPTH):
        nxt = []
        for at, upper in frontier:
            edges = [(old_uri, ikraft) for old_uri, rel, _s, _p, ikraft in
                     catalog.correspondence_for_new(site.con, at)
                     if rel == "betecknas" and ikraft
                     and (upper is None or ikraft < upper)]
            if not edges:
                continue
            # the arrival at this label; ties are one renumbering event
            # mapping several old labels onto it
            arrival = max(ik for _o, ik in edges)
            for old_uri, ikraft in edges:
                if ikraft != arrival:
                    continue        # an earlier occupant's arrival, not ours
                # the label's previous reassignment opens the window
                lower = max((ik for _n, r2, _s2, _p2, ik in
                             catalog.correspondence_for_old(site.con, old_uri)
                             if r2 == "betecknas" and ik and ik < arrival),
                            default=None)
                nxt.append((old_uri, arrival))
                rows = [r for r in catalog.inbound_collapsed(site.con, old_uri)
                        if r[5] and r[5] < arrival
                        and (not lower or r[5] >= lower)]
                if not rows:
                    continue
                label = human_fragment(old_uri.partition("#")[2])
                out.append('<div class="rail-sec"><div class="rail-sec-h">'
                           'Hänvisningar till tidigare beteckning %s '
                           '(före %s)</div>%s</div>'
                           % (escape(label), escape(arrival),
                              _capped_list([_citer_line(r) for r in rows])))
        frontier = nxt
        if not frontier:
            break
    return "".join(out)


def _sentiment_span(sentiment):
    """A compact, self-contained sentiment indicator for the remiss rail: a glyph
    (+ / − / ±) and a sign/magnitude css class the stylesheet colours. A small band
    around zero reads neutral. `sentiment` is a validated numeric score from the
    `.ann` (ai_analyze enforces [-1, 1]), so it is not user-escaped HTML."""
    if sentiment >= 0.15:
        cls, glyph = "sentiment-pos", "+"
    elif sentiment <= -0.15:
        cls, glyph = "sentiment-neg", "−"
    else:
        cls, glyph = "sentiment-neutral", "±"
    return '<span class="sentiment %s">%s</span>' % (cls, glyph)


class Rail:
    """Collects each paragraph's context panel (who cites it, and which EU
    article it transposes) as a document is rendered, keyed by the node's anchor
    id. Serialized to a JSON island the client swaps into the right rail as the
    reader scrolls -- the Gravitas "Kontext för …" rail. The link/href logic
    stays in Python; the client only moves pre-rendered HTML. A node carries a
    ``data-rail`` attribute (see `_rail_attr`) iff it has an entry here, so the
    scrollspy knows which elements drive the rail."""

    def __init__(self, site, doc_uri):
        self.site = site
        self.doc_uri = doc_uri
        self.data = {}

    def add(self, nid, pinpoint="", extra=""):
        """Record node `nid`'s rail panel if it has commentary, anything cites it,
        it transposes an EU article, or it carries an editorial `extra` section
        (the EU article<->recital links). Idempotent per id; no-op for
        context-less nodes."""
        if not nid or nid in self.data:
            return
        uri = self.doc_uri + "#" + nid
        commentary = self._commentary(nid)
        fk = self._fk(nid)
        guidance = self._guidance_html(
            self.site.article_guidance.get((self.doc_uri, nid)))
        remiss = self._remiss_html(
            self.site.remiss_feedback.get((self.doc_uri, nid)))
        groups = _inbound_groups(self.site, uri,
                                 exclude_before=_reassigned_before(self.site, uri))
        genomfor = genomfor_margin(self.site, self.doc_uri, nid)
        bemyndigande = bemyndigande_margin(self.site, uri)        # föreskrifter under it
        corr_cases = corresponding_cases_margin(self.site, uri)   # new-law side
        renumbered = renumbered_refs_margin(self.site, uri)       # earlier beteckning
        corresponds = corresponds_margin(self.site, uri)          # old-law side
        if not (commentary or fk or guidance or remiss or groups or genomfor
                or bemyndigande or extra or corr_cases or renumbered
                or corresponds):
            return
        head = ('<div class="rail-h">Kontext%s</div>'
                % (' för <b>%s</b>' % escape(pinpoint) if pinpoint else ""))
        body = ('<div class="rail-sec"><div class="rail-sec-h">Hänvisat till av</div>'
                '%s</div>' % groups) if groups else ""
        self.data[nid] = (head + commentary + fk + guidance + remiss + body
                          + renumbered + corr_cases + extra + genomfor
                          + bemyndigande + corresponds)

    def add_document(self):
        """The document-level rail panel (key ''), shown when no single paragraph
        is in focus (at the top of the document): the act's curated external links
        (Externa länkar) plus any commentary on the document as a whole. Replaces
        the client's empty-rail placeholder."""
        panel = (self._guidance_html(self.site.guidance.get(self.doc_uri))
                 + self._commentary(None)
                 + self._fk(None)
                 # the "most interesting feedback" for the whole SOU/Ds. v1
                 # deliberately renders every overall stance as-is; a later pass can
                 # rank by |sentiment| to surface only the strongest.
                 + self._remiss_html(self.site.remiss_overall.get(self.doc_uri)))
        if panel:
            self.data[""] = '<div class="rail-h">Om dokumentet</div>' + panel

    def _remiss_html(self, items):
        """Remiss (referral) feedback on a node -- what each answering organisation
        said about this section (or, in `add_document`, the SOU/Ds as a whole),
        from the `.ann` sentiment layer -- as a rail section; '' for no items.
        Render-only: the remiss corpus has no page of its own, so each item links
        out to the organisation's own answer PDF (`source_url`, a "Källa" link,
        always `rel="external"` -- a remiss PDF is never a BASE-prefixed internal
        url). Everything shown (organisation, quote) is PDF/LLM-derived and
        `html.escape`d, exactly like `_guidance_html`."""
        if not items:
            return ""
        out = []
        for it in items:
            out.append(
                '<li><span class="remiss-org">%s</span> %s '
                '<span class="q">”%s”</span> '
                '<a href="%s" rel="external">Läs remissvaret</a></li>'
                % (escape(it["organisation"]), _sentiment_span(it["sentiment"]),
                   escape(it["quote"]), escape(it["source_url"])))
        return ('<div class="rail-sec remiss"><div class="rail-sec-h">Remissvar'
                '</div><ul>%s</ul></div>' % "".join(out))

    def _guidance_html(self, items):
        """A list of curated external links -- the wiki annotation's `## Externa
        länkar` block (Commission FAQs, guidance PDFs, call-for-evidence pages, …) --
        as a rail section, used both for the act's document-level panel (Step 2) and
        for a single article's context panel (Step 3); '' for no items. Render-only:
        these resources live outside the corpus, so they carry no inbound edge. A
        lagen.nu-absolute href renders internal, any other an external link."""
        if not items:
            return ""
        out = []
        for g in items:
            ext = "" if g["href"].startswith(BASE) else ' rel="external"'
            # a guidance link carries either a `desc` (the guidance section's own
            # text, e.g. the FAQ question -- shown after the link as ": ...") or a
            # `note` (provenance for a hand-curated link -- shown as "— ...")
            if g.get("desc"):
                tail = ': <span class="q">%s</span>' % escape(g["desc"])
            elif g.get("note"):
                tail = ' <span class="prov">— %s</span>' % escape(g["note"])
            else:
                tail = ""
            out.append('<li><a href="%s"%s>%s</a>%s</li>'
                       % (escape(href(g["href"])), ext, escape(g["label"]), tail))
        return ('<div class="rail-sec vagledning"><div class="rail-sec-h">Externa '
                'länkar</div><ul>%s</ul></div>' % "".join(out))

    def _fk(self, nid):
        """The författningskommentar prose propositioner wrote for the paragraph
        `nid` (or None for the law as a whole), as a rail section: each prop's
        comment opens the section (initial text, ellipsized on a word boundary),
        with the proposition as a provenance link pinpointing the FK page. The
        official sibling of the wiki `_commentary` -- authored by the
        lagstiftare, not our editors -- so it renders as its own section."""
        entries = self.site.fk.get((self.doc_uri, nid))
        if not entries:
            return ""
        out = []
        for prop_uri, label, page, text in entries:
            lead = textwrap.shorten(text.split("\n")[0], 300, placeholder=" …")
            target = prop_uri + ("#sid%d" % page if page else "")
            src = ('<a href="%s">%s</a>' % (escape(href(target)), escape(label))
                   if label and self.site.has(prop_uri)
                   else escape(label or ""))
            out.append('<p>%s <span class="prov">— %s</span></p>'
                       % (escape(lead), src))
        return ('<div class="rail-sec rail-fk"><div class="rail-sec-h">'
                'Författningskommentar</div>%s</div>' % "".join(out))

    def _commentary(self, nid):
        """The wiki commentary for the paragraph `nid` (or `None` for the law as a
        whole), rendered as a rail section (its prose + author byline) -- shown
        side-by-side with what it comments on, in place of a separate kommentar
        page."""
        entries = self.site.commentary.get((self.doc_uri, nid))
        if not entries:
            return ""
        out = []
        for author, blocks in entries:
            prose = "".join("<p>%s</p>" % render_runs(c["text"], self.site)
                            for c in blocks if c.get("text"))
            by = '<div class="komm-by">— %s</div>' % escape(author) if author else ""
            out.append(prose + by)
        return ('<div class="rail-sec rail-komm"><div class="rail-sec-h">Kommentar'
                '</div>%s</div>' % "".join(out))

    def island(self):
        """The ``<script type=application/json>`` island, or '' if no paragraph
        has context. ``</`` is escaped so the payload can't break out of the
        surrounding HTML."""
        if not self.data:
            return ""
        payload = json.dumps(self.data, ensure_ascii=False).replace("</", "<\\/")
        return ('<script type="application/json" id="lagen-context">%s</script>'
                % payload)


def _rail_attr(rail, nid):
    """`data-rail="id"` for a node the rail has context for, else ''."""
    return ' data-rail="%s"' % escape(nid) if nid and nid in rail.data else ""


# --------------------------------------------------------------------------
# generic node renderer (artifact type -> HTML)
# --------------------------------------------------------------------------

def _id_attr(nid):
    return ' id="%s"' % escape(nid) if nid else ""


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
    "forteckning": "Förteckning", "tabell": "Tabell", "vagmarke": "Vägmärke"}


def _grafik_crop(entry, doc_uri, gap_key, alt):
    """The `<img>` for one located graphic: the /api/v1/sfs-graphic crop of the
    provenance-correct published PDF (geometry lives server-side in the layer,
    so the src is just uri+node), lazily loaded. `v` hashes source, page and bbox
    so every content-changing re-verification gets a fresh immutable URL."""
    versioned = {k: entry.get(k) for k in ("sfs", "page", "bbox")}
    ver = hashlib.sha256(json.dumps(versioned, sort_keys=True).encode()).hexdigest()[:12]
    src = "/api/v1/sfs-graphic?uri=%s&node=%s&v=%s" % (
        quote(doc_uri, safe=""), quote(gap_key, safe=""),
        quote(ver, safe=""))
    return '<img class="grafik-img" src="%s" alt="%s" loading="lazy">' % (
        escape(src), escape(alt))


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
        return ('<p class="grafik-saknas" data-grafik="%s">%s saknas i den '
                'konsoliderade texten — se %s</p>'
                % (escape(nid), escape(label), escape(where)))
    alt = entry.get("alt") or ("%s ur SFS %s" % (label, entry["sfs"]))
    return ('<figure class="grafik" data-grafik="%s">%s<figcaption>%s ur '
            '<a href="/%s">SFS %s</a></figcaption></figure>'
            % (escape(nid), _grafik_crop(entry, doc_uri, nid, alt),
               escape(label), escape(entry["sfs"]), escape(entry["sfs"])))


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
    content = render_runs(version.get("text", []), site)
    content = "<%s>%s</%s>" % (tag, content, tag)
    return '<div class="konvention-cell" lang="%s">%s</div>' % (
        escape(version["language"]), content)


def _convention_row(node, site, css, tag, languages):
    versions = _parallel_versions(node)
    return '<div class="konvention-row %s">%s</div>' % (
        css, "".join(_convention_cell(versions[language], site, tag)
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
        provisions.append('<section class="%s"%s>%s%s</section>' % (
            css, _id_attr(child.get("id")), heading, paragraphs))
    return '<section class="konvention-instrument"%s>%s%s</section>' % (
        _id_attr(node.get("id")), title + ingress, "".join(provisions))


def render_konventionsbilaga(node, site, doc_uri, toc, rail):
    languages = node.get("languages")
    assert languages, "convention appendix must declare its languages"
    language_head = '<div class="konvention-languages">%s</div>' % "".join(
        '<div lang="%s">%s</div>' % (language, LANGUAGE_LABELS.get(language, language))
        for language in languages)
    instruments = "".join(
        _render_konventionsinstrument(child, site, toc, languages)
        for child in node.get("children", []))
    return '<div class="konventionsbilaga" style="--n-languages: %d">%s%s</div>' % (
        len(languages), language_head, instruments)


def render_node(node, site, doc_uri, toc, rail, drop_marker=False):
    t = node.get("type")
    nid = node.get("id")

    if t == "konventionsbilaga":
        return render_konventionsbilaga(node, site, doc_uri, toc, rail)

    if t == "tabell":
        rows = "".join(render_node(c, site, doc_uri, toc, rail)
                       for c in node.get("children", []))
        return "<table>%s</table>" % rows
    if t == "rad":
        cells = "".join("<td>%s</td>" % render_runs(c, site)
                        for c in node.get("cells", []))
        g = node.get("grafik")
        if g:  # a dropped road-sign image (2007:90): the sign beside its code
            gid = g.get("key") or g.get("id", "")
            entry = site.graphics.get((doc_uri, gid))
            if entry:
                alt = entry.get("alt") or ("Vägmärke %s" % g.get("code", ""))
                cells = ('<td class="grafik" data-grafik="%s">%s</td>'
                         % (escape(gid),
                            _grafik_crop(entry, doc_uri, gid, alt)) + cells)
            else:  # unlocalized: the honest gap beside the code
                cells = ('<td class="grafik-saknas" data-grafik="%s">[%s]</td>'
                         % (escape(gid), escape(g.get("code", ""))) + cells)
        return "<tr>%s</tr>" % cells
    if t == "grafik":
        return render_grafik(node, site, doc_uri)
    if t == "lista":
        items = "".join(render_node(c, site, doc_uri, toc, rail)
                        for c in node.get("children", []))
        return "<ul>%s</ul>" % items
    if t == "rubrik":
        text = node.get("text", [])
        anchor = toc.add(nid, plain(text), node.get("level") or 1)
        lvl = min(node.get("level") or 2, 5) + 1
        return '<h%d id="%s" class="rubrik">%s</h%d>' % (
            lvl, escape(anchor), render_runs(text, site), lvl)

    # the node's context (who cites it + which EU article it transposes) is
    # routed to the scroll-driven rail, not floated inline; the element is tagged
    # data-rail so the client knows it drives the rail. Leaf rubrik/tabell/rad/
    # lista nodes above carry no context.
    rail.add(nid, human_fragment(nid))
    ra = _rail_attr(rail, nid)

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
        num = ('<span class="num">%s</span>%s'
               % (escape(str(marker)), "" if is_listitem else " ")
               if marker else "")
        tag = "li" if is_listitem else "p"
        open_html = "<%s%s%s>%s%s" % (tag, _id_attr(nid), ra, num,
                                      render_runs(node["text"], site))
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
        # a sub-list nests inside its list item (<li>…<ol>…</ol></li>); a stycke's
        # list follows the closed paragraph (<p>…</p><ol>…</ol>)
        return ("%s%s</%s>" % (open_html, inner, tag) if is_listitem
                else "%s</%s>%s" % (open_html, tag, inner))

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
        if title is not None and plain(title.get("text", [])):
            # anchor the heading at the container's id (or a minted secN when the
            # container is id-less) and point the TOC there; capturing toc.add's
            # return keeps the heading id and the TOC anchor in lockstep, as the
            # rubrik branch does -- _id_attr(nid) alone would emit no id for an
            # id-less container while the TOC still linked its minted secN anchor
            anchor = toc.add(nid, plain(title.get("text", [])), 1)
            head = '<h2 id="%s" class="kaprubrik">%s</h2>' % (
                escape(anchor),
                render_runs(_strip_self_ref(title.get("text", []), nid), site))
            body = kids[1:]
        else:
            # no usable title (empty rubrik) -- keep the bare designator heading
            head = '<h2%s class="kaprubrik">%s</h2>' % (
                _id_attr(nid),
                escape(("%s %s" % (node.get("ordinal", ""), label)).strip()))
            body = kids[1:] if title is not None else kids
        children = "".join(render_node(c, site, doc_uri, toc, rail) for c in body)
        return '<section class="%s"%s>%s%s</section>' % (t, ra, head, children)

    if t == "paragraf":
        # hanging §-numeral in the gutter; the first stycke drops its inline number
        kids = node.get("children", [])
        children = "".join(
            render_node(c, site, doc_uri, toc, rail,
                        drop_marker=(i == 0 and c.get("type") == "stycke"))
            for i, c in enumerate(kids))
        # the §-symbol belongs with the numeral ("1 §") in the gutter; the
        # permalink anchor keeps its own (pilcrow) glyph
        ordinal = node.get("ordinal", "")
        gutter = ('<div class="paragraf-gutter"><span class="n">%s</span>'
                  '<a class="pilcrow" href="#%s" aria-label="Permalänk">¶</a></div>'
                  % (escape("%s §" % ordinal if ordinal else "§"), escape(nid or "")))
        return ('<section class="paragraf"%s%s>%s<div class="paragraf-body">%s</div>'
                '</section>' % (_id_attr(nid), ra, gutter, children))

    children = "".join(render_node(c, site, doc_uri, toc, rail)
                       for c in node.get("children", []))
    return '<section class="%s"%s%s>%s</section>' % (t or "node", _id_attr(nid),
                                                     ra, children)


# --------------------------------------------------------------------------
# page shells
# --------------------------------------------------------------------------

PAGE = """<!doctype html>
<html lang="sv"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>%(title)s</title>
%(head)s
<script>(function(){try{var t=localStorage.getItem('theme');if(t==='light'||t==='dark')document.documentElement.setAttribute('data-theme',t);}catch(e){}})();</script>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=Source+Serif+4:ital,wght@0,400;0,500;0,600;1,400;1,500&display=swap">
<link rel="stylesheet" href="/style.css">
</head><body class="gr-root%(body_class)s">
%(masthead)s
%(grid)s
%(island)s<script src="/script.js" defer></script>
<script>(function(){var b=document.querySelector('[data-theme-toggle]');if(!b)return;b.addEventListener('click',function(){var cur=document.documentElement.getAttribute('data-theme');if(cur!=='light'&&cur!=='dark')cur=matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light';var next=cur==='dark'?'light':'dark';document.documentElement.setAttribute('data-theme',next);try{localStorage.setItem('theme',next);}catch(e){}});})();</script>
</body></html>
"""

# masthead nav: label, browse route, the page kinds that mark it current
MAST_NAV = (("Lagar", "/sfs/", ("Författning",)),
            ("Rättsfall", "/dom/", ("Rättsfall",)),
            ("Förarbeten", "/forarbete/", ("Proposition", "SOU", "Ds",
             "Kommittédirektiv", "Förordningsmotiv", "Skrivelse", "Lagrådsremiss",
             "Sveriges internationella överenskommelser", "Förarbete")),
            ("Föreskrifter", "/foreskrift/", ("Föreskrift",)),
            ("EU-rätt", "/eurlex/", ("EU-förordning", "EU-direktiv", "EU-beslut",
             "EU-domstolen", "Fördrag", "EU-rättsakt")),
            ("Folkrätt", "/folkratt/", ("Folkrätt", "Europarådets fördrag",
             "Europadomstolen")),
            ("Om", "/om/", ("Om",)),
            ("Nyheter", "/dataset/sitenews/feed/", ("Nyheter",)))


# the magnifier icon, shared by the masthead search button and the mobile
# bar's Sök button (different sizes) so the glyph can't drift between them
def _search_icon(size):
    return ('<svg width="%d" height="%d" viewBox="0 0 16 16" fill="none" '
            'stroke="currentColor" stroke-width="1.5" aria-hidden="true">'
            '<circle cx="7" cy="7" r="5"></circle><path d="M11 11l4 4"></path>'
            '</svg>' % (size, size))


def _masthead(kind):
    links = "".join('<a href="%s"%s>%s</a>'
                    % (route, ' class="on"' if kind in act else "", label)
                    for label, route, act in MAST_NAV)
    return ('<header class="masthead">'
            '<a class="brand" href="/">lagen<em>.nu</em></a>'
            '<button class="search" type="button" data-search>'
            + _search_icon(15) +
            '<span>Sök lag, paragraf, rättsfall…</span>'
            '<span class="k">⌘K</span></button>'
            '<nav class="mast-nav">%s</nav>'
            '<button class="theme-toggle" type="button" data-theme-toggle '
            'aria-label="Växla mellan ljust och mörkt tema" '
            'title="Växla ljust / mörkt tema">'
            '<svg class="icon-moon" width="17" height="17" viewBox="0 0 24 24" '
            'fill="none" stroke="currentColor" stroke-width="1.8" '
            'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
            '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"></path></svg>'
            '<svg class="icon-sun" width="17" height="17" viewBox="0 0 24 24" '
            'fill="none" stroke="currentColor" stroke-width="1.8" '
            'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
            '<circle cx="12" cy="12" r="4"></circle>'
            '<path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4'
            'M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"></path></svg>'
            '</button></header>' % links)


# the mobile bottom toolbar (document pages only): thumb-reach access to the
# TOC drawer, the search palette and the context-rail sheet. A sibling of
# .gr-body, not a child -- popover.js imports .gr-body into split-view panes,
# and the toolbar must not ride along. display:none on desktop (style.css).
# The TOC button (MOBILE_BAR_TOC) only appears when the page has TOC entries;
# an "Innehåll" that opens an empty drawer is worse than no button.
MOBILE_BAR_TOC = (
    '<button type="button" data-drawer="toc" aria-expanded="false">'
    '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" '
    'stroke="currentColor" stroke-width="1.8" stroke-linecap="round" '
    'aria-hidden="true"><path d="M4 6h16M4 12h16M4 18h10"></path></svg>'
    'Innehåll</button>')
MOBILE_BAR = (
    '<nav class="mobile-bar" aria-label="Verktyg">'
    '%s'
    '<button type="button" data-search>' + _search_icon(18) + 'Sök</button>'
    '<button type="button" data-drawer="rail" aria-expanded="false">'
    '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" '
    'stroke="currentColor" stroke-width="1.8" stroke-linecap="round" '
    'stroke-linejoin="round" aria-hidden="true">'
    '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z">'
    '</path></svg>'
    'Kontext</button></nav>')


def _source_link(source_url):
    """The document's authoritative-source ("Källa") link -- the publisher's own
    page for it, stamped onto the artifact by build.write_artifact. Absent for
    documents with no known source url."""
    return ('<p class="kalla"><a class="ext" href="%s" rel="external">Källa'
            '</a></p>' % escape(source_url)) if source_url else ""


def _frontmatter(eyebrow, title, subtitle, summary, meta, source_url=None):
    eb = '<div class="eyebrow">%s</div>' % escape(eyebrow) if eyebrow else ""
    sub = '<p class="subtitle">%s</p>' % escape(subtitle) if subtitle else ""
    return ('<header class="frontmatter">%s<h1>%s</h1>%s%s%s%s</header>'
            % (eb, escape(title), sub, summary, meta, _source_link(source_url)))


def page(title, kind, meta, body, toc="", eyebrow=None, subtitle=None,
         summary="", island="", solo=False, source_url=None, body_class="",
         head=""):
    """Assemble a page. Document pages use the 3-column grid (TOC · reading
    column · context rail); `solo` pages (frontpage, browse indexes) drop the
    side columns for a single centered column. `body_class` adds a modifier to
    the <body> (e.g. " expired" for a repealed statute -- subdued reading column
    + a fixed watermark). `summary` (already-wrapped HTML, e.g. a
    `<p class="sammanfattning">`) sits in the frontmatter between the title and
    `meta`, not in the reading column -- pass it instead of prepending to `body`
    when a source wants its abstract to read before the metadata block."""
    front = _frontmatter(eyebrow, title, subtitle, summary, meta, source_url)
    if solo:
        grid = ('<div class="gr-body solo"><main class="gr-main">%s%s</main></div>'
                % (front, body))
    else:
        grid = ('<div class="gr-body"><aside class="toc-col">%s</aside>'
                '<main class="gr-main">%s%s</main>'
                '<aside class="rail" id="rail" aria-live="polite"></aside></div>'
                % (toc, front, body)) + MOBILE_BAR % (MOBILE_BAR_TOC if toc else "")
    return PAGE % {"title": escape(title), "masthead": _masthead(kind),
                   "grid": grid, "island": island, "body_class": body_class,
                   "head": head}


def render_search_page():
    """Static shell for the complete, API-backed result list at ``/sok/``."""
    body = (
        '<div class="search-page">'
        '<form class="full-search-form" role="search">'
        '<input type="search" name="q" autocomplete="off" '
        'placeholder="Sök lag, paragraf, rättsfall…" aria-label="Sökord">'
        '<button type="submit">Sök</button></form>'
        '<div class="full-search-status" role="status" aria-live="polite"></div>'
        '<div class="full-search-layout">'
        '<aside class="full-search-facets" aria-label="Avgränsa sökningen"></aside>'
        '<section><div class="full-search-results" aria-live="polite"></div>'
        '<nav class="search-pagination" aria-label="Sökresultatsidor"></nav>'
        '</section></div></div>')
    return page("Sök", "Sök", "", body, solo=True)


def render_admin_page():
    """Static shell for the editor login at ``/admin/``. The sign-in affordance
    lives here, not in the masthead -- editor.js mounts the credential form (or,
    when a session is already live, the logout control) into ``[data-admin-login]``,
    so an anonymous reader's chrome carries no login link."""
    body = ('<div class="admin-panel" data-admin-login>'
            '<p class="empty">Läser in…</p></div>')
    return page("Logga in", "Admin", "", body, solo=True,
                eyebrow="Redaktörsinloggning")


def render_feed_page(item, entries, params=None):
    """Human-readable twin of an Atom document at the legacy ``/feed`` URL."""
    atom = feeds.feed_url(item.alias, atom=True, params=params)
    listing = "".join(
        '<article class="news-item"><p class="news-date">%s</p>'
        '<h2><a href="%s">%s</a></h2><p>%s</p></article>'
        % (escape(entry.published[:10]), escape(entry.url), escape(entry.title),
           escape(entry.summary))
        for entry in entries)
    body = ('<p class="feed-link"><a href="%s">Atom-flöde</a></p>%s'
            % (escape(atom), listing or '<p class="empty">Inga dokument.</p>'))
    discovery = '<link rel="alternate" type="application/atom+xml" href="%s">' \
        % escape(atom)
    return page(item.title, "Nyheter", "", body, solo=True, head=discovery)


def _feed_index_groups(con):
    """The legacy all-feeds directory, reshaped from the current catalog."""
    groups = [("Nyheter", [("Nyheter om webbtjänsten", "sitenews", {})])]
    groups.append(("Lagar", [
        ("Alla förordningar", "sfs", {"rdf_type": "type/forordning"}),
        ("Alla lagar", "sfs", {"rdf_type": "type/lag"}),
        ("Alla författningar", "sfs", {}),
    ]))

    dv = []
    if catalog.document_count(con, "dv"):
        tree = facets.tree(con, "dv")
        dv = [("Rättsfall från %s" % bucket["label"], "dv",
               {"rpubl_rattsfallspublikation": bucket["key"]})
              for bucket in tree["buckets"]]
    dv.append(("Samtliga rättsfall", "dv", {}))
    groups.append(("Rättsfall", dv))

    type_labels = {"prop": "Alla propositioner", "sou": "Alla SOU",
                   "ds": "Alla Ds", "dir": "Alla kommittédirektiv",
                   "skr": "Alla skrivelser", "lr": "Alla lagrådsremisser",
                   "fm": "Alla förordningsmotiv", "so": "Alla SÖ"}
    kinds = [row[0] for row in con.execute(
        "SELECT DISTINCT kind FROM documents WHERE source = 'forarbete' ORDER BY kind")]
    fa = [(type_labels.get(kind, "Alla %s" % kind), "forarbeten",
           {"rdf_type": "type/" + kind}) for kind in kinds]
    fa.append(("Samtliga förarbeten", "forarbeten", {}))
    groups.append(("Förarbeten", fa))

    publishers = [("Författningar utgivna av %s" % label, "myndfs",
                   {"dcterms_publisher": "publisher/" + slug})
                  for slug, label, _count in feeds.publisher_options(con)]
    publishers.append(("Samtliga föreskrifter", "myndfs", {}))
    groups.append(("Föreskrifter", publishers))

    avg_labels = {"arn": "Allmänna reklamationsnämnden",
                  "jk": "Justitiekanslern", "jo": "Riksdagens ombudsmän"}
    organs = [row[0] for row in con.execute(
        "SELECT DISTINCT kind FROM documents WHERE source = 'avg' ORDER BY kind")]
    praxis = [("Dokument publicerade av %s" % avg_labels.get(kind, kind),
               "myndprax", {"dcterms_publisher": "publisher/" + kind})
              for kind in organs]
    praxis.append(("Samtliga dokument", "myndprax", {}))
    groups.append(("Praxis", praxis))

    groups.append(("EU-rätt", [("Samtliga EU-rättsakter", "eurlex", {})]))
    groups.append(("Begrepp", [("Alla nya och ändrade begrepp", "keyword", {})]))
    return groups


def render_feed_index(con):
    groups = []
    for heading, items in _feed_index_groups(con):
        links = []
        for label, alias, params in items:
            atom = feeds.feed_url(alias, atom=True, params=params).removeprefix(feeds.BASE)
            html = feeds.feed_url(alias, params=params).removeprefix(feeds.BASE)
            links.append('<li><a class="feed-atom" href="%s" '
                         'aria-label="Atom-flöde: %s">Atom</a> '
                         '<a href="%s">%s</a></li>'
                         % (escape(atom), escape(label), escape(html), escape(label)))
        groups.append('<section class="browse-group"><h2>%s</h2><ul>%s</ul></section>'
                      % (escape(heading), "".join(links)))
    return page("Alla nyhetsflöden", "Nyheter", "", "".join(groups), solo=True)


def _expired_banner(props):
    """The repeal callout for a statute whose repeal has taken effect: the repeal
    date and, when known, a link to the repealing act. Paired with the
    `body.expired` treatment (subdued reading column + a fixed 'Upphävd
    författning' watermark) so the status stays visible even when an anchor link
    jumps deep past the heading."""
    when = props.get("rpubl:upphavandedatum")
    av = props.get("rinfoex:upphavdAv")
    detail = ("Upphörde att gälla %s" % escape(when)) if when else "Upphävd"
    if av:
        detail += ' genom <a href="%s">SFS %s</a>' % (
            escape(layout.page_url(av)), escape(catalog.local(av)))
    return ('<div class="expired-banner"><strong>Upphävd författning</strong>'
            '<span>%s.</span></div>' % detail)


def _version_banner(base_id, version):
    """The callout on a historical-consolidation ("lydelse") page: which
    cutoff it shows, and the way back to the law as it reads today."""
    return ('<div class="version-banner"><strong>Äldre lydelse</strong>'
            '<span>Författningen i dess lydelse t.o.m. ändringar genom '
            'SFS %s. <a href="%s">Visa gällande lydelse</a>.</span></div>'
            % (escape(version), escape(layout.page_url(BASE + base_id))))


def _version_notes(art):
    """version id -> "i kraft <date> · Prop. …" annotation for the compare
    panel, from the amendment register (lib.history's join, reduced to the
    display string)."""
    return {v: " · ".join(
                n for n in ((("i kraft %s" % ikraft) if ikraft else None),
                            next((f for f in forarbeten
                                  if f.startswith("Prop")), None)) if n)
            for v, (ikraft, forarbeten) in history.amendment_info(art).items()}


def _versions_panel(art, base_id, own_version, versions):
    """The compare panel (the old pipeline's docversions dropdown): the
    <select> that versions.js turns into the on-demand diff view
    (?diff=<version>, served by /api/v1/document/diff), annotated with each
    consolidation's ikraft date + proposition where the register knows them.
    Point-in-time links live in the andringar view (see _andringar); here is
    only the comparison affordance. Empty when this very consolidation is the
    only one known."""
    versions = [(v, u) for v, u in versions if v != own_version]
    if not versions:
        return ""
    notes = _version_notes(art)
    options = []
    for v, _vuri in reversed(versions):               # newest first
        note = notes.get(v, "")
        options.append('<option value="%s">SFS %s%s</option>'
                       % (escape(v), escape(v),
                          escape(" (%s)" % note) if note else ""))
    return ('<details class="lydelser">'
            '<summary>Jämför lydelser <span class="count">%d</span></summary>'
            '<label>Jämför %s lydelse med <select data-diff data-uri="%s" '
            'data-to="%s"><option value="">– välj lydelse –</option>%s'
            '</select></label></details>'
            % (len(options), "denna" if own_version else "aktuell",
               escape(BASE + base_id), escape(own_version or ""),
               "".join(options)))


def _act_source_links(nr):
    """The change act's own authoritative publication, by era (the old
    registerpost rules): print PDFs at rkrattsdb.gov.se for SFS 1998:306 --
    2018:159, the official svenskforfattningssamling.se version from 2018:160
    on, nothing digitized before that."""
    year, _, lop = nr.partition(":")
    if not (year.isdigit() and lop.isdigit()):
        return ""
    y, n = int(year), int(lop)
    if (y, n) >= (2018, 160):
        return ('<li><a class="ext" rel="external" href="https://'
                'svenskforfattningssamling.se/doc/%d%d.html">'
                'Officiell autentisk version</a></li>' % (y, n))
    if (y, n) >= (1998, 306):
        return ('<li><a class="ext" rel="external" href="https://'
                'rkrattsdb.gov.se/SFSdoc/%02d/%02d%04d.PDF">'
                'Tryckt format (PDF)</a></li>' % (y % 100, y % 100, n))
    return ""


def _prop_link(site, ident):
    """A förarbete identifier from the register, linked when it is a
    proposition we host (the old registerpost linked only propositioner)."""
    m = re.match(r"Prop\. (\d{4}/\d{2,4}):(\S+)$", ident)
    if m:
        uri = BASE + "prop/%s:%s" % (m.group(1), m.group(2))
        if site.has(uri):
            return '<a href="%s">%s</a>' % (escape(layout.page_url(uri)),
                                            escape(ident))
    return escape(ident)


def _andringar(art, base_id, own_version, versions, site, toc, rail):
    """The bottom-of-page register view (the old pipeline's div.andringar):
    one section per register post -- the base act first, then every change
    act -- with the act's own publication links, the point-in-time
    "Konsoliderad version med ändringar införda till och med SFS X" link where
    that consolidation is parsed, a diff link against the previous available
    consolidation (the amendment as a single change), its
    övergångsbestämmelser, and the register details (förarbeten, omfattning,
    CELEX, ikraftträdande)."""
    amendments = art.get("amendments") or []
    if not amendments:
        return ""
    have = dict(versions)                       # version id -> lydelse uri
    order = [v for v, _ in versions]            # oldest first
    # the current consolidation's own cutoff: its amendment's diff target is
    # the *current* page (that snapshot is not archived -- it is the document)
    m = re.search(r" i lydelse enligt SFS (.+)$",
                  art.get("metadata", {}).get("properties", {})
                     .get("dcterms:identifier", ""))
    cutoff = m.group(1) if m else None
    doc_uri = art["uri"]
    posts = []
    for i, am in enumerate(amendments):
        p = am.get("properties", {})
        ident = p.get("dcterms:identifier", "")
        nr = ident[4:] if ident.startswith("SFS ") else None
        heading = ("Ändring, %s" % ident) if i and ident else (ident or "Ändring")
        links = []
        prev = view_url = None
        if nr:
            links.append(_act_source_links(nr))
            if nr in have:
                view_url = layout.page_url(have[nr])
                if nr != own_version:
                    links.append('<li><a href="%s">Konsoliderad version med '
                                 'ändringar införda till och med SFS %s</a></li>'
                                 % (escape(view_url), escape(nr)))
                idx = order.index(nr)
                prev = order[idx - 1] if idx else None
            elif nr == cutoff and order:
                # the newest amendment: its consolidation is the current text
                view_url = layout.page_url(BASE + base_id)
                prev = order[-1]
        if prev and view_url:
            links.append('<li><a href="%s?diff=%s">Visa ändringarna (jämfört '
                         'med lydelsen enligt SFS %s)</a></li>'
                         % (escape(view_url), escape(prev), escape(prev)))
        content = "".join(render_node(c, site, doc_uri, toc, rail)
                          for c in am.get("content", []))
        if content:
            content = "<h3>Övergångsbestämmelse</h3>" + content
        celex = p.get("rpubl:celexNummer", [])
        rows = [
            ("Förarbeten", ", ".join(_prop_link(site, f)
                                     for f in am.get("forarbeten", []))),
            ("Omfattning", escape(p["rpubl:andrar"])
             if p.get("rpubl:andrar") else ""),
            ("CELEX-nr", " ".join(
                _ext_link(EURLEX % c, c)
                for c in ([celex] if isinstance(celex, str) else celex))),
            ("Ikraftträder", escape(p["rpubl:ikrafttradandedatum"])
             if p.get("rpubl:ikrafttradandedatum") else ""),
        ]
        # values above are already escaped/markup, so not _meta_dl (which
        # escapes wholesale)
        details = "".join("<dt>%s</dt><dd>%s</dd>" % (k, v)
                          for k, v in rows if v)
        details = '<dl class="meta">%s</dl>' % details if details else ""
        # the anchor: the övergångsbestämmelse node already mints L{nr}; the
        # wrapper carries it only when no child does (no duplicate DOM ids)
        child_ids = {c.get("id") for c in am.get("content", [])}
        wrapper_id = ("L" + nr.replace(" ", "_")) if nr else None
        ida = (' id="%s"' % escape(wrapper_id)
               if wrapper_id and wrapper_id not in child_ids else "")
        posts.append('<div class="andring"%s><h2>%s</h2>%s%s%s</div>'
                     % (ida, escape(heading),
                        "<ul>%s</ul>" % "".join(links) if any(links) else "",
                        content, details))
    anchor = toc.add("L", "Ändringar och övergångsbestämmelser", 1)
    return ('<section class="andringar" id="%s"><h2 class="kaprubrik">'
            'Ändringar och övergångsbestämmelser</h2>%s</section>'
            % (escape(anchor), "".join(posts)))


def render_sfs(art, site):
    props = art.get("metadata", {}).get("properties", {})
    local_id = catalog.local(art["uri"])
    # a historical consolidation ("lydelse") carries its cutoff in `version`
    # and a /konsolidering/ uri; its page gets a way-back banner instead of
    # the inbound panel (citations always target the current consolidation)
    base_id, _, _ = local_id.partition("/konsolidering/")
    version = art.get("version")
    title = props.get("dcterms:title") or ("SFS " + base_id)
    # a repeal that has taken effect (a future repeal date is still in force):
    # mark the whole page as upphävd
    upphavd = props.get("rpubl:upphavandedatum")
    expired = (bool(upphavd) and upphavd <= date.today().isoformat()
               and not version)
    meta = _meta_dl([
        ("Utfärdad", props.get("rpubl:utfardandedatum")),
        ("Ikraftträder", props.get("rpubl:ikrafttradandedatum")),
        ("Upphävd", upphavd),
        ("Källa", props.get("dcterms:identifier")),
    ])
    toc = Toc()
    rail = Rail(site, art["uri"])
    parallel_appendix = any(
        child.get("type") == "konventionsbilaga"
        for node in art.get("structure", []) if node.get("type") == "bilaga"
        for child in node.get("children", []))
    versions = history.versions(base_id)
    structure = '<div id="dokument">' + "".join(
        render_node(n, site, art["uri"], toc, rail)
        for n in art.get("structure", [])) + "</div>"
    # the register view renders after the structure so its TOC entry and
    # rail hooks come last, but the OB anchors (#L{nr}) sit inside it
    andringar = _andringar(art, base_id, version, versions, site, toc, rail)
    # the statute's own preparatory works get top billing; their hosted uris are
    # then excluded from the generic citation panel below
    forarbeten, own_forarbeten = ("", set()) if version \
        else forarbeten_section(site, art)
    body = (_version_banner(base_id, version) if version
            else (_expired_banner(props) if expired else "")) \
        + _versions_panel(art, base_id, version, versions) \
        + forarbeten \
        + ("" if version else document_inbound(site, art["uri"], own_forarbeten)) \
        + structure + andringar
    rail.add_document()        # external links + law-level commentary, default panel
    body_classes = []
    if version:
        body_classes.append("inaktuell")
    elif expired:
        body_classes.append("expired")
    if parallel_appendix:
        body_classes.append("parallel-appendix")
    return page(title, "Författning", meta, body, render_toc(toc),
                eyebrow=("SFS %s · äldre lydelse" % base_id if version
                         else "SFS " + base_id),
                island=rail.island(),
                source_url=art.get("source_url"),
                body_class="".join(" " + name for name in body_classes))


DV_SHORT_COURT = {"Högsta domstolen": "HD",
                  "Högsta förvaltningsdomstolen": "HFD"}
DV_RULING_HEADING = {"betankande": "Föredragandens förslag till beslut",
                     "skiljaktig": "Skiljaktig mening", "tillagg": "Tillägg"}


def _dv_genitive(court):
    short = DV_SHORT_COURT.get(court)
    if short:
        return short + ":s"                       # HD:s, HFD:s
    return court + ("" if court.endswith("s") else "s")


def _dv_ruling_word(art):
    """The operative ruling's noun, from the målnummer prefix the court assigns:
    Ö-mål are beslut, B/T-mål are dom; otherwise the neutral "avgörande"."""
    mals = art.get("malnummer") or []
    pre = (mals[0][:1].upper() if mals else "")
    return {"Ö": "beslut", "B": "dom", "T": "dom"}.get(pre, "avgörande")


def _dv_walk(nodes, site, doc_uri, toc, rail, court=None, ruling="avgörande"):
    """Render a DV structure level: court instances and the betänkande/dom split
    become titled sections (the föredragande's proposal muted), domskäl/domslut
    are transparent wrappers whose own `<h2>` leaves carry the section titles,
    and prose leaves render as ordinary paragraphs."""
    sib = {n.get("type") for n in nodes}
    out = []
    for n in nodes:
        t = n.get("type")
        if t == "instans":
            c = n.get("court") or "Instans"
            anchor = toc.add(None, c, 1)
            inner = _dv_walk(n.get("children", []), site, doc_uri, toc, rail,
                             court=n.get("court"), ruling=ruling)
            out.append('<section class="instans"><h2 id="%s" class="instans-rubrik">'
                       '%s</h2>%s</section>' % (escape(anchor), escape(c), inner))
        elif t == "delmal":
            inner = _dv_walk(n.get("children", []), site, doc_uri, toc, rail,
                             court=court, ruling=ruling)
            head = ('<h2 class="delmal-rubrik">%s</h2>' % escape(n["ordinal"])
                    if n.get("ordinal") else "")
            out.append('<section class="delmal">%s%s</section>' % (head, inner))
        elif t in ("betankande", "skiljaktig", "tillagg"):
            label = DV_RULING_HEADING[t]
            anchor = toc.add(None, label, 2)
            inner = _dv_walk(n.get("children", []), site, doc_uri, toc, rail,
                             court=court, ruling=ruling)
            out.append('<section class="%s"><h3 id="%s" class="instans-rubrik">%s'
                       '</h3>%s</section>' % (t, escape(anchor), escape(label), inner))
        elif t == "dom":
            inner = _dv_walk(n.get("children", []), site, doc_uri, toc, rail,
                             court=court, ruling=ruling)
            # title the court's own ruling only where a betänkande precedes it in
            # the same instance; otherwise the instans heading already names it
            head = ""
            if "betankande" in sib and court:
                label = "%s %s" % (_dv_genitive(court), ruling)
                anchor = toc.add(None, label, 2)
                head = ('<h3 id="%s" class="instans-rubrik">%s</h3>'
                        % (escape(anchor), escape(label)))
            out.append('<section class="dom">%s%s</section>' % (head, inner))
        elif t in ("domskal", "domslut"):                # transparent wrappers
            out.append(_dv_walk(n.get("children", []), site, doc_uri, toc, rail,
                                court=court, ruling=ruling))
        else:
            out.append(render_node(n, site, doc_uri, toc, rail))
    return "".join(out)


def _dv_footnotes(footnotes, site):
    """The end-of-document footnotes as an endnote list, each with a ↩ link back
    to its inline marker (#fnref-N)."""
    if not footnotes:
        return ""
    items = []
    for fn in footnotes:
        n = escape(str(fn["num"]))
        items.append('<li id="fn-%s">%s <a class="fn-back" href="#fnref-%s" '
                     'aria-label="Tillbaka till texten">↩</a></li>'
                     % (n, render_runs(fn["text"], site), n))
    return ('<section class="fotnoter"><h2>Fotnoter</h2><ol>%s</ol></section>'
            % "".join(items))


def render_dv(art, site):
    md = art.get("metadata", {})
    # heading by canonical identity + HD's given name (the stamped artifact label;
    # computed live for an artifact parsed before the field). The löpnummer
    # ("NJA 2025:58") stays metadata, never part of the identity string.
    title = art.get("label") or casenaming.case_label(art)
    summary = ('<p class="sammanfattning">%s</p>' % escape(md["sammanfattning"])
               if md.get("sammanfattning") else "")
    meta = _meta_dl([
        ("Domstol", art.get("court_namn")),
        ("Avgörandedatum", art.get("avgorandedatum")),
        ("Målnummer", ", ".join(art.get("malnummer") or [])),
        ("Löpnummer", ", ".join(casenaming.lopnummer(art))),
        ("Rättsområde", ", ".join(md.get("rattsomrade") or [])),
    ])
    sokord = _keywords(md.get("nyckelord") or [], site)
    toc = Toc()
    rail = Rail(site, art["uri"])
    # a record with explicit instance structure (HD's modern <h1>-tagged form) is
    # walked as nested sections; a flat legacy record has no structural wrappers,
    # so the same walk renders it as a plain paragraph sequence
    body = (document_inbound(site, art["uri"]) + sokord
            + _dv_walk(art.get("structure", []), site, art["uri"], toc, rail,
                       ruling=_dv_ruling_word(art))
            + _dv_footnotes(art.get("footnotes", []), site))
    return page(title, "Rättsfall", meta, body, render_toc(toc),
                eyebrow=art.get("court_namn"), summary=summary,
                island=rail.island(), source_url=art.get("source_url"))


def _keywords(nyckelord, site):
    """Case keywords as links to their concept (begrepp) page where one
    exists -- the case→concept half of the keyword graph."""
    if not nyckelord:
        return ""
    items = []
    for n in nyckelord:
        uri = site.resolve(begrepp_uri(n))      # fold onto the canonical concept
        items.append('<a href="%s">%s</a>' % (escape(href(uri)), escape(n))
                     if site.has(uri) else escape(n))
    return '<p class="sokord"><span>Sökord</span> %s</p>' % " · ".join(items)


FA_TYPE_LABEL = {"prop": "Proposition", "sou": "SOU", "ds": "Ds",
                 "pm": "Promemoria", "bet": "Betänkande",
                 "dir": "Kommittédirektiv", "fm": "Förordningsmotiv",
                 "skr": "Skrivelse", "lr": "Lagrådsremiss",
                 "so": "Sveriges internationella överenskommelser"}


def render_implements(art, site):
    """The genomför-direktiv statements pulled from a proposition's
    författningskommentar (§7d): which EU directive article each provision
    transposes. Each links to the directive -- its article on our EU page when we
    host it, else out to EUR-Lex."""
    recs = art.get("implements")
    if not recs:
        return ""
    items = []
    for r in recs:
        directive = r["directive"]
        target = r["uris"][0] if r.get("uris") else directive
        link = _directive_link(site, directive, target)
        where = (("%s kap. %s § " % (r["chapter"], r["paragraf"]))
                 if r.get("chapter") and r.get("paragraf")
                 else ("%s § " % r["paragraf"]) if r.get("paragraf") else "")
        ref = ", ".join(r["pinpoints"] or r["articles"])
        items.append('<li>%sgenomför%s artikel %s i %s</li>'
                     % (escape(where), " delvis" if r.get("partial") else "",
                        escape(ref), link))
    return ('<section class="genomforande"><h2>Genomför EU-direktiv</h2>'
            '<ul>%s</ul></section>' % "".join(items))


def render_forarbete(art, site):
    title = art.get("title") or art.get("identifier") or art["uri"]
    meta = _meta_dl([("Beteckning", art.get("identifier")),
                     ("Typ", FA_TYPE_LABEL.get(art.get("type"), art.get("type"))),
                     ("Datum", art.get("date"))])
    parts = [document_inbound(site, art["uri"]), render_implements(art, site)]
    toc = Toc()
    doc_uri = art["uri"]
    rail = Rail(site, doc_uri)
    state = {"page": None}

    def emit_page(node):
        # page anchor (#sid{N} -- the förarbete citation target, unchanged by the
        # hierarchy); the statute/case paragraphs citing this page drive the rail
        pg = node.get("page")
        if pg and pg != state["page"]:
            state["page"] = pg
            key = "sid%d" % pg
            rail.add(key, "s. %d" % pg)
            # the page number doubles as the facsimile button: a click loads
            # the source PDF page as a retina PNG (faksimil.js + the
            # /api/v1/facsimile endpoint, rendered on demand and disk-cached)
            fax = "/api/v1/facsimile?uri=%s&sid=%d" % (quote(doc_uri, safe=""), pg)
            parts.append('<span class="sid" id="%s"%s><button type="button" '
                         'data-fax="%s" title="Visa faksimil av sidan %d">'
                         '%d</button></span>'
                         % (key, _rail_attr(rail, key), escape(fax), pg, pg))

    def close_komm():
        if state["komm"] is not None:
            parts.append("</div>")
            state["komm"] = None

    def walk(nodes):
        for n in nodes:
            emit_page(n)
            if n.get("type") == "avsnitt":
                close_komm()
                level = n.get("level") or 1
                anchor = toc.add(n.get("id"), plain(n["text"]), level)
                # wire the section to the scroll-driven rail (remiss feedback on
                # this avsnitt); a section with no context gets no data-rail
                rail.add(n.get("id"), plain(n["text"]))
                ra = _rail_attr(rail, n.get("id"))
                parts.append('<h%d id="%s"%s class="rubrik">%s</h%d>'
                             % (min(level + 1, 5), escape(anchor), ra,
                                render_runs(n["text"], site), min(level + 1, 5)))
                walk(n.get("children", []))
            elif n.get("type") == "tabell":
                # a nuvarande/föreslagen lydelse comparison: two columns of
                # aligned cells, the `th` row the italic column header
                close_komm()
                rows = []
                for r in n.get("children", []):
                    tag = "th" if r.get("th") else "td"
                    rows.append("<tr>%s</tr>" % "".join(
                        "<%s>%s</%s>" % (tag, render_runs(c, site), tag)
                        for c in r.get("cells", [])))
                parts.append('<table class="lydelse">%s</table>' % "".join(rows))
            else:
                # författningskommentar blocks (`fk`, stamped per entry by
                # forarbete's extractor at parse time): one highlight box per
                # entry -- a new entry number closes the previous box
                if n.get("fk"):
                    if state["komm"] != n["fk"]:
                        close_komm()
                        parts.append('<div class="fk-komm">')
                        state["komm"] = n["fk"]
                else:
                    close_komm()
                cls = ' class="fotnot"' if n.get("type") == "fotnot" else ""
                parts.append("<p%s>%s</p>" % (cls, render_runs(n["text"], site)))

    state["komm"] = None
    walk(art.get("structure", []))
    close_komm()
    rail.add_document()        # document-level remiss "most interesting" overall panel
    return page(title, "Förarbete", meta, "".join(parts), render_toc(toc),
                eyebrow=FA_TYPE_LABEL.get(art.get("type"), "Förarbete"),
                island=rail.island(), source_url=art.get("source_url"))


def _meta_dl(pairs):
    rows = "".join("<dt>%s</dt><dd>%s</dd>" % (escape(k), escape(str(v)))
                   for k, v in pairs if v)
    return '<dl class="meta">%s</dl>' % rows if rows else ""


def _doc_title(site, uri):
    row = site.con.execute("SELECT title FROM documents WHERE uri = ?",
                           (uri,)).fetchone()
    return row[0] if row else None


def render_begrepp(art, site):
    """A concept definition; its inbound panel shows everything (laws, cases,
    förarbeten, commentary, other concepts) that references the concept."""
    title = art.get("title") or catalog.local(art["uri"])
    meta = _meta_dl([("Kategori", ", ".join(art.get("categories") or []))])
    toc = Toc()
    rail = Rail(site, art["uri"])
    nodes = art.get("body", [])
    # a synthesized stub (a defined term / nyckelord with no wiki page) has no
    # description -- its value is the aggregated inbound below (what defines and
    # tags it), so say so instead of showing a blank page
    note = ("" if nodes else
            '<p class="stub-note">Det här begreppet har ännu ingen beskrivning. '
            'Nedan visas var det definieras och används.</p>')
    body = note + document_inbound(site, art["uri"]) + "".join(
        render_node(b, site, art["uri"], toc, rail) for b in nodes)
    return page(title, "Begrepp", meta, body, render_toc(toc),
                eyebrow="Begrepp", island=rail.island(),
                source_url=art.get("source_url"))


EURLEX_KIND = {"regulation": "EU-förordning", "directive": "EU-direktiv",
               "decision": "EU-beslut", "judgment": "EU-domstolen",
               "treaty": "Fördrag", "act": "EU-rättsakt"}

# block type -> css class for the generic (paragraph-like) EU blocks
EURLEX_CLASS = {"recital": "recital", "citation": "visa", "preamble": "preamble",
                "paragraph": "paragraph", "point": "point", "ruling": "ruling",
                "note": "note", "row": "row"}


# --------------------------------------------------------------------------
# editorial layer (a `.ann` file in the curated store, lib.annstore): thematic
# recital groups + the article<->recital cross-reference, folded into an EU
# act's page. Authored offline by `lagen eurlex ai-annotate`; absent for an
# unannotated act.
# --------------------------------------------------------------------------

def _sub_to_dot(key):
    """Normalise a sub-article ref to the canonical dotted id grammar --
    "6(2)(a)" -> "6.2.a" -- tolerating the legacy parenthesised form an older
    `.ann` may still carry (new ones are authored dotted)."""
    return re.sub(r"\(([^)]+)\)", r".\1", key)


class Editorial:
    """The `.ann` editorial layer for one EU act, mapping both directions of the
    preamble<->enacting-terms relation: an article (or sub-article like "4.5")
    to the recitals that explain it, and a recital back to the articles it
    underpins plus the thematic group it belongs to."""

    def __init__(self, layer):
        # keys are normalised to the dotted sub-article grammar the renderer mints,
        # so recitals_for(subarticle_key(...)) matches regardless of the on-disk form
        self.a2r = {_sub_to_dot(k): v
                    for k, v in layer.get("articleToRecitals", {}).items()}
        self.groups = layer.get("recitalGroups", [])
        self.group_start = {}        # first recital n of a group -> group (heading)
        self.group_of = {}           # recital n -> its group
        for g in self.groups:
            lo, hi = g["range"]
            self.group_start[lo] = g
            for n in range(lo, hi + 1):
                self.group_of[n] = g
        articles = {}                # recital n -> set of article numbers citing it
        for key, recitals in self.a2r.items():
            art = key.split(".", 1)[0]                       # "6.2.a" -> "6"
            for n in recitals:
                articles.setdefault(n, set()).add(art)
        self.recital_articles = {n: sorted(a, key=_art_sort_key)
                                 for n, a in articles.items()}

    def recitals_for(self, key):
        return self.a2r.get(key)


def _art_sort_key(art):
    """Sort article numbers numerically where possible ('2' before '10')."""
    return (0, int(art)) if art.isdigit() else (1, art)


def _load_editorial(celex):
    path = annstore.path("eurlex", celex)
    if not path.exists():
        return None
    layer = json.loads(path.read_text()).get("editorialLayer")
    return Editorial(layer) if layer else None


def _artlist(refs):
    """Article refs as links joined the Swedish way: "2", "2 och 6",
    "2, 6 och 28"."""
    return _swedish_join(['<a href="#%s">%s</a>' % (escape(a), escape(a))
                          for a in refs])


def _group_anchor(g):
    """The recital group's citation anchor -- its editorial `.ann` id, with a
    range-derived fallback if one is missing."""
    return g.get("id") or "rg%d" % g["range"][0]


def _recital_group_heading(g):
    """A compact, deliberately unofficial editorial label introducing a thematic
    recital group -- a single subdued line outdented into the left margin, since
    it is not part of the authentic act text. E.g. "Skäl 1–5: Bakgrund och syfte
    (jfr art 1)". Carries the group anchor so the TOC's Preambel section links to
    it."""
    lo, hi = g["range"]
    rng = "Skäl %d" % lo if lo == hi else "Skäl %d–%d" % (lo, hi)
    refs = g.get("articleRefs") or []
    jfr = ' <span class="jfr">(jfr art %s)</span>' % _artlist(refs) if refs else ""
    return ('<p id="%s" class="recital-group"><span class="rg-range">%s:</span> '
            '<b>%s</b>%s</p>' % (escape(_group_anchor(g)), escape(rng),
                                 escape(g["label"]), jfr))


def _recital_links_html(recitals):
    """Rail section for an article/sub-article: links to its relevant recitals."""
    links = "".join('<a href="#recital-%d">skäl %d</a>' % (n, n) for n in recitals)
    return ('<div class="rail-sec skal"><div class="rail-sec-h">Relevanta skäl'
            '</div><div class="skal-links">%s</div></div>' % links)


def _recital_context_html(editorial, n):
    """Rail panel for a recital: its thematic group and the articles it underpins
    (the back half of the article<->recital round-trip)."""
    parts = []
    g = editorial.group_of.get(n)
    if g:
        parts.append('<div class="rail-sec"><div class="rail-sec-h">Tematisk grupp'
                     '</div>%s</div>' % escape(g["label"]))
    articles = editorial.recital_articles.get(n)
    if articles:
        links = "".join('<a href="#%s">artikel %s</a>' % (escape(a), escape(a))
                        for a in articles)
        parts.append('<div class="rail-sec skal"><div class="rail-sec-h">Förklarar'
                     '</div><div class="skal-links">%s</div></div>' % links)
    return "".join(parts)


def _eurlex_marker(t, num):
    """Display form of an EU block's structural number. The artifact stores the
    bare token ("42", "1", "a") -- the surrounding punctuation is presentational:
    a recital is parenthesised ("(42)"), a numbered paragraph gets a full stop
    ("1."), a lettered/roman point the list-parenthesis ("a)", "i)"). Other
    numbered kinds (ruling, note) keep the bare token."""
    if t == "recital":
        return "(%s)" % num
    if t == "point":
        return "%s)" % num
    if t == "paragraph":
        return "%s." % num
    return num


def _eurlex_pin(t, num, bid):
    """The rail's "Kontext för …" label for an EU block."""
    if t == "recital" and num:
        return "Skäl %s" % num
    if t == "article":
        return "Artikel %s" % (num or bid or "")
    if bid and "." in bid:            # a dotted sub-article id ("5.2", "6.2.a")
        return "Artikel %s" % bid
    return human_fragment(bid)


def _render_eurlex_block(b, site, doc_uri, toc, rail, editorial=None,
                         cur_article=None, cur_parag=None):
    runs = render_runs(b["text"], site)
    bid = b.get("id")
    t = b["type"]
    num = b.get("num")
    if t == "heading":
        level = b.get("level") or 1
        anchor = toc.add(bid, plain(b["text"]), level)
        lvl = min(level + 1, 5)
        return '<h%d id="%s" class="rubrik">%s</h%d>' % (lvl, escape(anchor),
                                                         runs, lvl)
    if t == "keyword":
        return '<span class="sokord">%s</span>' % runs
    # a numbered recital is a citation target in its own right (`#recital-N`), so
    # it can be cited, commented on and ride the rail even with no editorial layer.
    if t == "recital" and num and num.isdigit():
        bid = "recital-%s" % num
    # editorial layer (.ann): wire this block into the article<->recital graph.
    # A recital gets a back-link panel (its articles + group); an article/
    # sub-article (paragraph/point, keyed like the .ann's "4.5") gets a forward
    # panel of its relevant recitals. Both ride the scroll-driven rail.
    extra = ""
    if t == "recital" and num and num.isdigit():
        if editorial:
            extra = _recital_context_html(editorial, int(num))
    else:
        # an article's key is its own id; a sub-article's is the dotted form. Every
        # numbered sub-article (paragraph/point) gets that id, so a reader can link
        # to it directly (`#4.22.a`) -- but it only *rides* the rail when it has
        # context to show (rail.add is a no-op otherwise, and _rail_attr then omits
        # the data-rail marker), so ubiquitous ids don't clutter the margin. The
        # editorial layer additionally gives a block a forward panel of its recitals.
        key = (cur_article if t == "article"
               else subarticle_key(t, num, cur_article, cur_parag))
        if key:
            recitals = editorial.recitals_for(key) if editorial else None
            if t != "article":
                bid = bid or key       # synthesise the sub-article citation id
            if recitals:
                extra = _recital_links_html(recitals)
    # the article is a citation target (id == its number); its inbound (incl.
    # implementing förarbeten) drives the rail, like an SFS paragraph
    pin = _eurlex_pin(t, num, bid)
    rail.add(bid, pin, extra)
    ra = _rail_attr(rail, bid)
    if t == "article":
        anchor = toc.add(bid, plain(b["text"]), 2)
        return '<h3 id="%s" class="artikel"%s>%s</h3>' % (escape(anchor), ra, runs)
    # the marker doubles as the block's permalink when the block is addressable,
    # so a reader can grab the link to a specific recital/paragraph/point by its
    # number; an unaddressable block (no id) keeps a plain span
    if num:
        label = escape(_eurlex_marker(t, num))
        marker = ('<a class="num" href="#%s">%s</a> ' % (escape(bid), label)
                  if bid else '<span class="num">%s</span> ' % label)
    else:
        marker = ""
    classes = [EURLEX_CLASS.get(t, "")]
    # a marked recital/paragraph/point hangs its marker in the left margin
    if num and t in ("recital", "paragraph", "point"):
        classes.append("hang")
    # a definitions-article point is a citation target (#<article>.<point>) and
    # the begrepp the act defines -- emit its id and emphasise the defined term
    defines = b.get("defines")
    if defines:
        classes.append("definition")
        runs = _emphasize_term(runs, defines, site)
    cls = " ".join(c for c in classes if c)
    return '<p%s%s%s>%s%s</p>' % (_id_attr(bid), ' class="%s"' % cls if cls else "",
                                  ra, marker, runs)


def _emphasize_term(runs_html, term, site):
    """Wrap a definition point's lead term (the plain text before its colon) in
    <dfn>, so the defined word stands out from its definition -- and, when the
    corpus has a begrepp page for the term, link the <dfn> to it (the act's own
    definition of "personuppgifter" -> /begrepp/Personuppgift). The concept name
    is folded onto its canonical page the way case keywords resolve, so an
    inflected/variant term ("personuppgifter") still finds the page."""
    lead = escape(term)
    if not runs_html.startswith(lead):
        return runs_html
    uri = site.resolve(begrepp_uri(term))
    dfn = ('<a href="%s"><dfn>%s</dfn></a>' % (escape(href(uri)), lead)
           if site.has(uri) else "<dfn>%s</dfn>" % lead)
    return dfn + runs_html[len(lead):]


def render_eurlex(art, site):
    # the heading is the act's short name (curated or extracted, stamped onto the
    # artifact at parse) plus its citing acronym -- "Cyberresiliensförordningen
    # (CRA)"; the full official title moves into the metadata list. With no short
    # name the heading is the full title, so it is not repeated in the metadata.
    # display_title is the single definition of this, shared with search/listings.
    title = catalog.display_title(art, art.get("title") or catalog.local(art["uri"]))
    # a named case shows its case number ("C-311/18") as its own row: the heading
    # is the usual name, so the number would otherwise appear nowhere but the
    # CELEX. An unnamed case's heading already is the case number -- don't repeat
    # it. Read the stamped `shortname` (the artifact is the source of truth) to
    # decide "named", rather than re-deriving the name live, so this row can never
    # disagree with the heading if the name snapshot is refreshed before re-parse.
    number = (eucasenaming.case_number(art["celex"])
              if art.get("doctype") == "judgment" else None)
    named_case = number is not None and art.get("shortname") not in (None, number)
    meta = _meta_dl([
        ("Titel", art.get("title") if art.get("shortname") else None),
        ("Mål", number if named_case else None),
        ("CELEX", art.get("celex")),
        ("Typ", EURLEX_KIND.get(art.get("doctype"), art.get("doctype"))),
        ("Datum", art.get("date")),
        ("EUT", art.get("oj")),
        ("ECLI", art.get("ecli")),
    ])
    editorial = _load_editorial(art["celex"])
    toc = Toc()
    rail = Rail(site, art["uri"])
    parts = [document_inbound(site, art["uri"])]
    cur_article = cur_parag = None       # running context for sub-article keys
    preamble_in_toc = False              # the "Preambel" TOC parent is added once
    # the artifact is a nested structure (divisions > articles > paragraphs >
    # points); render reads it in document order -- the heading levels and the
    # TOC already convey the hierarchy, so no nested <section> markup is needed
    for b in eurlex_flatten(art.get("structure", [])):
        t = b["type"]
        if editorial and t == "recital" and (b.get("num") or "").isdigit():
            group = editorial.group_start.get(int(b["num"]))
            if group:
                anchor = _group_anchor(group)
                if not preamble_in_toc:   # a Preambel section listing the groups
                    toc.add(anchor, "Preambel", 1)
                    preamble_in_toc = True
                toc.add(anchor, group.get("label", ""), 2)
                parts.append(_recital_group_heading(group))
        if t == "article":
            cur_article, cur_parag = b.get("id") or b.get("num"), None
        elif t == "paragraph":
            cur_parag = b.get("num")
        parts.append(_render_eurlex_block(b, site, art["uri"], toc, rail,
                                          editorial, cur_article, cur_parag))
    rail.add_document()        # external links + commentary, the rail's default panel
    body = "".join(parts)
    kind = EURLEX_KIND.get(art.get("doctype"), "EU-rättsakt")
    return page(title, kind, meta, body, render_toc(toc),
                eyebrow=kind, island=rail.island(),
                source_url=art.get("source_url"))


def _ref_link(site, uri):
    """A link to a referenced document for a föreskrift's outbound metadata
    (bemyndigande -> SFS paragraf, genomför -> EU directive): the statute
    paragraf pinpointed and named, or the CELEX out to EUR-Lex; a plain span
    for an SFS we have not parsed."""
    if is_external(uri):
        return ('<a class="ext" href="%s" rel="external">%s</a>'
                % (escape(external_href(uri)),
                   escape(catalog.local(uri).rsplit("/", 1)[-1])))
    base, _, frag = uri.partition("#")
    pin = human_fragment(frag)
    name = _law_title(site, base)
    label = ("%s %s" % (pin, name)).strip() if pin else name
    return ('<a href="%s">%s</a>' % (escape(href(uri)), escape(label))
            if site.has(base) else '<span class="noref">%s</span>' % escape(label))


def _ref_list(site, heading, uris):
    if not uris:
        return ""
    items = "".join("<li>%s</li>" % _ref_link(site, u) for u in uris)
    return ('<section class="refs"><h2>%s</h2><ul>%s</ul></section>'
            % (escape(heading), items))


def render_foreskrift(art, site):
    md = art.get("metadata", {})
    ident = art.get("identifier") or catalog.local(art["uri"])
    title = md.get("title") or ident
    meta = _meta_dl([
        ("Utgivare", md.get("publisher")),
        ("Beslutad", md.get("beslutsdatum")),
        ("Ikraftträdande", md.get("ikrafttradandedatum")),
        ("Utkom från trycket", md.get("utkomFranTryck")),
    ])
    # outbound: the empowering statute paragrafer (the inbound mirror of which is
    # the SFS paragraf's "Föreskrifter meddelade med stöd av …" margin) + EU dir
    refs = (_ref_list(site, "Bemyndigande", md.get("bemyndigande"))
            + _ref_list(site, "Genomför EU-direktiv", md.get("genomfor")))
    toc = Toc()
    rail = Rail(site, art["uri"])
    body = document_inbound(site, art["uri"]) + refs + "".join(
        render_node(n, site, art["uri"], toc, rail)
        for n in art.get("structure", []))
    return page(title, "Föreskrift", meta, body, render_toc(toc),
                eyebrow=ident, island=rail.island(),
                source_url=art.get("source_url"))


def render_avg(art, site):
    md = art.get("metadata", {})
    ident = art.get("identifier") or catalog.local(art["uri"])
    title = md.get("title") or ident
    meta = _meta_dl([
        ("Myndighet", md.get("publisher")),
        ("Beslutsdatum", md.get("beslutsdatum")),
        ("Diarienummer", ", ".join(md.get("diarienummer", []))),
        ("Avgjord av", md.get("avgjordAv")),
        ("Sakområde", ", ".join(md.get("nyckelord", [])) or None),
    ])
    summary = ('<p class="sammanfattning">%s</p>'
               % escape(art["sammanfattning"])
               if art.get("sammanfattning") else "")
    toc = Toc()
    rail = Rail(site, art["uri"])
    body = document_inbound(site, art["uri"]) + "".join(
        render_node(n, site, art["uri"], toc, rail)
        for n in art.get("structure", []))
    section = {"jo": "JO-beslut", "jk": "JK-beslut",
               "arn": "ARN-beslut"}.get(art.get("org"), "Myndighetsavgörande")
    return page(title, section, meta, body, render_toc(toc),
                eyebrow=ident, summary=summary, island=rail.island(),
                source_url=art.get("source_url"))


def render_hudoc(art, site):
    md = art.get("metadata", {})
    meta = _meta_dl([
        ("Domstol", md.get("publisher")),
        ("Avgörandedatum", art.get("date")),
        ("Ansökningsnummer", ", ".join(md.get("applicationNumber", [])) or None),
        ("Dokumenttyp", art.get("doctype")),
        ("Språk", md.get("language")),
        ("ECLI", art.get("ecli")),
        ("Motpart", md.get("respondent")),
        ("Artiklar", ", ".join(md.get("articles", [])) or None),
    ])
    summary = ("<p class=\"sammanfattning\">%s</p>" % escape("; ".join(
        md.get("conclusions", []))) if md.get("conclusions") else "")
    toc = Toc()
    rail = Rail(site, art["uri"])
    refs = _ref_list(site, "Berörda konventionsartiklar",
                     [ref["uri"] for ref in art.get("references", [])])
    body = document_inbound(site, art["uri"]) + refs + "".join(
        render_node(node, site, art["uri"], toc, rail)
        for node in art.get("structure", []))
    rail.add_document()
    return page(art.get("title") or art.get("itemid"), "Europadomstolen",
                meta, body, render_toc(toc), eyebrow=art.get("ecli") or art["itemid"],
                summary=summary, island=rail.island(),
                source_url=art.get("source_url"))


def _render_coe_provision(node, site, doc_uri, toc, rail):
    aid = node.get("id")
    number = node.get("ordinal") or ""
    title = render_runs(node.get("text", []), site)
    anchor = toc.add(aid, plain(node.get("text", [])), 1)
    label = "Artikel" if node.get("type") == "artikel" else "Sektion"
    rail.add(aid, "%s %s" % (label, number))
    children = "".join(render_node(child, site, doc_uri, toc, rail)
                       for child in node.get("children", []))
    return ('<section class="artikel"%s%s><h2 id="%s">%s</h2>%s</section>'
            % (_id_attr(None), _rail_attr(rail, aid), escape(anchor), title, children))


def render_coe(art, site):
    md = art.get("metadata", {})
    implementation = md.get("swedishImplementation")
    meta = _meta_dl([
        ("Referens", md.get("reference")),
        ("Öppnad för undertecknande", md.get("openingDate")),
        ("Ort", md.get("openingPlace")),
        ("Ikraftträdande", md.get("entryIntoForce")),
        ("Svensk lag", "SFS 1994:1219" if implementation else None),
    ])
    toc = Toc()
    rail = Rail(site, art["uri"])
    implementation_link = (_ref_list(site, "Svensk inkorporering", [implementation])
                           if implementation else "")
    parts = [document_inbound(site, art["uri"]), implementation_link]
    for node in art.get("structure", []):
        if node.get("type") in ("artikel", "sektion"):
            parts.append(_render_coe_provision(node, site, art["uri"], toc, rail))
        else:
            parts.append(render_node(node, site, art["uri"], toc, rail))
    rail.add_document()
    return page(art.get("title") or art.get("identifier"),
                "Europarådets fördrag", meta, "".join(parts), render_toc(toc),
                eyebrow=art.get("identifier"), island=rail.island(),
                source_url=art.get("source_url"))


# the sources whose pages carry inline-editable content. A logged-in user edits
# the *commentary* (kommentar rail) on a host act's node -- the official body text
# stays read-only -- so the editable ref is the host's `annotates` basefile: the
# uri's local part, bar eurlex's `ext/celex/` prefix (the bare CELEX the
# commentary frontmatter keys on). A concept page edits its own body.
KOMMENTAR_HOSTS = ("sfs", "eurlex", "foreskrift", "forarbete")


def edit_meta(kind, ref, uri, source="", basefile=""):
    """The `<meta>` that tells editor.js what a page is and which markdown region
    an edit maps to. `source`/`basefile` additionally name the document's own
    identity when it is patchable (see lib.patch), so the editor can offer a
    "patch source" button beside the commentary one. Empty string disables editing
    on the page. Kept a plain string (not a page-shell param) so it can be injected
    uniformly into every renderer's output, including the editorial-site renderer."""
    return ('<meta name="lagen-doc" data-kind="%s" data-ref="%s" '
            'data-source="%s" data-basefile="%s" content="%s">'
            % (escape(kind), escape(ref), escape(source), escape(basefile),
               escape(uri)))


def _document_edit_meta(source, art):
    uri = art["uri"]
    if source in KOMMENTAR_HOSTS:
        local = catalog.local(uri)
        ref = local[len("ext/celex/"):] if local.startswith("ext/celex/") else local
        # the host act's own basefile is `ref`; every KOMMENTAR_HOSTS source is
        # patchable, so pass its identity through for the patch-source button
        return edit_meta("kommentar", ref, uri, source=source, basefile=ref)
    if source == "begrepp":
        return edit_meta("begrepp", art["title"], uri)
    return ""                            # dv / avg pages host no editable content


def render_document(art, source, site):
    # kommentar is not here -- it is an annotation rendered into statute rails
    # (generate_site skips it), not a page of its own
    html = {"sfs": render_sfs, "dv": render_dv, "forarbete": render_forarbete,
            "begrepp": render_begrepp, "eurlex": render_eurlex,
            "foreskrift": render_foreskrift, "avg": render_avg,
            "hudoc": render_hudoc, "coe": render_coe}[source](art, site)
    meta = _document_edit_meta(source, art)
    alias = feeds.alias_for_source(source)
    discovery = ('<link rel="alternate" type="application/atom+xml" '
                 'href="/dataset/%s/feed.atom">' % alias) if alias else ""
    # injected right before </head> (PAGE has exactly one) rather than threaded
    # through every per-source renderer's page() call
    return html.replace("</head>", discovery + meta + "</head>", 1)


# --------------------------------------------------------------------------
# frontpage
# --------------------------------------------------------------------------

# the document types, in the order they appear on the frontpage, with their
# Swedish collection labels. dv's documents (and so its browse index) live under
# /dom/, lagen.nu's grammar; every other source browses under its own name.
# kommentar is an annotation layer shown in the rail (no page tree), so it is
# not a browsable source on the frontpage
SOURCE_ORDER = ("sfs", "dv", "hudoc", "forarbete", "foreskrift", "avg",
                "eurlex", "coe", "begrepp")
SOURCE_LABEL = {"sfs": "Författningar", "dv": "Rättsfall",
                "forarbete": "Förarbeten", "foreskrift": "Myndighetsföreskrifter",
                "avg": "JO- och JK-beslut", "eurlex": "EU-rättsakter",
                "hudoc": "Europadomstolens praxis",
                "coe": "Europarådets fördrag",
                "kommentar": "Lagkommentarer", "begrepp": "Begrepp"}
# the international-law sources share one masthead entry and one landing page
# (/folkratt/): a bespoke alphabetical treaty listing (coe) beside the faceted
# case browse (hudoc), which relocates under /folkratt/hudoc/. coe has no faceted
# browse tree of its own -- its whole listing lives on the landing page.
FOLKRATT_SOURCES = ("hudoc", "coe")
FOLKRATT_LABEL = "Folkrätt"
BROWSE_DIR = {"dv": "dom", "hudoc": "folkratt/hudoc"}


def _browse_dir(source):
    return BROWSE_DIR.get(source, source)


def _most_cited(con, source):
    """The 25 most-referenced documents of a source as ranked-list <li>s (the
    highlight reels on the frontpage), or '' if the source is empty."""
    rows = con.execute(
        "SELECT d.uri, COALESCE(d.title, d.label), COUNT(DISTINCT l.from_uri) c "
        "FROM links l JOIN documents d ON d.uri = l.to_root "
        "WHERE d.source = ? AND l.from_uri <> l.to_root "
        "GROUP BY l.to_root ORDER BY c DESC LIMIT 25", (source,)).fetchall()
    return "".join('<li><a href="%s">%s</a> <span class="c">%d</span></li>'
                   % (escape(href(u)), escape(t), c) for u, t, c in rows)


def _index_rows(n):
    """The frontpage source rows as (route, label, count): each browsable source
    in SOURCE_ORDER, but the international-law sources collapsed into one
    'Folkrätt' row (their combined count, linking to the shared landing) at the
    position of the first one present."""
    seen = False
    for s in SOURCE_ORDER:
        if s in FOLKRATT_SOURCES:
            if seen:
                continue
            seen = True
            total = sum(n.get(x, 0) for x in FOLKRATT_SOURCES)
            if total:
                yield "/folkratt/", FOLKRATT_LABEL, total
        elif n.get(s):
            yield "/%s/" % _browse_dir(s), SOURCE_LABEL.get(s, s), n[s]


def render_index(con):
    n = {s: c for s, c in catalog.counts(con).items() if s != "kommentar"}
    nav = "".join(
        '<li><a href="%s">%s</a> <span class="c">%d</span></li>'
        % (route, escape(label), count)
        for route, label, count in _index_rows(n))
    cols = []
    for source, heading in (("sfs", "Mest hänvisade författningar"),
                            ("dv", "Mest hänvisade rättsfall")):
        items = _most_cited(con, source)
        if items:
            cols.append('<section><h2>%s</h2><ol class="ranked">%s</ol></section>'
                        % (heading, items))
    body = ('<p class="lead">%d sammanlänkade dokument fördelade på %d '
            'dokumenttyper.</p>'
            '<nav class="browse counts"><ul>%s</ul></nav>'
            '<div class="cols">%s</div>'
            % (sum(n.values()), sum(1 for s in n if n[s]), nav, "".join(cols)))
    return page("lagen.nu", "Start", "", body,
                eyebrow="Sveriges lagar, med kontext", solo=True)


# --------------------------------------------------------------------------
# the international-law (folkrätt) landing at /folkratt/: a bespoke page, not a
# faceted browse. The Council-of-Europe treaties are listed alphabetically by
# their significant title (the SFS listing convention), each with its amending
# protocols nested beneath it, split into a curated central set (the treaties
# named in coe/data/names.json) and the rest A-Z. The European Court of Human
# Rights sits beside them as links into its own faceted browse (relocated under
# /folkratt/hudoc/) plus a most-cited reel.
# --------------------------------------------------------------------------

@functools.lru_cache(maxsize=1)
def _coe_named():
    """The hand-edited coe/data/names.json, the file the citation engine reads,
    as {ETS/CETS number: entry}. Its keys are the curated central treaties
    (surfaced first on the folkrätt page); each entry carries the informal
    Swedish name(s) (`label`) and acronym (`abbr`) shown in the listing, either a
    string or a list."""
    return {number: entry
            for number, entry in json.loads(datasets.COE_NAMES.read_text("utf-8")).items()
            if isinstance(entry, dict)}


def _first(value):
    """The primary form of a names.json `label`/`abbr` (a string, or the first of
    a list)."""
    return value[0] if isinstance(value, list) else value


def _coe_parenthetical(row, named):
    """The subdued gloss after a treaty title: its informal Swedish name and
    acronym where registered, then always the ETS/CETS reference --
    'Europakonventionen, EKMR, ETS No. 005', or just 'ETS No. 024'."""
    entry = named.get(row["number"]) or {}
    parts = []
    if entry.get("label"):
        name = _first(entry["label"])
        parts.append(name[:1].upper() + name[1:])
    if entry.get("abbr"):
        parts.append(_first(entry["abbr"]))
    parts.append(row["identifier"])
    return ", ".join(parts)


def _coe_number(uri):
    return uri.rsplit("/", 1)[-1]                 # '…/ext/coe/005' -> '005'


def _coe_sort_key(title):
    return coe.significant_title(title)[1].lower()


def _coe_nest(rows):
    """Group Council-of-Europe rows into top-level instruments each carrying its
    amending protocols. A protocol whose parent name (parsed from its title)
    prefix-matches a convention in the corpus nests under it; one that matches
    nothing (a protocol to a protocol, or a parent outside the corpus) stands as
    its own top-level entry. Returns (top_level_rows, {parent_number: [protocol
    rows]}), both ordered for display."""
    conventions = [r for r in rows if r["kind"] != "protocol"]
    # longest title first so a protocol's parent name matches the most specific
    # convention it starts with, not a shorter convention that shares a prefix
    by_title = sorted(conventions, key=lambda r: -len(r["title"]))
    children, orphans = {}, []
    for r in rows:
        if r["kind"] != "protocol":
            continue
        reference = coe.protocol_reference(r["title"])
        parent = next((c for c in by_title if reference
                       and reference.lower().startswith(c["title"].lower())), None)
        if parent:
            children.setdefault(parent["number"], []).append(r)
        else:
            orphans.append(r)
    for kids in children.values():
        kids.sort(key=lambda r: (r["date"] or "", r["number"]))
    top = sorted(conventions + orphans, key=lambda r: _coe_sort_key(r["title"]))
    return top, children


def _coe_entry(row, named, children):
    pre, key = coe.significant_title(row["title"])
    name = ('<a href="%s"><span class="pre">%s</span>%s</a> '
            '<span class="ref">(%s)</span>'
            % (escape(href(row["uri"])), escape(pre), escape(key or row["title"]),
               escape(_coe_parenthetical(row, named))))
    kids = children.get(row["number"], [])
    inner = ('<ul class="folkratt-protocols">%s</ul>'
             % "".join(_coe_entry(k, named, children) for k in kids)) if kids else ""
    return "<li>%s%s</li>" % (name, inner)


def _coe_listing(con):
    """The Council-of-Europe half of the folkrätt page: the central treaties, then
    every other instrument A-Z, protocols nested. '' when the corpus has none."""
    rows = [{"uri": uri, "number": _coe_number(uri), "kind": kind,
             "title": title, "identifier": label, "date": doc_date}
            for uri, _src, kind, label, title, _url, _path, _display, doc_date
            in catalog.facet_documents(con, "coe")]
    if not rows:
        return ""
    named = _coe_named()
    top, children = _coe_nest(rows)
    groups = []
    for heading, members in (
            ("Centrala fördrag", [r for r in top if r["number"] in named]),
            ("Övriga fördrag", [r for r in top if r["number"] not in named])):
        if members:
            groups.append('<h3>%s</h3><ul class="browse-list folkratt-treaties">%s</ul>'
                          % (heading, "".join(_coe_entry(r, named, children)
                                              for r in members)))
    return ('<section class="folkratt-group"><h2>Europarådet</h2>%s</section>'
            % "".join(groups))


def _hudoc_section(con):
    """The European Court of Human Rights half of the landing: the most-cited reel
    and a link into the case browse. Doc-type navigation lives in the shared
    top-level selector, so this no longer repeats the facet links. '' when empty."""
    if not catalog.document_count(con, "hudoc"):
        return ""
    cited = _most_cited(con, "hudoc")
    reel = ('<h3>Mest hänvisade avgöranden</h3><ol class="ranked">%s</ol>' % cited
            if cited else "")
    return ('<section class="folkratt-group"><h2>Europadomstolen</h2>%s'
            '<p><a href="/folkratt/hudoc/">Bläddra bland avgöranden →</a></p>'
            '</section>' % reel)


# the shared top-level "Dokumenttyp" selector carried by every folkrätt aggregate
# page (the landing and the hudoc browse leaves), so a reader switches between the
# instrument families from anywhere. Entries: the Council-of-Europe treaties as
# one "Fördrag" bucket (protocols nest under their convention, not as a sibling
# type) plus each HUDOC case type (currently only "Domar"). Data-driven, so a new
# case type or the later UN/ICJ sources extend it without a code change.
def _folkratt_axis(con):
    n = catalog.counts(con)
    entries = []
    if n.get("coe"):
        entries.append(("coe", "Fördrag", "/folkratt/", n["coe"]))
    if n.get("hudoc"):
        for b in facets.tree(con, "hudoc")["buckets"]:
            entries.append(("hudoc:" + b["slug"], b["label"],
                            _browse_url("hudoc", [b["slug"]]), b["count"]))
    return entries


def _folkratt_nav(entries, active_id):
    items = "".join(
        '<li><a href="%s"%s>%s <span class="c">%d</span></a></li>'
        % (escape(url), ' aria-current="page"' if key == active_id else "",
           escape(label), count)
        for key, label, url, count in entries)
    return ('<nav class="facets"><h2 class="facet-axis">Dokumenttyp</h2>'
            '<ul class="facet-list">%s</ul></nav>' % items)


def render_folkratt(con):
    body = _coe_listing(con) + _hudoc_section(con)
    if body:
        body = _folkratt_nav(_folkratt_axis(con), "coe") + body
    return page("Folkrätt", "Folkrätt", "",
                body or '<p class="empty">Inga dokument.</p>',
                eyebrow="Internationell rätt och mänskliga rättigheter", solo=True)


# --------------------------------------------------------------------------
# faceted browse. A whole source is too large for one flat listing, so it is
# sliced into one or two facets (a law's subject initial, a case's court + year).
# The generator is a *client of the REST API*: it reads the browse model from
# GET /api/v1/browse (the navigator + each leaf bucket's ordered, labelled
# documents) and writes static HTML -- it never touches the catalog directly.
# Every leaf bucket becomes its own page ("Författningar som börjar på A",
# "NJA – Högsta domstolen 2024") with a navigator linking the sibling buckets,
# so the site is browsable with no JS.
# --------------------------------------------------------------------------

def _browse_client(catalog_path):
    """An in-process API client bound to `catalog_path` -- the generator consumes
    the same REST endpoints a network client would, with no running server. The
    get_con override is cleared by the caller (render_aggregates)."""
    def _con():
        con = sqlite3.connect("file:%s?mode=ro" % catalog_path, uri=True)
        try:
            yield con
        finally:
            con.close()
    api_service.app.dependency_overrides[api_service.get_con] = _con
    return TestClient(api_service.app)


def _browse_url(source, slugs):
    """Absolute URL of a browse bucket page (a directory, trailing slash)."""
    return "/" + "/".join([_browse_dir(source), *slugs]) + "/"


def _browse_item(doc):
    # a statute carries a split title: the designation/number prefix is shown
    # subdued, the sort subject emphasised, so the eye lands on where it files.
    # data-name/data-year drive the client-side filter. A non-statute (förordning,
    # kungörelse, …) dims the whole entry.
    if doc.get("key") is not None:
        cls = ' class="subdued"' if doc.get("subdued") else ""
        name = (doc.get("pre") or "") + doc["key"]
        label = ('<span class="pre">%s</span>%s'
                 % (escape(doc.get("pre") or ""), escape(doc["key"])))
        return ('<li%s data-name="%s" data-year="%s"><a href="%s">%s</a></li>'
                % (cls, escape(name.lower()), escape(doc.get("year") or ""),
                   escape(doc["url"]), label))
    return ('<li><a href="%s">%s</a></li>'
            % (escape(doc["url"]), escape(doc["display"])))


def _facet_links(source, buckets, parent_slugs, active_keys, depth):
    items = []
    for b in buckets:
        url = _browse_url(source, parent_slugs + [b["slug"]])
        cur = (' aria-current="page"' if depth < len(active_keys)
               and active_keys[depth] == b["key"] else "")
        items.append('<li><a href="%s"%s>%s <span class="c">%d</span></a></li>'
                     % (escape(url), cur, escape(b["label"]), b["count"]))
    return '<ul class="facet-list">%s</ul>' % "".join(items)


def _facet_nav(source, view, active_keys):
    """The navigator: the primary buckets as links, plus -- under the active
    primary -- its secondary buckets (the year/… within a court/type). A primary
    axis with a single bucket is not navigable (nothing to choose), so it is
    omitted -- e.g. HUDOC's lone 'Domar' type, whose selector lives in the shared
    folkrätt axis above instead."""
    levels, buckets = view["levels"], view["buckets"]
    parts = (['<h2 class="facet-axis">%s</h2>' % escape(levels[0]),
              _facet_links(source, buckets, [], active_keys, 0)]
             if len(buckets) > 1 else [])
    if len(levels) > 1:
        cur = next((b for b in buckets if b["key"] == active_keys[0]), None)
        if cur and cur["children"]:
            parts.append('<h2 class="facet-axis">%s</h2>' % escape(levels[1]))
            parts.append(_facet_links(source, cur["children"], [cur["slug"]],
                                      active_keys, 1))
    return '<nav class="facets">%s</nav>' % "".join(parts)


def _bucket_heading(source, levels, nodes):
    """The reading heading for a leaf bucket -- 'Författningar som börjar på A',
    'NJA – Högsta domstolen 2024', 'Förordningar 2016'."""
    if len(levels) == 1:
        return "%s som börjar på %s" % (SOURCE_LABEL.get(source, source), nodes[0]["key"])
    return "%s %s" % (nodes[0]["label"], nodes[1]["key"])


# client-side filter for a statute listing: narrows this letter's entries by name
# substring, or -- when the query is all digits -- by year prefix. data-name/
# data-year live on each <li>; the running match count updates .browse-shown.
BROWSE_FILTER = ('<input type="search" class="browse-filter" '
                 'placeholder="Filtrera på namn eller år…" '
                 'aria-label="Filtrera författningar">')

BROWSE_FILTER_JS = """<script>
(function(){
  var box=document.querySelector('.browse-filter'),
      list=document.querySelector('.browse-list');
  if(!box||!list)return;
  var items=Array.prototype.slice.call(list.children),
      shown=document.querySelector('.browse-shown');
  box.addEventListener('input',function(){
    var q=box.value.trim().toLowerCase(), byYear=/^[0-9]+$/.test(q), n=0;
    items.forEach(function(li){
      var ok=!q||(byYear
        ?(li.getAttribute('data-year')||'').indexOf(q)===0
        :(li.getAttribute('data-name')||'').indexOf(q)!==-1);
      li.hidden=!ok; if(ok)n++;
    });
    if(shown)shown.textContent=n;
  });
})();
</script>"""


def render_facet_page(source, view, nodes, banner=""):
    """A single browse bucket page: an optional cross-source `banner` (the shared
    folkrätt selector), the navigator, and this leaf bucket's document list.
    `nodes` is the bucket-node path (one per level); the leaf carries its
    `documents` (from the API, already ordered and labelled). A statute listing
    also gets a client-side name/year filter over the letter's entries."""
    heading = _bucket_heading(source, view["levels"], nodes)
    docs = nodes[-1].get("documents") or []
    listing = ('<ul class="browse-list">%s</ul>' % "".join(_browse_item(d) for d in docs)
               if docs else '<p class="empty">Inga dokument.</p>')
    filtered = source == "sfs" and bool(docs)
    body = ('%s%s<section class="browse-group"><h1>%s '
            '<span class="c"><span class="browse-shown">%d</span></span></h1>%s%s%s</section>'
            % (banner, _facet_nav(source, view, [n["key"] for n in nodes]),
               escape(heading), len(docs),
               BROWSE_FILTER if filtered else "", listing,
               BROWSE_FILTER_JS if filtered else ""))
    alias = feeds.alias_for_source(source)
    discovery = ('<link rel="alternate" type="application/atom+xml" '
                 'href="/dataset/%s/feed.atom">' % alias) if alias else ""
    return page(heading, "Bläddra", "", body, solo=True, head=discovery)


def _write_browse(out_root, source, slugs, html):
    target = Path(out_root).joinpath(_browse_dir(source), *slugs)
    target.mkdir(parents=True, exist_ok=True)
    compress.write_text(target / "index.html", html,
                        encodings=compress.PAGE_ENCODINGS)


def generate_browse(client, source, out_root, folk_axis=None):
    """Write every leaf-bucket page of one source from the API's browse model,
    plus the landing copies: a primary bucket's directory shows its first
    (default) child, and the source root shows the overall default bucket -- so
    /dom/, /dom/nja/ and /dom/nja/2025/ all resolve without a redirect or JS.
    The caller (render_aggregates) already skips the one source the API does
    not facet (kommentar), so every `source` here is faceted. `folk_axis` (the
    shared folkrätt selector entries) prepends that selector to each page,
    marking this source's primary bucket current -- passed only for hudoc."""
    resp = client.get("/api/v1/browse", params={"source": source})
    view = resp.json()
    root_html = None
    for prim in view["buckets"]:
        banner = (_folkratt_nav(folk_axis, "%s:%s" % (source, prim["slug"]))
                  if folk_axis else "")
        leaves = [[prim, sec] for sec in prim["children"]] if prim["children"] \
            else [[prim]]
        for i, nodes in enumerate(leaves):
            slugs = [n["slug"] for n in nodes]
            html = render_facet_page(source, view, nodes, banner=banner)
            _write_browse(out_root, source, slugs, html)
            if len(nodes) > 1 and i == 0:        # primary landing = first child
                _write_browse(out_root, source, slugs[:1], html)
            if root_html is None:                # overall default = first leaf
                root_html = html
    _write_browse(out_root, source, [], root_html)


# --------------------------------------------------------------------------
# generate the whole site
# --------------------------------------------------------------------------

# per-worker render state, set once per process by _render_init -- the catalog
# connection and Site can't cross the ProcessPool fork, so each worker builds its
# own once and renders many pages against it (mirrors build.run_action's pattern)
_RENDER: dict = {}


def _render_init(catalog_path, out_root):
    con = catalog.connect(catalog_path)
    _RENDER.update(con=con, site=Site.from_catalog(con), out_root=Path(out_root))


def _write_page(uri, source, path, title, site, out_root):
    """Render one document to its HTML file. A synthesized concept stub has no
    artifact on disk (empty path) and renders a shell whose content is its
    aggregated inbound (what defines/tags the concept); everything else loads its
    artifact."""
    art = (json.loads(compress.read_bytes(path)) if path
           else {"uri": uri, "type": source, "title": title})
    out = Path(out_root) / doc_relpath(uri)
    out.parent.mkdir(parents=True, exist_ok=True)
    compress.write_text(out, render_document(art, source, site),
                        encodings=compress.PAGE_ENCODINGS)


def _render_one(job):
    """ProcessPool entry point: render `job` (uri, source, path, title) against
    this worker's prebuilt Site, returning the uri rendered."""
    _write_page(*job, _RENDER["site"], _RENDER["out_root"])  # ty: ignore[too-many-positional-arguments]  # job is a 4-tuple; ty cannot see arity through *
    return job[0]


def generate_site(catalog_path, out_root, progress=None, fresh=None, record=None,
                  only=None, source=None, jobs=1, extra=None, write_index=True):
    """Render every catalogued document to static HTML. `fresh(uri, out_path,
    art_path, dep_digest) -> bool` lets the caller skip a page whose inputs are
    unchanged (incremental generate); `record(uri, art_path, dep_digest)` is
    called after a page is (re)rendered so the caller can store its new
    signature. `art_path` is the page's own artifact (content-hashed by the
    caller); `dep_digest` captures its citation relationships (set-based) plus,
    where present, the content of cross-document layers rendered onto the page
    (site_cross_digests) and its current repeal status.
    `only`, a set of artifact path strings, restricts the run to those documents
    (a targeted `lagen <source> generate <id>`) -- the corpus-wide aggregate
    pages are then left untouched. `extra` appends pre-scoped (uri, source,
    path, title) page rows that have no catalog row (the sfs historical
    consolidations). `jobs>1` renders the stale pages across a process pool.
    Returns (total_pages, rendered) -- rendered < total when pages were
    skipped."""
    out_root = Path(out_root)
    con = catalog.connect(catalog_path)
    site = Site.from_catalog(con)
    rows = con.execute(
        "SELECT uri, source, path, title, content_hash FROM documents "
        "ORDER BY source, uri").fetchall()
    # stored paths are data_root-relative (portable catalog); resolve to absolute
    # here so `only`, the fresh/record callbacks and _write_page all work in
    # absolute paths, exactly as before. A stub's empty path stays empty.
    root = catalog.data_root(con)
    rows = [(uri, src, str(root / path) if path else path, title, chash)
            for (uri, src, path, title, chash) in rows]
    # the two scopes COMPOSE: `source` narrows to one source (incl. stubs),
    # `only` to specific artifacts. The editor's post-commit rebuild passes both
    # (one host page within a source); treating `source` as overriding `only`
    # made every editor checkout scan the whole source instead of rendering the
    # one dirty page.
    if source is not None:                       # whole-source scope (incl. stubs)
        rows = [r for r in rows if r[1] == source]
    if only is not None:                         # specific-document scope
        rows = [r for r in rows if r[2] in only]
    # commentary is an annotation rendered into statute rails, not a page of its own
    rows = [r for r in rows if r[1] != "kommentar"]
    # uncatalogued pages (sfs historical consolidations) carry no catalog row, so
    # no stored content_hash -- the caller re-hashes their artifact from disk (few)
    rows += [(uri, src, path, title, None) for (uri, src, path, title)
             in (extra or ())]

    # the whole-corpus dependency digests in one batched pass (not one pair of
    # subqueries per document -- the 124k-page planning loop); a link-less uri is
    # absent and takes the empty default
    deps = catalog.page_dependency_digests(con)
    # cross-document content (kommentar prose/.ann, remiss .ann, .corr rows)
    # renders onto OTHER documents' pages -- fold a per-host content digest into
    # the dependency digest so editing it re-renders the host page
    cross = site_cross_digests(site)
    # a repeal is presented against today's date (render_sfs marks the page
    # upphävd, facets drop it from browse), so a page's freshness must carry its
    # current in-force status: the day the date passes, the fold flips and the
    # page re-renders (rule:respect-source-temporality)
    expired = catalog.expired_uris(con, date.today().isoformat())

    # Freshness planning is single-threaded: it reads the catalog + manifest and
    # hashes inputs (the manifest lives here in the parent). Fresh pages advance
    # the counter at once; stale ones go to `plan` to be rendered (in parallel).
    total = len(rows)
    done = rendered = 0
    plan = []                # (uri, source, path, title, dep, chash) needing render
    # doc_relpath is not injective (begrepp/Första-hjälpen-tavlor and
    # begrepp/Första_hjälpen-tavlor both slug to one file), so two catalogued
    # uris colliding here would clobber each other's page -- and race on the
    # deterministic .tmp name under jobs>1. Refuse the plan instead.
    outs: dict = {}          # output relpath -> uri
    for (uri, src, path, title, chash) in rows:
        rel = doc_relpath(uri)
        if outs.setdefault(rel, uri) != uri:
            raise ValueError(
                "output path collision: %s and %s both render to %s -- fold the "
                "duplicate concept (an aliases: redirect on the wiki page) before "
                "generating" % (outs[rel], uri, rel))
        out = out_root / rel
        dep = deps.get(uri, catalog.EMPTY_DEP_DIGEST)
        if uri in cross or uri in expired:
            dep = hashlib.sha256(
                ("%s\x1f%s\x1f%s" % (dep, cross.get(uri, ""),
                                     "expired" if uri in expired else "")
                 ).encode()).hexdigest()
        if fresh and fresh(uri, out, path, dep, chash):
            done += 1
            if progress and done % 500 == 0:
                progress(done, total, catalog.local(uri), rendered)
        else:
            plan.append((uri, src, path, title, dep, chash))

    def finish(uri, path, dep, chash):
        nonlocal done, rendered
        done += 1
        rendered += 1
        if record:
            record(uri, path, dep, chash)
        if progress:
            progress(done, total, catalog.local(uri), rendered)

    if jobs > 1 and len(plan) > 1:
        with ProcessPoolExecutor(max_workers=jobs, initializer=_render_init,
                                 initargs=(catalog_path, out_root)) as pool:
            futures = {pool.submit(_render_one, job[:4]): job for job in plan}
            for fut in as_completed(futures):
                fut.result()                 # propagate a render error (abort)
                uri, src, path, title, dep, chash = futures[fut]
                finish(uri, path, dep, chash)
    else:
        for (uri, src, path, title, dep, chash) in plan:
            _write_page(uri, src, path, title, site, out_root)
            finish(uri, path, dep, chash)

    if only is None and source is None:          # corpus-wide pages on a full run
        render_aggregates(con, out_root, catalog_path, write_index=write_index)
    if progress:
        progress(total, total, "", rendered)
    con.close()
    return total, rendered


# The browser chrome from lib/assets/, in the order the page loads them: dom.js
# defines window.lagenDom (the shared vocabulary the others build on) and MUST
# come first; the rest are order-independent IIFEs, editor.js last. They are
# concatenated into one script.js so the page links a single URL -- adding a
# module changes only script.js, never the per-page HTML, so a new script ships
# as an --assets-only refresh instead of forcing a full corpus regenerate.
SCRIPT_FILES = ("dom.js", "scrollspy.js", "search.js", "popover.js",
                "fullsearch.js", "versions.js", "faksimil.js", "drawers.js",
                "editor.js")
SCRIPT_BUNDLE = "script.js"     # the single served URL (render.PAGE links it)


def bundled_script():
    """The concatenated script.js: every lib/assets JS file in load order, each
    behind a banner comment so a stack trace or view-source still names its origin
    file. The files are self-contained IIFEs, so concatenation is order-preserving
    and semantically identical to the former one-tag-per-file loading."""
    return "\n".join("/* === %s === */\n%s" % (name,
                     (ASSETS / name).read_text(encoding="utf-8"))
                     for name in SCRIPT_FILES)


def write_assets(out_root):
    """Copy the static browser chrome (lib/assets/) into the generated tree -- the
    concatenated script.js bundle, robots.txt, and the stylesheet (reader CSS with
    the editor layer appended). Depends on nothing but the asset files, so it is
    the whole of an asset-only refresh (`lagen all generate --assets-only`) after
    a CSS/JS change -- no catalog, no relate, no HTML re-render. Rides the same
    precompression as the pages (nginx serves the .br/.gz as-is); tiny files stay
    plain via the size floor in compress.write."""
    out_root = Path(out_root)
    out_root.mkdir(parents=True, exist_ok=True)
    compress.write_text(out_root / SCRIPT_BUNDLE, bundled_script(),
                        encodings=compress.PAGE_ENCODINGS)
    compress.write_text(out_root / "robots.txt",
                        (ASSETS / "robots.txt").read_text(encoding="utf-8"),
                        encodings=compress.PAGE_ENCODINGS)
    # style.css ships the reader stylesheet with the editor layer appended -- one
    # request, and the editor rules are inert without a logged-in session.
    compress.write_text(out_root / "style.css",
                        (ASSETS / "style.css").read_text(encoding="utf-8")
                        + (ASSETS / "editor.css").read_text(encoding="utf-8"),
                        encodings=compress.PAGE_ENCODINGS)


def render_aggregates(con, out_root, catalog_path, write_index=True):
    """Write the corpus-wide pages -- stylesheet, scripts, frontpage and the
    per-source faceted browse -- from the catalog. They depend on the whole
    document set (not on any single artifact), so they are cheap and always
    rebuilt; `lagen all generate --aggregates-only` runs just this, skipping the
    per-document render. The browse pages are written through the REST API (an
    in-process client over `catalog_path`), the frontpage from the catalog.
    `write_index=False` skips the generic corpus-stats frontpage -- the caller
    (build.cmd_generate) then writes a curated editorial frontpage in its place,
    so this never write-then-clobbers `index.html`."""
    out_root = Path(out_root)
    out_root.mkdir(parents=True, exist_ok=True)
    write_assets(out_root)
    if write_index:
        compress.write_text(out_root / "index.html", render_index(con),
                            encodings=compress.PAGE_ENCODINGS)
    folkratt_dir = out_root / "folkratt"
    folkratt_dir.mkdir(parents=True, exist_ok=True)
    compress.write_text(folkratt_dir / "index.html", render_folkratt(con),
                        encodings=compress.PAGE_ENCODINGS)
    search_dir = out_root / "sok"
    search_dir.mkdir(parents=True, exist_ok=True)
    compress.write_text(search_dir / "index.html", render_search_page(),
                        encodings=compress.PAGE_ENCODINGS)
    admin_dir = out_root / "admin"
    admin_dir.mkdir(parents=True, exist_ok=True)
    compress.write_text(admin_dir / "index.html", render_admin_page(),
                        encodings=compress.PAGE_ENCODINGS)
    # The legacy feed directory and per-repository feeds. Query-parameter
    # variants are rendered live by api/app.py; these unfiltered copies keep the
    # generated tree independently publishable at the same stable URLs.
    feed_index = out_root / "dataset" / "sitenews"
    feed_index.mkdir(parents=True, exist_ok=True)
    compress.write_text(feed_index / "index.html", render_feed_index(con),
                        encodings=compress.PAGE_ENCODINGS)
    for item in feeds.DATASETS:
        entries = feeds.entries(con, item)
        target = out_root / "dataset" / item.alias
        (target / "feed").mkdir(parents=True, exist_ok=True)
        compress.write_text(target / "feed.atom", feeds.render_atom(item, entries),
                            encodings=compress.PAGE_ENCODINGS)
        compress.write_text(target / "feed" / "index.html",
                            render_feed_page(item, entries),
                            encodings=compress.PAGE_ENCODINGS)
    folk_axis = _folkratt_axis(con)
    client = _browse_client(catalog_path)
    try:
        for source in catalog.counts(con):
            # kommentar is an annotation layer, not a browsable source; coe's
            # instruments are listed in full on the folkrätt landing instead of
            # a faceted-by-year tree of their own
            if source in ("kommentar", "coe"):
                continue
            # hudoc browses under /folkratt/hudoc/ and carries the shared folkrätt
            # selector; every other source browses on its own
            generate_browse(client, source, out_root,
                            folk_axis=folk_axis if source == "hudoc" else None)
    finally:
        api_service.app.dependency_overrides.pop(api_service.get_con, None)
