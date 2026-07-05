"""Parse a legacy court decision (Word .doc/.docx) into the Avgorande model.

Consumes the flat (text, bold) paragraph stream from word.read() and
splits it into header / metadata / body / footer the way the documents are
laid out: a bold court name and referat at the top, bold "Label:" / value
pairs in a metadata table, a bold "REFERAT" marker, the body, and bold
"Sökord:" / "Litteratur:" in the footer.

Identity (canonical referat, court) is supplied by the identity index;
everything the index lacks for legacy-only cases -- avgörandedatum,
målnummer, and the curated fields (Rubrik→sammanfattning, Lagrum,
Rättsfall, Sökord) -- is recovered here from the document itself.

  python -m accommodanda.dv_legacy FILE                 # one Word file -> artifact
  python -m accommodanda.dv_legacy --index INDEX [--limit N]  # batch + report
"""

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

from ..lib import layout, util
from . import word
from .model import Avgorande, Lagrum, Rubrik, Stycke
from .parse import RE_NUMPARA, is_heading, to_artifact

# Footer labels that end the body region.
_FOOTER_LABELS = {"Sökord", "Litteratur"}


def parse_head_body(paras):
    """list[Para] -> (head dict, list[Para] body). Values are lists of strings."""
    head = {}
    body = []
    section = "header"
    label = None
    for p in paras:
        text = p.text
        if section == "header":
            if not text:
                continue
            if "|" in text and "Domstol" not in head:
                parts = [x.strip() for x in text.split("|") if x.strip()]
                head["Domstol"], head["Referat"] = parts[0], parts[1]
                section = "meta"
            elif "Domstol" not in head:
                head["Domstol"] = text
            else:
                head["Referat"] = text
                section = "meta"
            continue
        if text == "REFERAT":
            section = "body"
            label = None
            continue
        if section in ("meta", "footer"):
            if not text:
                continue
            if p.bold and text.endswith(":"):
                label = text[:-1].strip()
                head.setdefault(label, [])
            elif label is not None:
                head[label].append(text)
            continue
        if section == "body":
            stripped = text.rstrip(":")
            if p.bold and stripped in _FOOTER_LABELS and text.endswith(":"):
                section = "footer"
                label = stripped
                head.setdefault(label, [])
            elif text:
                body.append(p)
    return head, body


def _classify(p):
    """A body Para -> Rubrik or Stycke."""
    m = RE_NUMPARA.match(p.text)
    if m:
        return Stycke(text=m.group(2).strip(), ordinal=m.group(1))
    if (p.bold and len(p.text) <= 80) or is_heading(p.text):
        return Rubrik(text=p.text)
    return Stycke(text=p.text)


# "Ö 2475-12" is one målnummer, not ["Ö", "2475-12"]; other courts list
# several målnummer separated by comma/semicolon/space/"och".
_MALNR_SPLIT = re.compile(r"\s+och\s+|[,;]|\s+")


def _split_malnummer(value):
    if value[:2] in ("Ö ", "B ", "T "):
        return [value.replace(" ", "")]
    return [x.strip() for x in _MALNR_SPLIT.split(value) if x.strip()]


def _first(head, key):
    vals = head.get(key)
    return vals[0] if vals else None


def parse_legacy_file(path, case=None):
    """A legacy Word file -> Avgorande. `case` is the identity-index entry,
    used for canonical referat/court/málnummer when present."""
    head, body = parse_head_body(word.read(path))
    return build_avgorande(head, body, case, sources=[str(path)])


def build_avgorande(head, body, case=None, sources=None):
    """Map an extracted (head, body) onto Avgorande, preferring the identity
    index's canonical identity fields over the document's where present."""
    case = case or {}

    referat = case.get("referat") or ([head["Referat"]] if head.get("Referat") else [])
    malnummer = case.get("malnummer")
    if not malnummer:
        malnummer = _split_malnummer(_first(head, "Målnummer")) if head.get("Målnummer") else []
    sokord = head.get("Sökord") or []
    if len(sokord) == 1:
        sokord = [s.strip() for s in re.split(r"[;,]", sokord[0]) if s.strip()]

    return Avgorande(
        court=(case.get("courts") or [None])[0] or head.get("Domstol"),
        court_namn=head.get("Domstol"),
        malnummer=malnummer,
        referat=referat,
        avgorandedatum=case.get("avgorandedatum") or _first(head, "Avgörandedatum"),
        publiceringsform=None,
        typ=None,
        rattsomrade=[],
        nyckelord=sokord,
        lagrum=[Lagrum(referens=l) for l in head.get("Lagrum", [])],
        forarbeten=[],
        sammanfattning=" ".join(head.get("Rubrik", [])) or None,
        related=head.get("Rättsfall", []),
        body=[_classify(p) for p in body],
        sources=sources or [],
    )


def legacy_original(case):
    """The first legacy member with a non-empty original file, or None.
    Notisfall members have a zero-byte original (the body lives only in the
    frozen intermediate) and are excluded here."""
    for member in case["members"]:
        if member["store"] != "dv":
            continue
        path = util.load_relpath(layout.DATA, member["path"])
        if path.exists() and path.stat().st_size:
            return member
    return None


def cmd_index(args):
    cases = json.loads(Path(args.index).read_text())
    cases = [(c, legacy_original(c)) for c in cases]
    cases = [(c, m) for c, m in cases if m]
    if args.limit:
        cases = cases[:args.limit]
    counts = Counter()
    blockstats = Counter()
    failures = []
    for case, member in cases:
        try:
            av = parse_legacy_file(util.load_relpath(layout.DATA, member["path"]), case)
        except Exception as e:  # noqa: BLE001 — stats harness: failure tallied, corpus scan continues (rule:no-catch-log-continue)
            failures.append((case["canonical_id"], "%s: %s" % (type(e).__name__, e)))
            continue
        counts["parsed"] += 1
        blockstats["rubrik"] += sum(isinstance(b, Rubrik) for b in av.body)
        blockstats["stycke"] += sum(isinstance(b, Stycke) for b in av.body)
        if not av.body:
            counts["empty_body"] += 1
    print("%d legacy cases with an original: %d parsed, %d empty body, %d failed"
          % (len(cases), counts["parsed"], counts["empty_body"], len(failures)))
    print("blocks: %d rubrik, %d stycke" % (blockstats["rubrik"], blockstats["stycke"]))
    for cid, err in failures[:20]:
        print("  FAIL %s: %s" % (cid, err))


def cmd_file(args):
    av = parse_legacy_file(args.file)
    cid = av.referat[0] if av.referat else None
    json.dump(to_artifact(av, cid), sys.stdout, ensure_ascii=False, indent=2)
    print()


def main():
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument("file", nargs="?")
    parser.add_argument("--index")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    if args.index:
        cmd_index(args)
    elif args.file:
        cmd_file(args)
    else:
        parser.error("need FILE or --index")


if __name__ == "__main__":
    main()
