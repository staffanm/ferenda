"""Extract a proposition's per-paragraf författningskommentar text.

The författningskommentar (FK) is the section-by-section commentary chapter
ending every proposition: for each proposed or amended paragraf it explains
what the provision does and why -- the primary interpretive aid for the
resulting law. This module slices that chapter into per-paragraf entries
({law, chapter, paragraf(s), commentary text}), the substrate for showing a
paragraf's commentary in the statute page's context rail and for downstream
consumers (`sfs.correspond`, `kommentar.extract`) that today re-derive weaker
views of the same text.

The chapter is written in three styles, all present in the curated corpus:

  A. *lagtext included* (prop 2017/18:89): the proposed paragraf text is
     quoted after a bold "N §" marker, then the commentary follows;
  B. *bare marker* (prop 2020/21:194): a "N §" (or "9 och 10 §§") heading,
     commentary directly below;
  C. *marker inline* (prop 2018/19:163): the commentary paragraph itself
     opens with the marker -- "1 § Paragrafen reglerar …".

Robustness rules, each locked to an observed failure in the curated props:

  * the chapter is bounded by *content* (the trailing protocol/bilaga
    matter), never by the next level-1 rubrik -- the parser tags in-FK
    chapter headings ("1 kap.", "2 a kap. Skyldigheter …") and short inline
    markers ("5 § I paragrafen …") as level-1 rubriks, which truncates a
    rubrik-arithmetic bound after a handful of blocks (every curated prop);
  * the FK heading itself may be lost to a stycke (prop 2017/18:269 reflows
    it to "Författningskommentar 18"), so a stycke matching just that text
    also opens the chapter;
  * a single-law prop may name its law in an unnumbered rubrik or plain
    stycke instead of a "16.1 Förslaget till …" level-2 rubrik
    (prop 2020/21:13);
  * "N §" markers missed by the parser's bold-marker detection are recovered
    from stycke/rubrik text, including combined markers ("9 och 10 §§",
    "5–7 §§") and markers buried mid-stycke after a merged heading
    ("… lämplighetsprövning 8 § Paragrafen är ny …");
  * footnote rubriks ("3 Senaste lydelse 2022:443.") are noise inside an
    entry, not entry boundaries.

Commentary is separated from quoted lagtext by *phrase*, not layout metrics
(the legacy CommentaryFinder's font-size/linespacing thresholds are exactly
what silently drops 2017/18:89): commentary opens with a closed set of
formulas ("Paragrafen …", "I paragrafen …", "(Jfr …", …), confirmed by the
"Övervägandena finns i avsnitt N" convention.

What this module does NOT do: resolve the law rubrik to an SFS uri or the
paragraf to a statute anchor -- that join needs the SFS corpus and runs at
relate time (`genomforande._resolve_law` + `kommentar.paragraf_fragment`,
the same machinery the genomför-direktiv edges use).
"""

import argparse
import json
import re

from ..lib import catalog, compress, layout
from ..lib.text import runs_text
from ..lib.util import normalize_fold
from . import genomforande, kommentar
from .parse import parse_record, to_artifact
from .structure import flatten

# a paragraf designator: "7", "18 a"
_MARK = r"\d{1,3}(?: ?[a-z])?"

# what may follow a marker for it to open an entry: nothing, an
# uppercase/digit start (lagtext or commentary), or one of the comment verbs
# ("12 kap. 4 § upphävs med anledning av …" -- a lowercase continuation is
# otherwise a citation, "8 § andra stycket gäller …", never a marker)
_AFTER = (r"\s*(?=$|[A-ZÅÄÖ0-9]"
          r"|(?:upphävs|ändras|införs|föreslås|byts|justeras|kompletteras"
          r"|träder|utgår)\b)")
RE_VERB_HEAD = re.compile(
    r"^(?:upphävs|ändras|införs|föreslås|byts|justeras|kompletteras"
    r"|träder|utgår)\b")

# "N §" opening a block
RE_PAR = re.compile(r"^(%s) ?§(?!§)%s" % (_MARK, _AFTER))

# "9 och 10 §§" / "5–7 §§" / "1, 2 och 4 §§" opening a block
RE_PAR_MULTI = re.compile(
    r"^(%s(?:\s*(?:,|och|[–—‐‒-])\s*%s)+) ?§§%s" % (_MARK, _MARK, _AFTER))

# a combined chapter+paragraf marker heading: "3 kap. 18 §" (prop 2000/01:48),
# "12 kap. 3 a § Paragrafen har utformats …" (prop 2003/04:24) -- sets the
# chapter AND opens the paragraf entry in one block
RE_KAP_PAR = re.compile(
    r"^(\d+(?: ?[a-z])?) ?[Kk]ap\.?,? (%s) ?§(?!§)%s" % (_MARK, _AFTER))

# a chapter marker heading: "1 kap.", "2 a kap. Skyldigheter …",
# "1 Kap. Lagens tillämpningsområde"
RE_KAP = re.compile(r"^(\d+(?: ?[a-z])?) ?[Kk]ap\b")

# a per-law rubrik: "16.1 Förslaget till säkerhetsskyddslag" (numbered, the
# normal case) or an unnumbered "Förslaget till lag om ändring i …" (single-law
# props write it as a bare rubrik or stycke)
RE_LAW = re.compile(r"^(?:\d+(?:\.\d+)+ )?förslag(?:et|en)? till ")
RE_LAW_BARE = re.compile(r"^förslag(?:et|en)? till (?:lag|förordning)\b")

# the FK chapter heading, tolerating the page-header marginalia the column
# merge can fuse into it ("7 Författningskommentar Prop. 1997:74"), and its
# stycke-reflowed form ("Författningskommentar 18" -- the parser loses the
# rubrik and reflows "18 Författningskommentar")
RE_FK_HEAD = re.compile(
    r"^(?:\d+ )?författningskommentar(?:er)?"
    r"(?: prop\.? ?\d{4}(?:/\d{2,4})?:\d+)?$"
    r"|^specialmotivering$")
RE_FK_STYCKE = re.compile(r"^författningskommentar(?:er)?(?: \d+)?$")

# trailing matter that ends the FK chapter. The reliable signal is the bilaga
# marginalia: the column merge stamps "Bilaga N" into the text of every
# appendix block ("Sammanfattning av betänkandet En ny Bilaga 1
# säkerhetsskyddslag …"), and FK prose never carries that capitalised token --
# verified across the whole curated corpus. The protocol extract and a
# rubrik-shaped bilaga heading ("1 Förslag till säkerhetsskyddslag", undotted
# number unlike the FK's own "16.1 Förslaget till …") are backstops.
RE_FK_END_ANY = re.compile(
    r"\bBilaga \d+\b|^Utdrag ur protokoll\b"
    r"|\bUtdrag ur protokoll vid regeringssammanträde\b")
RE_FK_END_RUBRIK = re.compile(
    r"^bilaga\b|^rättsdatablad\b|^\d+ förslag(?:et|en)? till ")

# a footnote of the quoted lagtext, tagged rubrik by the parser -- noise
# inside an entry, never a boundary
RE_FOOTNOTE = re.compile(r"^\d+ senaste lydelse\b|^\d+ jfr\b")

# commentary openers, strongest first: the fixed formulas FK prose starts a
# paragraf's commentary with. The weak set (Bestämmelsen/Ändringen/…) also
# occurs in lagtext, so it only counts when no strong opener follows anywhere
# in the entry; "Övervägandena finns i avsnitt N" is the last-resort confirmer.
RE_OPENER = re.compile(
    r"^\(?(?:Av p|I p|Enligt p|Genom p|P)aragraf(?:en|erna|ens)\b"
    r"|^I denna(?: nya)? paragraf\b"
    r"|^Genom (?:ändringen|ändringarna|bestämmelsen)\b"
    r"|^I bestämmelsen\b"
    r"|^Denna paragraf\b|^Förslag(?:et|en) har behandlats\b|^\(Jfr\b")
RE_OPENER_WEAK = re.compile(
    r"^\(?(?:Bestämmelsen|Bestämmelserna|Ändringen|Ändringarna|De ändringar)\b"
    r"|^I (?:första|andra|tredje|fjärde|femte) stycket\b"
    r"|^De(?:t)? nya\b|^En ändring\b|^I lagen införs\b")
RE_OVERVAG = re.compile(r"\bövervägandena (?:finns|behandlas) i avsnitt\b",
                        re.IGNORECASE)

# a law-level comment in an omnibus prop, directly under its law rubrik with
# no paragraf marker at all: "I 4 c § görs en ändring med anledning av …",
# "I 19 § ändras landstingsfullmäktige …", "I tilläggsbestämmelse 13.6.2
# finns bestämmelser om …" -- meta-language about the amendment, which
# lagtext never opens with
RE_LAWLEVEL = re.compile(
    r"^I .{0,120}?(?:görs|ändras|finns bestämmelser|byts .{0,60}? ut)")

# orphaned group commentary: "Paragraferna 2–4 beskriver …" after a subject
# rubrik, commenting a run of paragrafer at once -- the designators in the
# opening formula are the entry's own anchors
RE_GROUP = re.compile(
    r"^Paragraferna (%s(?:\s*(?:,|och|samt|[–—‐‒-])\s*%s)+)\b" % (_MARK, _MARK))

# a group comment that names no designators, closing a run of quoted
# paragrafer: "I paragraferna finns bestämmelser om …" after 5–10 §§ are
# quoted back to back, or the whole-law "De ändringar som föreslås i lagen
# är en följd av …". Such commentary covers the run, not just the paragraf
# it happens to trail, so the preceding all-lagtext entries fold into it.
RE_GROUP_KOMM = re.compile(
    r"^(?:(?:I paragraferna|Paragraferna)\b(?!\s*\d)"
    r"|De ändringar(?:na)? som föreslås\b)")

# a marker buried mid-stycke after a heading the parser merged into it
# ("… lämplighetsprövning 8 § Paragrafen är ny …"): only recovered when the
# text after the marker is unmistakably a commentary opener
RE_EMBEDDED = re.compile(
    r"\b(%s) ?§,? (?=\(?(?:Av p|I p|Enligt p|Genom p|P)aragrafen\b)" % _MARK)


def fk_span(blocks):
    """The block index range [start, end) of the författningskommentar chapter,
    or None. Opens at the FK level-1 rubrik (or its stycke-reflowed ghost) and
    runs to the trailing matter (protocol extract / bilagor / rättsdatablad) or
    the document end -- never bounded by rubrik levels, which in-FK chapter
    headings corrupt (see module docstring)."""
    start = next(
        (i for i, b in enumerate(blocks)
         if (b["type"] == "rubrik" and (b.get("level") or 1) == 1
             and RE_FK_HEAD.match(normalize_fold(runs_text(b["text"]))))
         or (b["type"] == "stycke"
             and RE_FK_STYCKE.match(normalize_fold(runs_text(b["text"]))))),
        None)
    if start is None:
        return None
    end = next((i for i in range(start + 1, len(blocks))
                if RE_FK_END_ANY.search(runs_text(blocks[i]["text"]))
                or (blocks[i]["type"] == "rubrik"
                    and RE_FK_END_RUBRIK.match(
                        normalize_fold(runs_text(blocks[i]["text"]))))),
               len(blocks))
    return start, end


def parse_marks(refs):
    """The designators of a combined marker: '9 och 10' -> ['9', '10'],
    '5–7' -> ['5', '6', '7'] (pure-numeric ranges expand; a lettered end like
    '18 a' keeps both endpoints as-is)."""
    parts = re.split(r"\s*(?:,|och)\s*", refs)
    out = []
    for part in parts:
        ends = re.split(r"\s*[–—‐‒-]\s*", part.strip())
        if len(ends) == 2 and all(e.isdigit() for e in ends):
            out += [str(n) for n in range(int(ends[0]), int(ends[1]) + 1)]
        else:
            out += [e for e in ends if e]
    return out


class _Entry:
    """One paragraf's (or paragraf group's) FK slice being accumulated:
    the marker's own trailing text plus the stycken up to the next boundary.
    Each text remembers its source block, so a caller can stamp the blocks
    whose text is commentary (the prop page's highlight)."""

    def __init__(self, law, chapter, nums, page, head, bare=False, block=None):
        self.law, self.chapter, self.nums, self.page = law, chapter, nums, page
        self.bare = bare                # a bare "N §" marker quotes no lagtext
        self.marker = block             # the marker block itself
        self.texts = [head] if head else []
        self.blocks = [block] if head else []
        self.komm_blocks = []           # set by record(): the commentary side

    def add(self, text, block=None):
        self.texts.append(text)
        self.blocks.append(block)

    def record(self):
        """Split the accumulated texts into quoted lagtext and commentary and
        emit the entry dict; `kommentar` is None when no commentary opener was
        found (an all-lagtext or noise entry, filtered by the caller). Under a
        bare marker there is no lagtext to split off -- everything is
        commentary, whatever formula it opens with."""
        if self.bare:
            cut = 0 if self.texts else None
        else:
            cut = next((i for i, t in enumerate(self.texts)
                        if RE_OPENER.match(t)), None)
            if cut is None:
                cut = next((i for i, t in enumerate(self.texts)
                            if RE_OPENER_WEAK.match(t) or RE_OVERVAG.search(t)),
                           None)
        if cut is not None:
            # a bare marker heading belongs with its commentary run visually
            self.komm_blocks = ([self.marker] if self.bare and self.marker
                                else []) + self.blocks[cut:]
        return {"law": self.law, "chapter": self.chapter,
                "paragrafer": self.nums, "page": self.page,
                "lagtext": "\n".join(self.texts[:cut] if cut is not None
                                     else self.texts),
                "kommentar": "\n".join(self.texts[cut:])
                             if cut is not None else None}


def extract(art, include_empty=False, mark=False):
    """Per-paragraf författningskommentar entries of a proposition artifact:
    [{law, chapter, paragrafer, page, lagtext, kommentar}]. `law` is the raw
    per-law rubrik text (resolved to an SFS uri at relate time); `paragrafer`
    is a list because a combined "9 och 10 §§" heading comments several at
    once; entries with no locatable commentary text are dropped unless
    `include_empty` (the validation/debugging view) keeps them with
    kommentar None. A law-level entry (paragrafer=[]) carries commentary that
    precedes any marker under its law -- the norm in omnibus props whose
    per-law comment never cites a paragraf. With `mark`, every block whose
    text is commentary is stamped `fk: <entry-no>` **in the artifact
    structure** (flatten shares the block dicts with the nested tree), so the
    prop page can highlight the commentary visually, one box per entry. Only a proposition's FK
    accompanies the final enactment text (`kommentar.extract` has the full
    argument), so other types yield []."""
    if art.get("type") != "prop":
        return []
    blocks = flatten(art["structure"])
    span = fk_span(blocks)
    if span is None:
        return []
    out = []
    law = chapter = None
    entry = None
    pending = []      # all-lagtext entries awaiting a trailing group comment

    def close():
        """Close the open entry. An all-lagtext entry goes to `pending`: its
        commentary may be a group comment trailing the run's last paragraf
        ("I paragraferna finns …"), in which case the kept entry annexes the
        pending designators. `flush` (at any law/chapter/rubrik boundary)
        settles what is still pending as genuinely comment-less."""
        nonlocal entry
        if entry is None:
            return
        rec = entry.record()
        komm_blocks = entry.komm_blocks
        entry = None
        if rec["kommentar"] is None:
            pending.append(rec)
            return
        if pending and RE_GROUP_KOMM.match(rec["kommentar"]):
            rec["paragrafer"] = [n for p in pending
                                 for n in p["paragrafer"]] + rec["paragrafer"]
            pending.clear()          # annexed, not comment-less
        if mark:
            # rubrik-typed blocks are flatten-created copies (stamping them
            # would not reach the artifact tree) and render as headings anyway
            for blk in komm_blocks:
                if blk is not None and blk.get("type") != "rubrik":
                    blk["fk"] = len(out) + 1     # entry no: one box per entry
        flush()
        out.append(rec)

    def flush():
        if include_empty:
            out.extend(pending)
        pending.clear()

    def open_entry(nums, page, head, bare=False, block=None):
        nonlocal entry
        close()
        entry = _Entry(law, chapter, nums, page, head, bare, block)

    for i in range(span[0] + 1, span[1]):
        b = blocks[i]
        text = runs_text(b["text"]).strip()
        fold = normalize_fold(text)
        kind = b["type"]
        # --- law boundary: numbered rubrik (or the stycke the parser demoted
        # it to), or the single-law unnumbered rubrik/stycke form (short, not
        # a prose sentence)
        if ((RE_LAW.match(fold) and (kind == "rubrik" or fold[:1].isdigit())
             and len(text) < 150)
                or (RE_LAW_BARE.match(fold) and len(text) < 150
                    and (not text.endswith(".")
                         or text.endswith((" m.m.", " m.fl.")))
                    and "§" not in text)):
            close()
            flush()
            law, chapter = text, None
            continue
        # --- combined chapter+paragraf marker ("3 kap. 18 §", "12 kap. 3 a §
        # Paragrafen har utformats …"): one block sets the chapter and opens
        # the entry. Checked before the plain-chapter branches, which would
        # otherwise swallow the chapter half and drop the paragraf.
        m = RE_KAP_PAR.match(text)
        if m:
            close()
            flush()
            chapter = re.sub(r"\s+", " ", m.group(1))
            head = text[m.end():].strip()
            if RE_VERB_HEAD.match(head):   # "… 4 § upphävs …": the marker IS
                head, bare = text, True    # part of the comment -- keep it
            else:
                bare = not head
            open_entry(parse_marks(m.group(2)), b.get("page"), head, bare=bare,
                       block=b)
            continue
        # --- chapter marker: the parser's kapitel block, or the "N kap."
        # heading it mis-tagged as a rubrik/stycke
        if kind == "kapitel":
            close()
            flush()
            chapter = b.get("num")
            continue
        m = RE_KAP.match(text)
        if (m and "§" not in text     # "8 kap. 7 § regeringsformen meddela …"
                and (kind == "rubrik" # is wrapped lagtext, not a kap heading
                     or fold.rstrip(".") == m.group(0).lower().rstrip("."))):
            close()
            flush()
            chapter = re.sub(r"\s+", " ", m.group(1))
            continue
        # --- footnote noise: never a boundary, never content
        if kind == "rubrik" and RE_FOOTNOTE.match(fold):
            continue
        # --- paragraf marker opening the block (a real paragraf block, a
        # "9 och 10 §§" rubrik, or a stycke the bold-detection missed)
        m = RE_PAR_MULTI.match(text) or RE_PAR.match(text)
        if m:
            nums = parse_marks(m.group(1))
            head = text[m.end():].strip()
            if RE_VERB_HEAD.match(head):   # "21 § ändras på det sättet …"
                head, bare = text, True
            else:
                bare = not head
            open_entry(nums, b.get("page"), head, bare=bare, block=b)
            continue
        # --- marker buried mid-stycke after a merged heading
        m = RE_EMBEDDED.search(text)
        if kind == "stycke" and m:
            if entry is not None:
                entry.add(text[:m.start()].strip(), b)
            open_entry([m.group(1)], b.get("page"), text[m.end():].strip(),
                       block=b)
            continue
        # --- any other rubrik: a real subject heading ends the current entry
        # (what follows belongs to the next paragraf, or is group prose we
        # must not mis-anchor) and settles any pending group -- a group
        # comment trails its run directly, never across a heading. But a
        # "rubrik" that reads as prose (ends in a period, or carries a §
        # reference) is a wrapped line the parser promoted, not a heading:
        # it stays with the open entry.
        if kind == "rubrik":
            if (text.endswith(".") and not text.endswith((" m.m.", " m.fl."))
                    ) or "§" in text:
                if entry is not None:
                    entry.add(text)      # a flatten-copied rubrik: no block to stamp
                continue
            close()
            flush()
            continue
        # --- body text
        if entry is not None:
            entry.add(text, b)
        elif law is not None and (m := RE_GROUP.match(text)):
            # orphaned group commentary ("Paragraferna 2–4 beskriver …"):
            # the opening formula names the anchors itself
            open_entry(parse_marks(m.group(1)), b.get("page"), text, bare=True,
                       block=b)
        elif law is not None and (RE_LAWLEVEL.match(text)
                                  or (chapter is None
                                      and RE_OPENER_WEAK.match(text))):
            # law-level commentary (omnibus props): "I 4 c § görs en
            # ändring …" / "Ändringen innebär …" directly under the law
            # rubrik, no paragraf marker in sight
            open_entry([], b.get("page"), text, bare=True, block=b)
    close()
    flush()
    return out


def resolve(con):
    """Re-derive the per-paragraf FK commentary layer in the catalog from the
    prop artifacts' `kommentarer` sections: each entry's law rubrik is resolved
    to an SFS uri (genomforande.resolve_law -- the same numbered/"Förslaget
    till"-titled resolution the genomför edges use) and each designator to the
    statute's fragment anchor; a law-level entry (no designators) lands on
    anchor '' (the document-level rail). Runs at relate time over every prop in
    the catalog -- the FK is a fixture of the genre, so there is no cheaper
    candidate filter than the artifact itself. Returns the number of rows."""
    title_idx, path_idx = genomforande.law_index(con)
    root = catalog.data_root(con)
    rows = []
    for prop_uri, path, label, date in con.execute(
            "SELECT uri, path, label, date FROM documents "
            "WHERE source = 'forarbete' AND kind = 'prop'"):
        art = json.loads(compress.read_bytes(root / path))
        entries = art.get("kommentarer")
        if not entries:
            continue
        for e in entries:
            sfs_uri = genomforande.resolve_law(e.get("law"), date,
                                               title_idx, path_idx)
            if not sfs_uri:
                continue                # a law we do not hold (or a förordning)
            for num in e.get("paragrafer") or [None]:
                anchor = kommentar.paragraf_fragment(e.get("chapter"), num)
                rows.append((sfs_uri, anchor or "", prop_uri, label, date,
                             e.get("page"), e["kommentar"]))
    catalog.set_fk_kommentar(con, rows)
    return len(rows)


def main():
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("record", help="a förarbete record JSON (or its artifact)")
    ap.add_argument("--root", default=str(layout.FA_DOWNLOADED))
    ap.add_argument("--full", action="store_true",
                    help="print each entry's full commentary text")
    args = ap.parse_args()
    data = json.loads(compress.read_bytes(args.record))
    art = data if "structure" in data else to_artifact(parse_record(data, args.root))
    entries = extract(art)
    print("%d författningskommentar entries" % len(entries))
    for e in entries:
        where = " ".join(filter(None, [
            e["chapter"] and "%s kap." % e["chapter"],
            ("%s §" % ", ".join(e["paragrafer"])) if e["paragrafer"] else "(lagnivå)"]))
        print("  %-14s [%s] %s" % (where, (e["law"] or "?")[:40],
                                   e["kommentar"][:80].replace("\n", " ")))
        if args.full:
            print("    " + e["kommentar"].replace("\n", "\n    ") + "\n")


if __name__ == "__main__":
    main()
