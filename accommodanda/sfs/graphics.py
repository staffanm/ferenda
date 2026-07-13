"""Detect content the SFST text database omits but the published SFS carries.

The consolidated text the corpus is built from is text-only, so graphics,
equations, tables, maps and symbols present in the officially published SFS are
dropped. Two in-corpus signals mark the loss:

- an editorial ``<noun> är inte med här`` marker ("the <noun> is not included
  here"), with or without slash delimiters and sometimes followed by amendment
  provenance. It is normally its own stycke; a few trail a ``Bilaga N`` heading.
- the Vägmärkesförordning (2007:90), which carries no marker at all: it lists
  road signs by designator (A1, P11, …) in tables whose image column was
  dropped, so a designator cell *is* the trace of a missing sign.

Detection is a deterministic semantic overlay computed at projection time
(nf.py), exactly like the reference links: the typed model keeps the raw marker
text; the normal form replaces it with a typed ``grafik`` node carrying the
omission ``sort`` and ``satt_av`` -- the amending SFS that last set the
surrounding wording, i.e. which published PDF's graphic is currently in force
and must be cropped. ``(Författningstext saknas)`` is deliberately NOT a signal
here: it is a code-generated empty-body placeholder (extract.py), not an
omitted graphic.
"""

import hashlib
import json
import re
from datetime import date, datetime

from .. import config
from ..lib import facsimile, llm, render
from .tokenizer import re_ChangeNote

# The source is inconsistent about its editorial delimiters. All of these occur
# in the corpus: `/Formeln är inte med här/`, `Formeln är inte med här.`,
# `Bilaga 2 är inte med här` and `/Bilagan är inte med här. Bilagan senast
# ändrad genom lag (2013:1017)./`. Capture the human label; marker_gap separately
# requires the *whole block* to consist of the marker plus known editorial
# provenance, so this deliberately permissive search cannot eat real prose.
_DESIGNATOR = (r"(?:\d+[a-z]?|[A-ZÅÄÖ]|[IVX]+)"
               r"(?:\s*[-–]\s*(?:\d+[a-z]?|[A-ZÅÄÖ]|[IVX]+))?"
               r"(?:\s*(?:,|och)\s*(?:\d+[a-z]?|[A-ZÅÄÖ]|[IVX]+))*")
MARKER_RE = re.compile(
    r"(?P<label>[A-ZÅÄÖa-zåäö]+(?:\s+%s)?)\s+är inte med här\s*\.?"
    % _DESIGNATOR, re.I)

# noun head (lowercased) -> canonical omission sort. An unlisted noun still
# yields a typed gap (its lowercased self), so a new variant degrades to a
# generic graphic rather than silently vanishing back into the text.
_SORT = {
    "bilaga": "bilaga", "bilagan": "bilaga", "bilagorna": "bilaga",
    "bilden": "bild", "kartan": "karta",
    "figuren": "figur", "formeln": "formel", "symbolen": "symbol",
    "specialtecken": "specialtecken", "förteckningen": "forteckning",
    "tabellen": "tabell", "tabellerna": "tabell",
    "sammanställningen": "tabell", "uppställningen": "tabell",
    "tillägg": "bilaga",
}

_EDITORIAL_SENTENCE_RE = re.compile(
    r"(?:Bilag(?:an|a(?:\s+\S+)?)\s+)?(?:senast\s+)?"
    r"(?:ändrad|införd)\s+genom\s+"
    r"(?:(?:lag|förordning)\s+\(\d{4}:\d+\)|SFS\s+\d{4}:\d+)|"
    r"Rättelseblad\s+\d{4}:\d+\s+har\s+iakttagits", re.I)
_MARKER_PROVENANCE_RE = re.compile(
    r"(?:lag|förordning)\s+\((\d{4}:\d+)\)|SFS\s+(\d{4}:\d+)", re.I)

# a road-sign designator opening a table cell: a letter (incl. Å/Ä/Ö) + number,
# optional lowercase suffix (A1, C31, X2, P11, A13a). Chapter 3 (Signalbild --
# colour names) and chapter 1 (definitions) never match, so they are excluded
# without special-casing.
ROADSIGN_RE = re.compile(r"^[A-ZÅÄÖ]\d+[a-z]?\b")

# statutes whose tables list omitted road signs by designator (no marker). A
# set, not a hardcoded ==, so a republished-signs amendment can join later.
ROADSIGN_DOCS = frozenset({"2007:90"})


def marker_sort(noun):
    head = noun.split()[0].lower()
    return _SORT.get(head, head)


def changenote_sfs(text):
    """The ``YYYY:N`` of a trailing ``Lag/Förordning (YYYY:N).`` change note in
    `text`, or None -- the amendment that last set this wording."""
    m = re_ChangeNote.search(text.strip())
    if not m:
        return None
    num = re.search(r"\d{4}:\d+", m.group())
    assert num, "change note %r carries no SFS number" % m.group()
    return num.group()


def _marker_rest(text, marker):
    """Text outside `marker`, stripped of the source's slash/paren wrapper."""
    rest = (text[:marker.start()] + " " + text[marker.end():]).replace("/", " ").strip()
    if rest.startswith("(") and rest.endswith(")"):
        rest = rest[1:-1].strip()
    return rest


def _editorial_only(text):
    """Whether `text` is only recognized source-editor provenance sentences."""
    if not text:
        return True
    note = re_ChangeNote.fullmatch(text)
    if note:
        return True
    return all(_EDITORIAL_SENTENCE_RE.fullmatch(part.strip(" /,.").strip())
               for part in re.split(r"\.\s+|,\s+(?=[Bb]ilagan\b)", text)
               if part.strip(" /,."))


def marker_provenance(text):
    """The explicit amendment SFS in an omission marker's editorial tail."""
    found = _MARKER_PROVENANCE_RE.findall(text)
    return next((a or b for a, b in reversed(found)), None)


def marker_gap(text):
    """If `text` is *only* an omission marker (optionally followed by a change
    note), return ``(sort, satt_av)``; else None. Standalone markers are the
    dominant corpus shape (each `/Formeln.../` is its own stycke). A marker
    embedded in real prose returns None so no surrounding text is lost."""
    m = MARKER_RE.search(text)
    if not m:
        return None
    rest = _marker_rest(text, m)
    if not _editorial_only(rest):
        return None
    return marker_sort(m.group("label")), marker_provenance(text)


def heading_gap(text):
    """Split a marker trailing a heading (`Bilaga 1 /Bilagan är inte med här/`).
    Returns ``(clean_heading, sort)`` when a marker follows non-marker text, so
    the heading stays and the gap becomes a sibling grafik; else ``(text,
    None)``."""
    m = MARKER_RE.search(text)
    if not m:
        return text, None
    clean = text[:m.start()].strip(" /(")
    tail = text[m.end():].strip().removeprefix("/").removesuffix("/").strip()
    if not clean or not _editorial_only(tail):
        return text, None  # a bare marker is a marker_gap, not a heading
    return clean, marker_sort(m.group("label"))


def roadsign_code(cell_text):
    """The road-sign designator opening `cell_text` (A1, P11, …), or None."""
    m = ROADSIGN_RE.match(cell_text)
    return m.group() if m else None


def governing_sfs(children):
    """The last change-note SFS among a container's direct stycke children -- the
    amendment that set the container's current wording, inherited by any gap
    inside it that carries no note of its own (the first four of 2002:780's five
    formula stycken; a road-sign table's paragraf). None when unamended (a base
    act provision -- crop the base act's own PDF)."""
    note = None
    for node in children:
        text = getattr(node, "text", None)
        if isinstance(text, str):
            found = changenote_sfs(text)
            if found:
                note = found
    return note


# ---------------------------------------------------------------------------
# Gap collection: harvest the typed grafik gaps back out of a parsed artifact's
# normal form, each with the context the localization pass needs -- the
# enclosing bilaga ordinal (register-first provenance) and a short anchor
# snippet (nearest preceding text / the road-sign row) so the vision model can
# find the graphic on the page without ever anchoring into flowing plaintext.
# ---------------------------------------------------------------------------

_BILAGA_ORD_RE = re.compile(r"[Bb]ilaga\s+(\d+(?:\s*[a-z](?:\.\d+)?)?|[A-ZÅÄÖ])")


def _node_text(node):
    """Plain text of a node carrying `text` runs (stycke, rubrik), else ''."""
    runs = node.get("text")
    return render.plain(runs) if runs else ""


def _rad_text(node):
    """Plain text of a table row -- its cells joined; the road-sign anchor."""
    return " ".join(render.plain(c) for c in node.get("cells", [])).strip()


def _bilaga_ordinal(bilaga_node):
    """The ``Bilaga N`` number of a bilaga node (read from its rubrik child),
    or None for an unnumbered single bilaga."""
    for kid in bilaga_node.get("children", []):
        if kid.get("type") == "rubrik":
            m = _BILAGA_ORD_RE.search(_node_text(kid))
            if not m:
                return None
            value = m.group(1)
            return int(value) if value.isdigit() else _norm_identity_text(value)
    return None


def _norm_identity_text(text):
    return " ".join(text.casefold().split())


def _container_segment(node):
    """Stable semantic address component for an id-bearing/container node.

    Generated NF ids are positional and therefore deliberately excluded. The
    legal ordinal or heading is the identity; a temporal duplicate of the same
    appendix consequently gets the same segment and can share one crop.
    """
    kind = node.get("type")
    ordinal = node.get("ordinal")
    if ordinal:
        return "%s:%s" % (kind, _norm_identity_text(str(ordinal)))
    if kind in {"bilaga", "avdelning", "underavdelning"}:
        heading = next((_node_text(kid) for kid in node.get("children", [])
                        if kid.get("type") == "rubrik" and _node_text(kid)), "")
        return "%s:%s" % (kind, _norm_identity_text(heading or kind))
    if kind == "stycke" and node.get("children"):
        return "stycke:%s" % _norm_identity_text(_node_text(node))
    return kind if node.get("children") and kind not in {"tabell"} else None


def _identity(node, path, anchor, occurrence):
    return {
        "path": list(path),
        "sort": node["sort"],
        "code": node.get("code"),
        "anchor": _norm_identity_text(anchor),
        "occurrence": occurrence,
    }


def _identity_key(identity):
    raw = json.dumps(identity, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":")).encode()
    return "g-" + hashlib.sha256(raw).hexdigest()[:20]


def _scan_gaps(structure, assign=False):
    """Return semantic gap records, optionally stamping their key on NF nodes.

    Occurrence is counted among equal gaps *within one children list*. Thus the
    two maps following the same heading stay distinct, while a second temporal
    copy of the same `Bilaga 1` repeats the same local sequence and aliases the
    same published graphic instead of asking vision to locate it twice.
    """
    gaps = []

    def walk(nodes, path, bilaga_ord, in_bilaga):
        prev = ""
        occurrences = {}
        for node in nodes:
            kind = node.get("type")
            graphic = (node if kind == "grafik" else
                       node.get("grafik") if kind == "rad" else None)
            if graphic:
                anchor = _rad_text(node) if kind == "rad" else prev
                basis = (graphic["sort"], graphic.get("code"),
                         _norm_identity_text(anchor))
                occurrences[basis] = occurrences.get(basis, 0) + 1
                identity = _identity(graphic, path, anchor, occurrences[basis])
                key = _identity_key(identity)
                if assign:
                    graphic["key"] = key
                elif graphic.get("key"):
                    assert graphic["key"] == key, \
                        "%s: stored gap key does not match semantic identity" % graphic["id"]
                gaps.append({"id": graphic["id"], "key": key,
                             "identity": identity, "sort": graphic["sort"],
                             "satt_av": graphic.get("satt_av"),
                             "code": graphic.get("code"),
                             "bilaga_ordinal": bilaga_ord,
                             "in_bilaga": in_bilaga, "anchor": anchor})
            segment = _container_segment(node)
            subpath = path + ((segment,) if segment else ())
            sub_bilaga = _bilaga_ordinal(node) if kind == "bilaga" else bilaga_ord
            walk(node.get("children", []), subpath, sub_bilaga,
                 in_bilaga or kind == "bilaga")
            prev = _node_text(node) or prev

    walk(structure, (), None, False)
    return gaps


def assign_gap_keys(structure):
    """Stamp every artifact graphic with its stable semantic ``key``."""
    _scan_gaps(structure, assign=True)
    return structure


def collect_gaps(structure):
    """Every graphic gap in an artifact normal-form `structure`, in document
    order. A block ``grafik`` node anchors on its nearest preceding sibling
    text (the ``1 Balanstalet, BT`` stycke before a ``/Formeln.../`` gap, or a
    ``Bilaga N`` rubrik); a road-sign row's grafik anchors on the row cells."""
    return _scan_gaps(structure)


# ---------------------------------------------------------------------------
# Provenance: which officially published PDF carries a gap's *in-force* graphic.
# Resolved deterministically (never a vision problem) so the localization pass
# renders only the right PDF's pages.
# ---------------------------------------------------------------------------

def _sfs_key(beteckning):
    """(year, löpnr) sort key -- 'latest' among the register's amendments."""
    year, _, nr = beteckning.partition(":")
    m = re.match(r"\d+", nr)
    return (int(year), int(m.group()) if m else 0)


def _touches_bilaga(note, ordinal):
    """Does an Omfattning clause publish new content for bilaga `ordinal`?

    Only ``ändr.`` and ``ny`` clauses carry a replacement graphic. Merely
    mentioning a bilaga in ``upph.`` or ``betecknas`` must not redirect the crop
    to a PDF that contains no graphic.
    """
    target = (r"\bbil\.(?!\s*(?:\d|[A-ZÅÄÖ]))" if ordinal is None
              else r"\bbil\.\s*%s(?![\w.])" % re.escape(str(ordinal)))
    return any(re.search(r"(?:^|\s)(?:ändr\.|ny)(?:\s|$)", clause)
               and re.search(target, clause, re.I)
               for clause in note.split(";"))


def _in_force(amendment, as_of):
    value = amendment.get("ikraftDateTime")
    if not value:
        return True
    return datetime.fromisoformat(value).date() <= as_of


def latest_bilaga_amender(register, ordinal, as_of=None):
    """The latest SFS whose register note changes bilaga `ordinal`, or None.
    A wholesale-replaced map/appendix bilaga leaves NO trailing change note in
    the consolidated text, so the register `Omfattning` is authoritative here,
    not the Phase-1 `satt_av` hint (2004:629: bil. 1 -> 2023:395, bil. 2 ->
    2020:120, two independent histories). 'Latest' names the bilaga explicitly,
    so no betecknas back-mapping is needed to get the *current* graphic: older
    bare-``bil.`` touches predate any renumbering and are never the latest."""
    best = None
    as_of = as_of or date.today()
    for af in register.get("andringsforfattningar") or []:
        bet = af.get("beteckning")
        if (bet and _in_force(af, as_of)
                and _touches_bilaga(af.get("anteckningar") or "", ordinal)):
            if best is None or _sfs_key(bet) > _sfs_key(best):
                best = bet
    return best


def provenance_sfs(gap, register, base):
    """The SFS whose published PDF carries this gap's in-force graphic:

    - a gap **inside a bilaga** (a map/figure/table appendix, whatever
      the marker noun): register-first -- the LATEST of the register's
      ``ändr. bil. N`` clauses and the gap's own change-note ``satt_av``. A
      wholesale-replaced appendix usually leaves no in-text note, so the register
      is authoritative; but when a note IS present and newer, it wins (max). This
      is keyed on the enclosing bilaga, NOT the marker noun -- 2004:629's maps
      are ``karta`` gaps, yet their provenance is bilaga 1 -> 2023:395 / bilaga
      2 -> 2020:120, the two independent appendix histories.
    - **any other** gap (a formula in the main text or a road sign): the
      change-note ``satt_av`` that set the surrounding wording, else the base
      act's own PDF."""
    if gap.get("in_bilaga", gap.get("bilaga_ordinal") is not None):
        candidates = [c for c in (latest_bilaga_amender(
            register, gap["bilaga_ordinal"]), gap.get("satt_av")) if c]
        if candidates:
            return max(candidates, key=_sfs_key)
    return gap.get("satt_av") or base


def register_latest_amendment(register, as_of=None):
    """The latest (year, löpnr) andringsforfattning in the register, or None --
    the provenance horizon a .graphics layer was authored against. Stamped in the
    layer's ``meta.through`` so a later run can see at a glance how far the corpus
    has moved since; the per-entry ``sfs`` is the authoritative per-gap record."""
    as_of = as_of or date.today()
    bets = [af["beteckning"] for af in register.get("andringsforfattningar") or []
            if af.get("beteckning") and _in_force(af, as_of)]
    return max(bets, key=_sfs_key) if bets else None


def plan_localization(gaps, existing, register, base):
    """Split `gaps` into (keep, todo) against the `existing` layer entries.

    - **keep**: ``{gap_key: entry}`` -- entries preserved verbatim. An entry is
      kept ONLY if it is ``verified`` AND its recorded source ``sfs`` still equals
      the freshly resolved provenance. A verified crop whose provenance has since
      moved to a newer amendment (a new ``ändr. bil. N`` in the register) is NOT
      kept -- its curation is against the wrong PDF now, so it must be re-localized
      and re-reviewed.
    - **todo**: ``{source_sfs: [gap, ...]}`` -- everything else (a new gap, a
      ``generated`` cache entry, or a provenance-drifted verified entry), grouped
      by the source PDF to render.

    Pure and deterministic -- the action calls it, so the merge policy lives with
    the source (not in the source-agnostic annstore)."""
    unique = {}
    for gap in gaps:
        prior = unique.setdefault(gap["key"], gap)
        assert prior["identity"] == gap["identity"], \
            "gap-key collision for %s" % gap["key"]
        assert provenance_sfs(prior, register, base) == provenance_sfs(
            gap, register, base), \
            "%s aliases gaps with different provenance" % gap["key"]

    keep, todo = {}, {}
    for key, gap in unique.items():
        prov = provenance_sfs(gap, register, base)
        ent = existing.get(key)
        if (ent and ent.get("verified") and ent.get("sfs") == prov
                and ent.get("identity") == gap["identity"]):
            keep[key] = ent
        else:
            todo.setdefault(prov, []).append(gap)
    return keep, todo


# ---------------------------------------------------------------------------
# Vision localization: one call per source PDF places each known gap at a page +
# pixel bbox, converted to raw PDF points. Only the type noun + anchor text the
# detector already knows go to the model (Phase-0 finding: a generic prompt in
# the image's own pixel space, plus a next-anchor boundary hint, is what a
# vision model can actually deliver; PDF-point coordinates and doc-specific
# hints were both worse). The per-entry verified layer absorbs the residual
# boundary drift.
# ---------------------------------------------------------------------------

# type noun (Phase-1 `sort`) -> a neutral description; nothing doc-specific
_SORT_DESC = {
    "formel": "en formel eller ekvation", "bild": "en bild eller illustration",
    "karta": "en karta", "figur": "en figur eller ett diagram",
    "tabell": "en tabell", "bilaga": "grafiskt innehåll i en bilaga",
    "symbol": "en symbol eller ett specialtecken",
    "forteckning": "en förteckning", "vagmarke": "en vägmärkesbild",
}

# how many page images to send in one vision call. A superset of "one call per
# source PDF" (user's refinement) that also scales to a many-page source (the
# 2007:90 road-sign PDF): the gaps are offered against each page chunk and the
# model omits those not shown, so results accumulate across chunks.
PAGES_PER_CALL = 6

# the vision model reasons before emitting the JSON (like gpt-oss); the
# endpoint's tiny default budget truncates that mid-answer (a `length` finish,
# which llm raises on). Give it generous headroom -- Kimi spent ~20k completion
# tokens localizing 5 formulas across 4 pages, almost all chain-of-thought over
# the images. The ceiling is free (billing tracks actual usage); it only has to
# clear the model's real appetite, which grows with images/gaps per chunk.
VISION_MAX_TOKENS = 32000

_PROMPT = """De bifogade bilderna är sidor ur en PDF från Svensk \
författningssamling (SFS %s). Bild N motsvarar %s. Bildmått: %s. \
Origo (0,0) är varje bilds ÖVRE VÄNSTRA hörn; x växer åt \
höger, y växer nedåt. Ange alla koordinater i BILDPIXLAR.

Den textbaserade (konsoliderade) versionen av författningen saknar grafiska \
element som finns i denna PDF. Nedan listas de saknade elementen; för varje \
anges typ och den textrad i den konsoliderade versionen som ligger närmast där \
elementet hör hemma. Leta upp varje element och ange den rektangel i bildpixlar \
som omsluter HELA elementet (inklusive den rubrik och förklaring som hör till \
just det elementet). Ett elements rektangel slutar där nästa listade elements \
text börjar. Om ett element inte finns på någon av de bifogade sidorna, utelämna \
det ur svaret.

Saknade element:
%s

Svara med ENBART giltig JSON, inga kodstaket, på formen:
{"G1": {"page": <sidnummer>, "bbox": [x0, y0, x1, y1], "alt": "<kort \
alt-text>"}, ...} där page är det ABSOLUTA sidnnumret enligt listan ovan och \
bbox är [vänster, topp, höger, botten] i BILDPIXLAR."""


def localization_prompt(gaps, pages, image_sizes, src):
    """The vision prompt for one chunk: which absolute page each image is, the
    image dimensions, and the gap list (id, generic type, anchor snippet)."""
    which = ", ".join("bild %d = sida %d" % (i + 1, p)
                      for i, p in enumerate(pages))
    dimensions = ", ".join("bild %d = %d×%d pixlar" % (i + 1, *image_sizes[p])
                           for i, p in enumerate(pages))
    listing = "\n".join(
        '- %s: typ = %s; närmaste text: "%s"'
        % (g["id"], _SORT_DESC.get(g["sort"], g["sort"]), g["anchor"])
        for g in gaps)
    return _PROMPT % (src, which, dimensions, listing)


def parse_localization(reply, gap_ids, src, pages=None, image_size=None,
                       already=None):
    """Validate a vision reply against the known `gap_ids` and convert each pixel
    bbox to raw PDF points (top-left, ``px * 72 / DPI``). Returns
    ``{gap_id: {sfs, page, bbox?, alt}}`` for the located gaps (an omitted gap is
    simply absent -- it was not on these pages). Raises ValueError on a malformed
    reply so `llm.author` retries (rule:errors-drive-retry-use-raise): an unknown
    id, a page outside the shown chunk, or a malformed/out-of-image bbox."""
    data = json.loads(llm.strip_fence(reply))
    if not isinstance(data, dict):
        raise ValueError("reply is not a JSON object")
    out = {}
    for gid, ent in data.items():
        if gid not in gap_ids:
            raise ValueError("unknown gap id %r (known: %s)"
                             % (gid, sorted(gap_ids)))
        if not isinstance(ent, dict):
            raise ValueError("%s: entry must be an object, got %r" % (gid, ent))
        page = ent.get("page")
        if isinstance(page, bool) or not isinstance(page, int) or page < 1:
            raise ValueError("%s: page must be a positive int, got %r"
                             % (gid, page))
        if pages is not None and page not in pages:
            raise ValueError("%s: page %d is not among shown pages %s"
                             % (gid, page, list(pages)))
        alt = ent.get("alt") or ""
        if not isinstance(alt, str):
            raise ValueError("%s: alt must be a string, got %r" % (gid, alt))
        rec = {"sfs": src, "page": page, "alt": alt}
        bbox = ent.get("bbox")
        if bbox is not None:
            if not facsimile.valid_bbox(bbox):
                raise ValueError("%s: bbox must be [x0,y0,x1,y1] numbers with "
                                 "positive ordered bounds, got %r" % (gid, bbox))
            x1, y1 = bbox[2], bbox[3]
            size = image_size.get(page) if isinstance(image_size, dict) else image_size
            if size and (x1 > size[0] or y1 > size[1]):
                raise ValueError("%s: bbox %r exceeds image %dx%d"
                                 % (gid, bbox, *size))
            rec["bbox"] = [round(v * 72 / facsimile.DPI) for v in bbox]
        if already and gid in already and already[gid] != rec:
            raise ValueError("%s: conflicts with localization from an earlier page"
                             % gid)
        out[gid] = rec
    return out


def _chunks(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def localize_group(gaps, pdf_path, src, model=None, author=llm.author):
    """Localize every gap resolved to one source PDF to a page + bbox, one
    vision call per page-chunk. `author` is injectable so the orchestration is
    testable without a live model. The short local ids are used only in prompts;
    the result is keyed by stable semantic gap key and must cover every gap."""
    model = model or config.VISION_MODEL
    gap_by_id = {g["id"]: g for g in gaps}
    assert len(gap_by_id) == len(gaps), "%s: duplicate prompt gap ids" % src
    gap_ids = set(gap_by_id)
    pages = list(range(1, facsimile.page_count(pdf_path) + 1))
    located = {}
    for chunk in _chunks(pages, PAGES_PER_CALL):
        images = [facsimile.cached_page("sfs", src, pdf_path, p).read_bytes()
                  for p in chunk]
        image_sizes = {page: facsimile.png_size(image)
                       for page, image in zip(chunk, images, strict=True)}
        remaining = [gap for gap in gaps if gap["id"] not in located]
        if not remaining:
            break
        prompt = localization_prompt(remaining, chunk, image_sizes, src)
        shown_ids = {gap["id"] for gap in remaining}

        def validate(reply, ids=shown_ids, shown=chunk, size=image_sizes):
            return parse_localization(reply, ids, src, pages=shown,
                                      image_size=size, already=located)

        located.update(author(
            prompt, validate,
            model=model, images=images, max_tokens=VISION_MAX_TOKENS))
    missing = gap_ids - located.keys()
    if missing:
        raise ValueError("%s: vision did not locate gap(s) %s on any PDF page"
                         % (src, ", ".join(sorted(missing))))
    return {gap_by_id[gid]["key"]: {**entry,
            "identity": gap_by_id[gid]["identity"]}
            for gid, entry in located.items()}
