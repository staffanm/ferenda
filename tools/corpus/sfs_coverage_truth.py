"""Prove `sfs.coverage`'s splice against consolidations the archive already
holds: for every pair of consecutive chain links where both consolidations
are on disk, the later one's PDF is mirrored and its Omfattning is a plain
replacement, reconstruct the later one from the earlier one and compare.

    uv run tools/corpus/sfs_coverage_truth.py OUT.json [SAMPLE]

Writes one row per attempted pair to OUT.json -- `(basefile, base, base
path, target, target path, omfattning, status, detail)`, `status` one of
"match", "mismatch" (`detail` the first differing stycken, difflib style),
"not_simple" (the triage reason) or "unreadable" -- and prints the counts.
SAMPLE (default 600) draws that many pairs at random, same seed every run.

The comparison is stycke by stycke with whitespace collapsed, the "Lag
(YYYY:NNN)." marker split off, dashes folded (the legacy text carries the
cp1252 en dash as "\\x96"), and pending-variant tags folded: a version
captured before an amendment's effective date keeps both wordings, one
captured after -- or reconstructed now -- keeps the new one, and neither is
wrong. What remains as a mismatch is either the splice's own error or a
difference the government's later text carries on its own (a corrected
typo, a placeholder SFS number filled in); read the detail before acting.
Measured 2026-09-04 over 600 of 17,211 pairs (all change kinds the module
writes): 394 match, 125 mismatch (legacy-text quirks: a line-break hyphen
the legacy text kept, an en dash it dropped, a typo the government fixed
later, a transitional section one legacy generation lacks), 81 refused by
triage."""

import collections
import difflib
import json
import random
import re
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from ferenda.lib import compress, layout
from ferenda.lib.errors import SkipDocument
from ferenda.sfs import coverage
from ferenda.sfs.source import sfs_list

_RE_MARK = re.compile(r"\s*((?:Lag|Förordning) \(\d{4}:\d+\)\.)\s*$")
_RE_PENDING_OLD = re.compile(
    r"(?:^|\n\n)(\d+ ?[a-z]? \xa7) /Upph\xf6r att g\xe4lla U:[^/]*/.*?"
    r"(?=\n\n\1 /Tr\xe4der i kraft)", re.DOTALL)
_RE_PENDING_NEW = re.compile(r" /Tr\xe4der i kraft I:[^/]*/")


def normalized(text):
    """The text as a list of stycken, whitespace collapsed, markers split
    off, dashes folded, and every pending variant settled (a version
    captured before an amendment's effective date keeps both wordings, one
    captured after -- or reconstructed now -- keeps the new one)."""
    try:
        text = coverage.resolve_pending(text, "9999-12-31")
    except coverage.NotSimple:
        pass   # a renumbering the module does not settle: compared as it stands
    text = text.replace("\r\n", "\n")
    text = _RE_PENDING_OLD.sub("", text)
    text = _RE_PENDING_NEW.sub("", text)
    text = text.replace("–", "-").replace("\x96", "-")
    out = []
    for stycke in re.split(r"\n{2,}", text):
        stycke = re.sub(r"\s+", " ", stycke).strip()
        if not stycke:
            continue
        m = _RE_MARK.search(stycke)
        if m:
            if stycke[:m.start()].strip():
                out.append(stycke[:m.start()].strip())
            out.append(m.group(1))
        else:
            out.append(stycke)
    return out


def candidates(basefile):
    """Every `(basefile, base, base path, target, target path, omfattning)`
    pair the archive can prove for `basefile`: two consecutive covered
    links with the later one's PDF mirrored."""
    links = coverage.covered_links(basefile)
    omfattning = {e["beteckning"]: e.get("anteckningar") or ""
                  for e in coverage._chain(compress.read_json(layout.sfs_source(basefile)))}
    return [(basefile, a, str(a_path), b, str(b_path), omfattning[b])
            for (a, a_path), (b, b_path) in zip(links, links[1:], strict=False)
            if a_path is not None and b_path is not None
            and compress.exists(layout.sfs_pdf(b))
            and coverage.is_simple_omfattning(omfattning[b])]


def check(candidate):
    basefile, _base, base_path, target, target_path, omfattning = candidate
    try:
        base = coverage.read_base(Path(base_path), basefile)
        truth = coverage.read_base(Path(target_path), basefile)
    except SkipDocument as exc:
        return candidate + ("unreadable", str(exc)[:200])
    try:
        new = coverage.apply_amendment(base, layout.sfs_pdf(target), target,
                                       coverage.parse_omfattning(omfattning or ""))
    except coverage.NotSimple as exc:
        return candidate + ("not_simple", str(exc)[:200])
    got = normalized(new["fulltext"]["forfattningstext"])
    want = normalized(truth["fulltext"]["forfattningstext"])
    if got == want:
        return candidate + ("match", "")
    diff = [line for line in difflib.ndiff(want, got) if line[:1] in "+-"]
    return candidate + ("mismatch", "\n".join(line[:300] for line in diff[:12]))


def main(out, sample):
    with ProcessPoolExecutor() as pool:
        pairs = [c for cs in pool.map(candidates, list(sfs_list()), chunksize=50) for c in cs]
        print("pairs the archive can prove:", len(pairs))
        random.seed(7)
        rows = list(pool.map(check, random.sample(pairs, min(sample, len(pairs))), chunksize=4))
    Path(out).write_text(json.dumps(rows, ensure_ascii=False, indent=0))
    print(collections.Counter(row[6] for row in rows))


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 600)
