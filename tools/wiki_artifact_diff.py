#!/usr/bin/env python3
"""Artifact-equality check for the wiki markdown migration (PRD §4.1, the
linchpin safety property): for every ns0 content page, the artifact produced by
the **new** markdown path (`convert_page` -> markdown file -> `wiki.parse`) must
be byte-identical to the **old** wikitext path (`lib.wikitext`), except for the
additive `aliases` field on begrepp (from redirects, which the old path dropped).

Sources the live DB's latest revision per page (the content being migrated), so
it proves the conversion is lossless on the real corpus, not a fixture.

Usage:  tools/wiki_artifact_diff.py mediawiki-db/db/lagen.sqlite [--show N]
Exit 0 if all pages match (modulo aliases); 1 otherwise.
"""

import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mediawiki_to_markdown import (  # noqa: E402
    _connect,
    _u,
    convert_page,
    is_content,
    redirect_aliases,
    render_file,
)

from accommodanda.lib import wikitext  # noqa: E402
from accommodanda.wiki import parse as wiki  # noqa: E402

# -- the legacy wikitext artifact builders (ported verbatim from the pre-markdown
#    wiki/parse.py; kept here only as the equality reference) -------------------

def old_begrepp(title, wt):
    parser = wiki._parser("begrepp")
    body = []
    for block in wikitext.blocks(wt):
        if block[0] == "rubrik":
            body.append({"type": "rubrik", "level": block[1], "text": [block[2]]})
        else:
            body.append({"type": "stycke",
                         "text": wikitext.to_runs(block[1], parser, context={})})
    return {"uri": wikitext.begrepp_uri(title), "type": "begrepp",
            "title": title, "categories": wikitext.categories(wt), "body": body}


def old_kommentar(title, wt):
    sfsnr = title.split("/", 1)[1]
    law_uri = "https://lagen.nu/" + sfsnr
    parser = wiki._parser(sfsnr)
    body, section, frag = [], None, None
    for block in wikitext.blocks(wt):
        if block[0] == "rubrik":
            _, level, heading = block
            f = wiki.heading_fragment(heading)
            if f:
                frag, section = f, []
                body.append({"type": "sektion", "id": frag, "heading": heading,
                             "text": [{"predicate": "dcterms:references",
                                       "uri": "%s#%s" % (law_uri, frag),
                                       "text": heading}],
                             "children": section})
            else:
                (section if section is not None else body).append(
                    {"type": "rubrik", "level": level, "text": [heading]})
        else:
            runs = wikitext.to_runs(block[1], parser, fragment=frag)
            (section if section is not None else body).append(
                {"type": "stycke", "text": runs})
    return {"uri": "https://lagen.nu/kommentar/" + sfsnr, "type": "kommentar",
            "basefile": sfsnr, "annotates": law_uri, "author": wikitext.author(wt),
            "categories": wikitext.categories(wt), "body": body}


def new_artifact(title, wt, aliases, tmp):
    """Run the real production path: convert -> markdown file -> wiki.parse."""
    meta, body = convert_page(title, wt)
    if "title" in meta and meta["title"] in aliases:
        meta["aliases"] = aliases[meta["title"]]
    path = tmp / "page.md"
    path.write_text(render_file(meta, body), encoding="utf-8")
    return (wiki.kommentar_artifact if title.startswith("SFS/")
            else wiki.begrepp_artifact)(str(path))


def _norm(art):
    """Normalise the two adjudicated, content-free differences between the old
    wikitext path and the new markdown path (PRD §4.1 enumerates these as
    intended):
      1. a leading/trailing space on a paragraph's edge text run -- left by
         wikitext template stripping (`{{DISPLAYTITLE:…}} [link]`); markdown
         paragraphs are edge-trimmed by the format itself;
      2. a `stycke` that reduces to no runs (`text == []`, a template-only line):
         markdown drops the empty paragraph, the old path kept it.
    Neither carries content the site renders."""
    def trim(runs):
        runs = list(runs)
        if runs and isinstance(runs[0], str):
            runs[0] = runs[0].lstrip()
        if runs and isinstance(runs[-1], str):
            runs[-1] = runs[-1].rstrip()
        return [r for r in runs if r != ""]

    def node(n):
        if n.get("type") == "stycke":
            n = {**n, "text": trim(n["text"])}
        if "children" in n:
            n = {**n, "children": [c for c in (node(c) for c in n["children"])
                                   if not (c.get("type") == "stycke" and not c["text"])]}
        return n

    body = [n for n in (node(n) for n in art["body"])
            if not (n.get("type") == "stycke" and not n["text"])]
    return {**art, "body": body}


def latest_pages(con):
    """(title, wikitext) for every ns0 non-redirect content page, latest rev."""
    rows = con.execute(
        "SELECT p.page_title, t.old_text FROM page p "
        "JOIN slots s ON s.slot_revision_id = p.page_latest "
        "JOIN content c ON c.content_id = s.slot_content_id "
        "JOIN text t ON ('tt:' || t.old_id) = c.content_address "
        "WHERE p.page_namespace = 0 AND p.page_is_redirect = 0")
    return [(_u(title), _u(text)) for title, text in rows]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("--show", type=int, default=5, help="how many diffs to print")
    args = ap.parse_args()
    con = _connect(args.db)
    aliases = redirect_aliases(con)

    matched = mismatched = skipped = aliased = 0
    diffs = []
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        for title, wt in latest_pages(con):
            if title.startswith("SFS/"):
                old = old_kommentar(title, wt)
            elif not is_content(title):
                skipped += 1
                continue
            else:
                # the legacy pipeline read the XML <title> (spaces), not the DB
                # page_title (underscores); the markdown path matches the former
                old = old_begrepp(title.replace("_", " "), wt)
            new = new_artifact(title, wt, aliases, tmp)
            if new.pop("aliases", None):
                aliased += 1
            old, new = _norm(old), _norm(new)
            if old == new:
                matched += 1
            else:
                mismatched += 1
                if len(diffs) < args.show:
                    diffs.append((title, old, new))

    for title, old, new in diffs:
        print("\n=== MISMATCH: %s ===" % title)
        o, n = json.dumps(old, ensure_ascii=False), json.dumps(new, ensure_ascii=False)
        for i, (a, b) in enumerate(zip(o, n)):
            if a != b:
                print("  first diff at char %d:" % i)
                print("   old: …%s" % o[max(0, i - 40):i + 40])
                print("   new: …%s" % n[max(0, i - 40):i + 40])
                break
        else:
            print("  (lengths differ: old=%d new=%d)" % (len(o), len(n)))
    print("\nmatched=%d  mismatched=%d  skipped(non-content)=%d  with-aliases=%d"
          % (matched, mismatched, skipped, aliased))
    sys.exit(1 if mismatched else 0)


if __name__ == "__main__":
    main()
