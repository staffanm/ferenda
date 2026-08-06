#!/usr/bin/env python3
"""Date the named-law dataset from the corpus: when did each name mean which act?

`namedlaws.json` maps a spelled name ("socialtjänstlagen") and an acronym ("SoL")
to one SFS id. A name outlives the act holding it, so every citation written
before the current act took over resolves to a statute that did not exist yet --
5 rättsfall and 100+ myndighetsbeslut on 11 kap. 1 § socialtjänstlagen alone,
all predating the 2025 act they were filed under.

The corpus already knows the succession. Each SFS artifact carries
`rpubl:upphavandedatum` (when it stopped applying) and `rinfoex:upphavdAv` (the
act that replaced it), so following the repeal links backwards from a named act
yields its predecessors, and their repeal dates are the boundaries between them.

A predecessor inherits the name only if its **own title** yields that name. The
chain alone is not enough: begravningslagen (1990:1144) replaced "Lag (1963:537)
om gravrätt m.m.", which nobody ever cited as begravningslagen, and dating it as
such would send every pre-1991 "begravningslagen" to the wrong act. Measured
over the 245 curated entries: 93 have a repealed predecessor and 53 of those
carry the same name. That is the filter's selectivity, not a coverage figure --
what the repeal links happen to reach is a separate question (see
`promulgated_by` for one way they used not to).

Writes `from`/`until` into the dataset in place (`--write`), or prints the diff.
Re-run after a repeal; it is idempotent, and it never edits an entry it has no
corpus evidence for.
"""
import argparse
import json
import re
import sys
from collections import defaultdict

from accommodanda.lib import compress, datasets, layout


def sfs_index():
    """Every SFS act: id -> (title, upphavandedatum, the act that repealed it).

    Read off the base artifacts only -- a `konsolidering` is one act's text at a
    version, not a separate act, and would double-count the succession."""
    out = {}
    for path in (layout.ARTIFACT / "sfs").rglob("*.json*"):
        if "konsolidering" in str(path) or "/archive/" in str(path):
            continue
        try:
            doc = json.loads(compress.read_bytes(path))
        except (OSError, ValueError):
            continue          # mid-rebuild rewrite; the next run picks it up
        props = doc.get("metadata", {}).get("properties", {})
        uri = doc.get("uri") or ""
        out[uri.rsplit("/", 1)[-1] if uri else path.stem] = (
            props.get("dcterms:title"),
            props.get("rpubl:upphavandedatum"),
            (props.get("rinfoex:upphavdAv") or "").rsplit("/", 1)[-1] or None)
    return out


RE_INFORANDE = re.compile(r"om inf(?:ö|o)randet? av .*?\((\d{4}:[\w.: ]+?)\)", re.I)


def promulgated_by(index):
    """införandelag id -> the act it brings into force.

    `rinfoex:upphavdAv` names the act that *repealed* one, and for a major
    statute that is its införandelag rather than the successor itself: skollagen
    (1985:1100) was repealed by 2010:801, "Lag om införande av skollagen
    (2010:800)". Following the link literally stops the chain there, because an
    införandelag's title never yields the name -- so every pre-2011 "skollagen",
    and every pre-2006 "aktiebolagslagen", stayed pointed at today's act. The
    title states which act it introduces, SFS number and all."""
    out = {}
    for sfsid, (title, _upph, _by) in index.items():
        if m := RE_INFORANDE.search(title or ""):
            out[sfsid] = m.group(1).strip()
    return out


def cited_as(title):
    """The definite forms an act's own title could be cited by. "Socialtjänstlag
    (2001:453)" -> socialtjänstlagen; "Lag (1963:537) om gravrätt m.m." has no
    short name and yields only the useless "lag". The definite "-en" rather than
    real morphology: the caller only asks whether a form *equals* a name already
    in the dataset, so a wrong one matches nothing. Measured over the current
    dataset, all 85 successful matches came through "-en" and none through the
    bare head -- the head is kept because a name that *is* a title head costs
    nothing to admit, and a "-n" stem (as "stadga" would take) is in no dataset
    name at all, so that form was dropped."""
    if not title:
        return frozenset()
    head = title.split(" (")[0].strip().lower()
    return frozenset({head, head + "en"})


def predecessors(lawid, name, index, repealed_by):
    """The acts that carried `name` before `lawid`, newest first, each with the
    date it stopped: [(sfsid, upphavandedatum), ...]."""
    chain, seen, cur = [], set(), lawid
    while cur not in seen:
        seen.add(cur)
        step = [p for p in repealed_by.get(cur, ())
                if name in cited_as(index.get(p, (None,))[0])]
        if not step:
            break
        # One name, one line of succession. Several acts repealed by the same
        # one are a merge (three became begravningslagen), but only one of them
        # can have carried the name -- if two did, the boundaries below would be
        # fabricated and which act won would depend on directory order.
        assert len(step) == 1, (
            "%r was carried by %s at once -- the succession is not a line"
            % (name, ", ".join(step)))
        chain.append((step[0], index[step[0]][1]))
        cur = step[0]
    return chain


def successors(lawid, name, index, repealed_by):
    """The acts that carried `name` *after* `lawid`, oldest first, each with the
    date it took over: [(sfsid, from), ...].

    The mirror of `predecessors`, on the same evidence and for the same reason.
    `namedlaws.json` is hand-edited and names the act that was current when the
    entry was written, so an act replaced since is left looking current --
    "polisdatalagen" still pointed at 1998:622 although 2010:361 took the name in
    2012. Only a successor whose own title carries the name counts: where the
    replacement renamed the concept there is nothing to move, because no act
    holds the name today and a citation to it can only mean the repealed one
    (skuldsaneringslagen, firmalagen, giftermålsbalken)."""
    replaces = {old: new for new, olds in repealed_by.items() for old in olds}
    chain, seen, cur = [], set(), lawid
    while cur not in seen:
        seen.add(cur)
        nxt, ended = replaces.get(cur), index.get(cur, (None, None))[1]
        if not (nxt and ended and name in cited_as(index.get(nxt, (None,))[0])):
            break
        chain.append((nxt, ended))
        cur = nxt
    return chain


def dated_entries(data, index):
    """`namedlaws.json` with `from`/`until` filled in, plus the rows the
    predecessors need. Every act that ever carried a name gets its own entry, so
    the file stays keyed by SFS id and a reader needs no succession logic."""
    introduces = promulgated_by(index)
    repealed_by = defaultdict(list)
    for sfsid, (_title, _upph, by) in index.items():
        if by:
            # an act repealed by an införandelag was replaced by the act that
            # införandelag brings into force
            repealed_by[introduces.get(by, by)].append(sfsid)

    out = {k: dict(v) for k, v in data.items()}
    for lawid, entry in data.items():
        label = entry.get("label")
        # The succession is traced by the spelled name, because that is what an
        # act's own title states. The acronym is then carried along it: "SoL"
        # meant the 2001 act in 2010 for exactly the reason "socialtjänstlagen"
        # did, and an entry that has only an acronym has nothing to trace.
        for name in ([label] if isinstance(label, str) else label or []):
            # the dataset and the index agree on the underscored form (the
            # artifact URI keeps it: "1845:50_s.1"); the spaced form the parser
            # emits is not a key here, and looking one up made the walk dead for
            # that whole class of act
            chain = predecessors(lawid, name, index, repealed_by)
            if not chain:
                continue
            # boundaries: each act ran until it was repealed and started where
            # its own predecessor stopped; the named (current) act starts when
            # the most recent predecessor was repealed
            if any(ended is None for _sfsid, ended in chain):
                undated = [c for c, e in chain if e is None]
                print("WARN %-14s predecessor %s carries a repeal link but no "
                      "rpubl:upphavandedatum -- skipping the chain, since a row "
                      "with no `until` would be a second act still carrying %r "
                      "and `load_namedlaws` refuses that at every parse"
                      % (lawid, ", ".join(undated), name), file=sys.stderr)
                continue
            for i, (sfsid, ended) in enumerate(chain):
                row = out.setdefault(sfsid, {})
                row.setdefault("label", name)
                if entry.get("abbr"):
                    row.setdefault("abbr", entry["abbr"])
                if ended:
                    row["until"] = ended
                earlier = chain[i + 1][1] if i + 1 < len(chain) else None
                if earlier:
                    row["from"] = earlier
            if chain[0][1]:
                out[lawid]["from"] = chain[0][1]

    # forward from every named act, on the same evidence
    for lawid, entry in data.items():
        label = entry.get("label")
        for name in ([label] if isinstance(label, str) else label or []):
            held_by = lawid
            for sfsid, took_over in successors(lawid, name, index, repealed_by):
                # the act that held the name until now stops there, and the one
                # taking over starts -- and is open-ended until a later step (or
                # a later run, once it too is replaced) closes it
                out.setdefault(held_by, {})["until"] = took_over
                row = out.setdefault(sfsid, {})
                row.setdefault("label", name)
                if entry.get("abbr"):
                    row.setdefault("abbr", entry["abbr"])
                row["from"] = took_over
                row.pop("until", None)
                held_by = sfsid
    return {k: out[k] for k in sorted(out, key=_sfs_sort)}


def superseded(dated, index, repealed_by):
    """Entries left claiming a name whose successor carries that same name --
    which `successors` should have moved, so anything here is a bug in this tool
    rather than work for a curator.

    A repealed act that still holds its name is *not* an error and is not
    reported: where the replacement renamed the concept, no act carries the old
    name today, so a citation to "skuldsaneringslagen" or "firmalagen" can only
    mean the repealed act and pointing at it is the correct answer. Only a
    same-name succession is ambiguous, and only that is something to fix."""
    return {lawid: index[lawid][1] for lawid, entry in dated.items()
            if "until" not in entry and entry.get("label")
            and index.get(lawid) and index[lawid][1]
            and successors(lawid, entry["label"], index, repealed_by)}


def _sfs_sort(lawid):
    """File order: by year, then by number, so a new row lands beside its
    neighbours rather than at the end (the file is hand-edited)."""
    year, _, rest = lawid.partition(":")
    num = "".join(c for c in rest if c.isdigit())
    return (int(year) if year.isdigit() else 0, int(num) if num else 0, lawid)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write", action="store_true",
                    help="edit namedlaws.json in place (default: show the diff)")
    args = ap.parse_args()

    data = json.loads(datasets.NAMEDLAWS.read_text(encoding="utf-8"))
    index = sfs_index()
    print("sfs acts read: %d (%d carry a repeal link)"
          % (len(index), sum(1 for v in index.values() if v[2])), file=sys.stderr)

    dated = dated_entries(data, index)
    changed = {k: v for k, v in dated.items() if data.get(k) != v}
    for k in sorted(changed, key=_sfs_sort):
        print("%-14s %-5s %s" % (k, "" if k in data else "NEW",
                                 json.dumps(changed[k], ensure_ascii=False)))
    introduces = promulgated_by(index)
    repealed_by = defaultdict(list)
    for sfsid, (_t, _u, by) in index.items():
        if by:
            repealed_by[introduces.get(by, by)].append(sfsid)
    for lawid, upph in sorted(superseded(dated, index, repealed_by).items(),
                              key=lambda kv: _sfs_sort(kv[0])):
        print("WARN %-14s %r still holds the name though %s took it over on %s"
              % (lawid, dated[lawid].get("label"), "its successor", upph),
              file=sys.stderr)
    print("%d entries dated, %d added, %d total"
          % (sum(1 for k in changed if k in data),
             sum(1 for k in changed if k not in data), len(dated)), file=sys.stderr)

    if args.write:
        body = ",\n".join("  %s: %s" % (json.dumps(k, ensure_ascii=False),
                                        json.dumps(v, ensure_ascii=False))
                          for k, v in dated.items())
        datasets.NAMEDLAWS.write_text("{\n%s\n}\n" % body, encoding="utf-8")
        print("wrote %s" % datasets.NAMEDLAWS, file=sys.stderr)


if __name__ == "__main__":
    main()
