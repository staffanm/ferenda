"""Walk the artifact trees once and reduce each document to a compact fact row.

The measurements need per-document and per-node numbers the catalog does not
hold (a law's character count, its amendment register, every paragraf's length),
so ``stats compute`` has to read the artifacts. This module is that read, kept
separate from `compute` so the expensive part is one place and one shape: each
`scan_*` returns plain tuples/dicts, small enough that a whole corpus of them
fits in memory while the measures are assembled.

Everything here is pure and process-safe -- the per-document functions are
mapped over a `ProcessPoolExecutor` (the SFS tree alone is 42 399 files, ~4 min
serial), so they must not touch module state.

Two measurement rules are enforced here rather than left to each caller, because
getting either wrong silently poisons a whole family of numbers:

* **Table cells count as text.** A definition paragraf whose body is a table
  ("*I denna lag betyder*" + table) measures 19 characters if only ``text`` runs
  are read, and would win "shortest paragraf" outright.
* **Provenance markers do not.** A repealed paragraf keeps only its "*Lag
  (2011:590).*" trailer; counted naively it is the shortest rule in Swedish law.
"""

import json
import re

from ..lib import compress, layout
from ..lib.eu_structure import CASELAW, doctype
from ..lib.pinpoint import human_fragment

# a trailing "Lag (2011:590)." / "Förordning (2019:12)." provenance marker: the
# amendment that last touched the node, not part of its rule
RE_PROVENANCE = re.compile(
    r"\s*(?:Lag|Förordning|Kungörelse|Tillkännagivande|Balk|Stadga)"
    r"\s*\(\d{4}:[^)]+\)\.?\s*$")
# "Ny beteckning 2 §." / "Har betecknats 23 a §" -- a renumbering stub, which is
# a pointer rather than a rule and must not compete for "shortest paragraf"
RE_RENUMBERED = re.compile(r"^(?:Ny beteckning|Har betecknats|Tidigare|Förutvarande)\b")
# the SFS beteckning inside a title ("Ellag (1997:857)"), and the renderer's
# temporal markers ("/Rubriken upphör att gälla U:2027-01-01 ")
RE_BETECKNING = re.compile(r"\s*\((?:\d{4}:[^)]*)\)\s*")
RE_TITLE_MARKER = re.compile(r"/[^/]*?(?:upphör att gälla|träder i kraft)[^/]*/?\s*")
# "Lag (2025:191) om ändring i lagen (2022:201) om ändring i lagen (…) om …"
RE_CHAIN = re.compile(r"om ändring i ", re.I)


def load(path):
    """The artifact at `path`, or None when it is a zero-byte SkipDocument
    placeholder. Empty artifacts are the pipeline's documented way of recording
    "this basefile produced no document" (`catalog.rebuild` skips them the same
    way), so a scan must not read one as a broken file."""
    if compress.stat(path).st_size == 0:
        return None
    return json.loads(compress.read_text(path))


def _run_text(runs):
    """One run list flattened. A run is either a bare string or a link/reference
    object carrying its display text under ``text``."""
    if isinstance(runs, str):
        return runs
    return "".join(r if isinstance(r, str) else r.get("text", "") for r in runs)


def _runs_text(node):
    """The node's own inline text, table cells included.

    A table row (``rad``) holds ``cells``, and each cell is itself a *run list*,
    not a string -- so the cells are two levels deep, which is what makes the
    naive read of them come back empty. Cells are joined on a space because a
    cell boundary is a real one: run together, "dråp;" and "Mord" read as one
    word."""
    return " ".join([_run_text(node.get("text") or ""),
                     *(_run_text(cell) for cell in node.get("cells") or [])]).strip()


def _subtree_text(node):
    """A node's text with its subtree, minus the publisher's editorial notes --
    the text of the law itself.

    A ``redaktionell`` node (sfs/nf.py) is a repeal notice or a "text finns bara
    i tryckt version" gap standing where statute text would be. Counting it is
    what made every row of "de kortaste lagarna" an editorial note rather than a
    short law, and what let a repealed paragraf's stub compete for "kortaste
    paragrafen". The exclusion lives here rather than in a second walker beside
    this one: the other caller (`scan_eurlex`) can never see the type, so one
    function serves both and there is no pair to keep in lockstep."""
    if node.get("type") == "redaktionell":
        return ""
    parts = [_runs_text(node)]
    for child in node.get("children") or []:
        parts.append(_subtree_text(child))
    return " ".join(p for p in parts if p)


def _clean_title(title):
    """A statute title with neither its beteckning nor the temporal markers --
    what "longest/shortest title" must actually measure ("Ellag (1997:857)" is a
    five-character title, not a sixteen-character one)."""
    return RE_BETECKNING.sub(" ", RE_TITLE_MARKER.sub("", title)).strip()


# --------------------------------------------------------------------------
# SFS
# --------------------------------------------------------------------------

def scan_sfs(path):
    """One consolidated statute -> its shape, its title forms and its whole
    amendment register. Historical consolidations (``…/konsolidering/…``) are
    counted only as versions -- they are the same law at another moment, and
    letting them into a "longest law" ranking would list one statute ten times."""
    art = load(path)
    if art is None:
        return {"kind": "skipped"}
    uri = art.get("uri") or ""
    props = art.get("metadata", {}).get("properties", {})
    if "/konsolidering/" in uri:
        return {"kind": "version", "of": uri.split("/konsolidering")[0]}

    chars = paragrafer = kapitel = stycken = 0
    lengths = []                       # (chars, anchor, beteckning) per paragraf

    def walk(node):
        nonlocal chars, paragrafer, kapitel, stycken
        kind = node.get("type")
        if kind == "redaktionell":
            return          # the publisher's note, not the statute's text
        chars += len(_runs_text(node))
        if kind == "kapitel":
            kapitel += 1
        elif kind == "stycke":
            stycken += 1
        elif kind == "paragraf":
            paragrafer += 1
            body = RE_PROVENANCE.sub("", _subtree_text(node)).strip()
            # a paragraf left with nothing (its whole body was an editorial
            # note) states no rule, so it contributes no length measurement --
            # the same treatment the renumbering stub beside it gets. Counted,
            # its zero would both drag the median down and stand as "the
            # shortest paragraf"
            if body and not RE_RENUMBERED.match(body):
                # the beteckning has to carry the chapter: "62 §" of a chaptered
                # statute is not a citable reference, and the anchor is the one
                # place the chapter survives (K9P62 -> "9 kap. 62 §"). No
                # fallback to the bare ordinal: that is the unciteable form this
                # line exists to avoid, and an unnamed row is worse than none.
                where = human_fragment(node.get("id"))
                if where:
                    lengths.append((len(body), node["id"], where))
        for child in node.get("children") or []:
            walk(child)

    for block in art.get("structure") or []:
        walk(block)

    amendments = []
    for entry in art.get("amendments") or []:
        p = entry.get("properties", {})
        amendments.append({
            "id": p.get("dcterms:identifier"),
            "ikraft": p.get("rpubl:ikrafttradandedatum"),
            "utfardad": p.get("rpubl:utfardandedatum"),
            "omfattning": p.get("rpubl:andrar") or "",
            "forarbeten": entry.get("forarbeten") or [],
            # `inforsI` is where a *new* paragraf was inserted; `ersatter` is
            # where an existing one was rewritten. They are different questions
            # and the register fills them at very different rates (28 % vs 91 %),
            # so a measure must name which one it counts.
            "inforsI": p.get("rpubl:inforsI") or [],
            "ersatter": p.get("rpubl:ersatter") or [],
            "celex": p.get("rpubl:celexNummer"),
        })

    title = props.get("dcterms:title") or ""
    return {
        "kind": "law",
        "uri": uri,
        "title": title,
        "clean_title": _clean_title(title),
        "alternate": props.get("dcterms:alternate"),
        "department": (props.get("dcterms:creator") or "").rsplit("/", 1)[-1],
        "ikraft": props.get("rpubl:ikrafttradandedatum"),
        # the day the statute was signed. Carried only by the *base* statute:
        # the amendment register records an ikraftträdandedatum but no
        # utfärdandedatum (11 of 50 948 entries), and the download tree has none
        # either -- so any utfärdande→ikraftträdande measure is a measure of
        # grundförfattningar, not of changes.
        "utfardad": props.get("rpubl:utfardandedatum"),
        "chars": chars, "paragrafer": paragrafer,
        "kapitel": kapitel, "stycken": stycken,
        "paragraf_lengths": lengths,
        "amendments": amendments,
    }


def scan_sfs_register(path):
    """One downloaded SFS register record -> its change acts' titles, which the
    artifact does not carry. The title is the only place the "lag om ändring i
    lagen om ändring i …" chain is written down, so measuring its depth means
    reading the download tree (PRD "Vad som saknas", point 2)."""
    rec = load(path)
    if rec is None:
        return []
    out = []
    for entry in rec.get("andringsforfattningar") or []:
        rubrik = entry.get("rubrik") or ""
        out.append((entry.get("beteckning"), rubrik, len(RE_CHAIN.findall(rubrik))))
    return out


# --------------------------------------------------------------------------
# EU law
# --------------------------------------------------------------------------

# The article-length measures cover **sector 1 (treaties) and sector 3
# (legislation) only**. A judgment or an Advocate General's opinion has no
# articles of its own -- what looks like one is the contested act quoted inside
# it -- so sector 6 is not a smaller sample of the same population, it is a
# different population, and including it measures the parser rather than the law.
EU_SECTORS = ("1", "3")


def scan_eurlex(path):
    """One EU act -> its article lengths. Returns None for anything outside
    sectors 1 and 3."""
    art = load(path)
    if art is None:
        return None
    celex = art.get("celex") or ""
    if not celex[:1] in EU_SECTORS or doctype(celex) in CASELAW:
        return None
    lengths = []
    nums = []

    def walk(node):
        if node.get("type") == "article":
            body = " ".join(_subtree_text(c) for c in node.get("children") or [])
            lengths.append((len(body.strip()), node.get("num")))
            nums.append(node.get("num"))
            return
        for child in node.get("children") or []:
            walk(child)

    for block in art.get("structure") or []:
        walk(block)
    return {"celex": celex, "doctype": art.get("doctype"),
            "title": art.get("title") or "", "date": art.get("date"),
            "lengths": lengths,
            "multi_instrument": _restarts(nums)}


def _restarts(nums):
    """Whether the article numbering restarts -- the tell that one CELEX document
    carries more than one instrument (an accession act is the treaty *plus* the
    act of accession *plus* the acts its annexes reproduce in full; a decision
    may quote an earlier decision's articles). Their articles are genuine, but
    they do not belong to one act, so a "longest article" ranking must be able to
    say so (PRD R5)."""
    seq = [int(m.group()) for n in nums if n and (m := re.match(r"\d+", str(n)))]
    return any(b <= a for a, b in zip(seq, seq[1:], strict=False))


# --------------------------------------------------------------------------
# förarbete + court decisions
# --------------------------------------------------------------------------

def scan_forarbete(path):
    """One preparatory work -> its thickness. Page numbers are deliberately not
    returned: an OCR'd body yields nonsense (SOU 1996:165's highest page number
    reads as 9005), so character count is the only honest thickness measure."""
    art = load(path)
    if art is None:
        return None
    chars = 0

    def walk(node):
        nonlocal chars
        chars += len(_runs_text(node))
        for child in node.get("children") or []:
            walk(child)

    for block in art.get("structure") or []:
        walk(block)
    return {"uri": art.get("uri"), "type": art.get("type"),
            "identifier": art.get("identifier"), "title": art.get("title") or "",
            "date": art.get("date"), "chars": chars}


def scan_dv(path):
    """One court decision -> its length and whether it carries a curated name."""
    art = load(path)
    if art is None:
        return None
    chars = 0

    def walk(node):
        nonlocal chars
        chars += len(_runs_text(node))
        for child in node.get("children") or []:
            walk(child)

    for block in art.get("structure") or art.get("body") or []:
        walk(block)
    return {"uri": art.get("uri"), "chars": chars,
            "label": art.get("label") or "", "court": art.get("court_namn") or ""}


# --------------------------------------------------------------------------
# remisser (no catalog rows -- the artifact tree is the only inventory)
# --------------------------------------------------------------------------

def scan_remisser():
    """(ärende -> [(organisation, ärendetitel)]) off the artifact tree.
    Consultation answers are not catalogued, so unlike every other source here
    this one is counted by reading files rather than by SQL (PRD post 51). The
    organisation comes from inside the artifact, not from the filename: the
    filename is a slug that has already lost its diacritics ("regelradet"), and
    a statistics page that renames Regelrådet is a statistics page nobody trusts."""
    root = layout.ARTIFACT / "remisser"
    cases = {}
    for path in sorted(root.rglob("*.json*")):
        art = load(path)
        if art is None:
            continue
        cases.setdefault(art["arende_basefile"], []).append(
            (art.get("organisation") or "", art.get("arende_titel") or ""))
    return cases
