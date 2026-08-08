"""The per-document inbound-citation artifact: every citation *into* a document
or any provision of it, written once per build and read by the serving layer.

Every count in this module was **measured on the corpus of 2026-08-07** and is a
fact about that build, not a standing one -- the corpus grows nightly. They are
here to show the shape of the problem and the size of the trade; retest before
reasoning from one.

Why it exists is a storage-shape problem, not a caching one. The citation table
is keyed by the citing document, so answering "who cites brottsbalken and
everything in it" means gathering 162 909 rows scattered across a 2.1 GB table
-- 231 MB of random reads, ~4 minutes on prod's ~100-IOPS disk (see
`catalog.INDEX_TO_ROOT_COLUMNS` for the sibling problem and why no index closes
this one: the query needs five link columns *and* a join per citing document).
The same rows written out per target are 1.2 MB of Brotli read sequentially.

It also fixes an answer, not just a latency. A citation panel is far too big to
return whole, so any caller sees a *page* of it -- and the order decides what
that page contains. Sorted by source name, the first 100 citations of
brottsbalken were 100 ARN/JK/JO decisions and nothing else; sorted by the citing
document's own authority, 100 statutes and nothing else. Both are well-formed
answers that misrepresent the corpus. So the file is written in the order the
website's context rail already uses (`ORDER`, below), where case law leads --
one editorial decision, made once, shared by every reader.

The file carries the **complete** set. The rail's two reductions -- folding a
document's repeated citations into one line, and dropping whole-document
citations superseded by a pinpointed one -- are presentation, and stay in the
renderer: a client building its own interface needs what the site chose to hide
as much as what it showed.

**A read is whole-file.** `read` decodes the document's entire payload before
`scoped` narrows it, so a request for one paragraf of brottsbalken costs the
same as a request for all of it: ~45 MB of JSON, ~300 MB peak RSS. The `limit`
on both readers bounds the *response*, not the read. That is the storage shape
buying the latency -- the alternative is the scattered query this exists to
avoid -- and it is not a regression (the catalog query it replaced fetched all
its joined rows too, after paying for the disk). But it is a real ceiling on a
public unauthenticated endpoint, and a generate worker pays it too, so a
`jobs=8` rebuild spikes on the big statutes. If concurrency ever makes that
bite, the fix is a per-provision index in the file, not a smaller file.
"""

import hashlib
import json
import re
from pathlib import Path

from . import catalog, compress, layout
from .page import (
    CASELAW_GROUPS,
    INBOUND_GROUPS,
    RAIL_SECTION_ORDER,
    forarb_sort_key,
    inbound_group,
)

# Where the files live under data_root, beside `artifact/` rather than inside
# it: an artifact is one document's own parsed content, and this is derived from
# the whole corpus (rebuildable, and rebuilt whenever the citing side changes).
TREE = "inbound"

# q11 spends 56 s on brottsbalken's 52 MB where q9 spends 0.31 s, and buys 17%
# (1 040 KB vs 1 187 KB) -- see compress.compress_bytes. Extrapolating that one
# document across the corpus, q11 would add most of an hour of encode to a full
# rebuild to save some tens of MB of disk.
QUALITY = 9

# The rail's section order, restricted to the sections that carry inbound
# citations -- `RAIL_SECTION_ORDER` also ranks commentary, amendments,
# transposition and the rest, which are not citations and have no rows here.
# Taking the intersection rather than restating the sequence means the file
# order follows the rail automatically when the rail is re-ranked.
ORDER = tuple(key for key in RAIL_SECTION_ORDER
              if key in {slug for slug, _label in INBOUND_GROUPS})
_RANK = {key: i for i, key in enumerate(ORDER)}


def sort_key(row):
    """Where one citation sits in a document's file.

    Primary key is the rail section, so the reader's first page is the rail's
    first panel -- for a statute that is Rättsfall, which is what someone asking
    "who cites 3 kap. 1 §" came for. Within a section the rail's conventions
    carry over where they can: EU case law newest-first, preparatory works by
    `forarb_sort_key` (propositions before SOU, then oldest-first, the older
    being the more foundational), everything else by label.

    **Except Swedish case law**, the largest section, where the rail sorts
    most-cited-first (`page.inbound_panel`, an adjudicated decision -- see D4)
    and this sorts newest-first. Deliberate: a citation count is recomputed every
    build, so ordering on it would reshuffle the file whenever the corpus grew
    and break the `offset` paging promised below. Recency is the closest stable
    stand-in. Do not "align" the two without solving that.

    Total, and free of anything build-dependent, so `offset` paging is stable
    across rebuilds: ties fall through to the target provision and the citing
    spot, which together are unique per row.

    Each branch yields the same shape -- three strings -- because the section
    rank alone does not keep the branches apart (an unranked group falls in with
    the ranked ones), and comparing a tuple of strings against one starting with
    an int raises. Uniform strings also keep the comparison a real one: the
    förarbete priority is stringified deliberately at one digit, and widening
    `FORARB_KIND_PRIORITY` past 9 would need a width here.
    """
    target, from_uri, anchor, _page, _pred, label, _title, source, kind, date = row
    group, label = inbound_group(source, kind), label or ""
    if group in CASELAW_GROUPS or group == "dv":
        within = (*_descending(date), label)
    elif group == "forarbete":
        priority, when, sorted_label = forarb_sort_key(kind, date, label)
        within = ("%d" % priority, when, sorted_label)
    else:
        within = ("", "", label)
    # `target` is never null (links.to_uri is NOT NULL); `anchor` and `label` are
    return (_RANK.get(group, len(ORDER)), within, from_uri, target, anchor or "")


def _descending(date):
    """(flag, key) putting the newest date first without reversing the whole row
    order -- the rest of the sort key stays ascending.

    The flag is what makes an undated entry sort *last*, as it does in the rail:
    complementing the characters of `""` yields `""`, which sorts before every
    real date rather than after it -- so without it the handful of undated
    notisfall would head the case-law section of every statute in the corpus."""
    return (("1", "") if not date else
            ("0", "".join(chr(0x10FFFF - ord(c)) for c in date)))


# every column the serving layer hands back, read once here so a request never
# joins. `to_root` is what makes this the *tree*: the document and every
# provision in it, which a `to_uri` match would miss -- brottsbalken is cited
# 40 696 times as an act and 162 909 times counting its 2 844 cited provisions.
ROWS_SQL = (
    "SELECT l.to_uri, l.from_uri, l.from_anchor, l.from_page, l.predicate, "
    "d.label, d.title, d.source, d.kind, d.date "
    "FROM links l JOIN documents d ON d.uri = l.from_uri "
    "WHERE l.to_root = ? AND l.from_uri <> l.to_root "
    "GROUP BY l.from_uri, l.from_anchor, l.to_uri")

FIELDS = ("target", "uri", "anchor", "page", "predicate",
          "label", "title", "source", "kind", "date")


def citations(con, uri):
    """Every citation into `uri` or any provision of it, in `sort_key` order,
    as a list of dicts keyed by FIELDS.

    Self-citations are dropped (a document's links to its own provisions are its
    outbound navigation, not inbound context) and the grain is one row per
    (citing document, spot it cites from, provision cited) -- the same grain the
    catalog's own queries use, so a count taken either way agrees. Nothing else
    is filtered. The typed relations the rail routes to their own panels
    (bemyndigande, ändrar, upphäver) are citations too, and lagen.nu's own
    commentary is a document that cites the statute even though the site renders
    it as an annotation instead of a citer; a client that wants either separated
    has `predicate` and `source` to do it with.
    """
    assert "#" not in uri, ("citations() takes a document uri; a fragment's "
                            "citations are a scope of its document's file: %s" % uri)
    rows = sorted(con.execute(ROWS_SQL, (uri,)).fetchall(), key=sort_key)
    return [dict(zip(FIELDS, row, strict=True)) for row in rows]


# Longest filename this tree writes, in bytes. ext4 and NFS cap a path component
# at 255, and `util.write_atomic` writes through a same-directory ".tmpNNNNNNN"
# sibling, so the cap has to leave that room. It bites only on the *uncited* side
# of the corpus: a begrepp uri is its concept name, and the ones the citation
# extraction got wrong are whole sentences -- they never became pages (which is
# why the site never hit this), but they are cited, so they get a file.
_NAME_MAX = 200


def _shortened(name: str, uri: str) -> str:
    """`name` cut to `_NAME_MAX` bytes with a digest of the full uri appended, so
    two long names that share a prefix still land on different files. Cutting
    bytes can split a character; the partial one is dropped."""
    digest = hashlib.sha256(uri.encode()).hexdigest()[:12]
    stem = name.encode()[:_NAME_MAX - len(digest) - len("~.json")]
    return "%s~%s.json" % (stem.decode(errors="ignore"), digest)


def path(root: Path, uri: str) -> Path:
    """The inbound file for a document uri.

    Keyed by the *page* relpath (`2018:585.html` -> `2018:585.json`), so the file
    is addressable from the uri alone -- the serving layer looks up no catalog row
    to find it, and the tree browses next to the site it mirrors. Fragments key to
    their document: one file per document holds its whole tree.
    """
    # string-appended, not `with_suffix`: a page name is free to contain a dot
    # (a slugged treaty title, an EU celex), and `with_suffix` would eat the tail
    rel = Path(layout.page_relpath(uri).removesuffix(".html") + ".json")
    if len(rel.name.encode()) > _NAME_MAX:
        rel = rel.with_name(_shortened(rel.name, uri))
    return Path(root) / TREE / rel


def write(root: Path, uri: str, rows) -> bool:
    """Write one document's inbound file, returning whether anything was written.

    A document nothing cites gets *no* file, and one that has stopped being cited
    loses the file it had: 47.7% of the corpus has no inbound citations, so an
    empty file each would be 121 624 of them to store and stat. Absence is
    therefore authoritative -- but only because generate writes-or-removes on
    every page it renders, and a page whose citers changed is stale by
    construction (its dependency digest covers the link set).
    """
    target = path(root, uri)
    if not rows:
        compress.remove(target)
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    compress.write_bytes(
        target,
        json.dumps({"uri": uri, "total": len(rows), "citations": rows},
                   ensure_ascii=False).encode(),
        encodings=compress.ARTIFACT_ENCODINGS, quality=QUALITY)
    return True


# Written by the corpus-wide sweep that completes the tree, and the *only* thing
# `available` trusts. A directory would be the obvious test and is the wrong one:
# the sweep creates it before it has swept, and `generate --ignore-code-changes`
# skips every page whose data is unchanged -- so a run that wrote almost no
# per-document files still leaves a plausible-looking directory behind. The
# marker says a full generate finished, which is the thing the reader needs.
BUILT = ".built"


def mark_built(root: Path, documents: int, targets: int) -> None:
    """Record that a full generate completed this tree, over `documents`
    catalogued documents and `targets` uncatalogued citation targets."""
    (Path(root) / TREE).mkdir(parents=True, exist_ok=True)
    (Path(root) / TREE / BUILT).write_text(
        json.dumps({"documents": documents, "targets": targets}) + "\n")


def available(root: Path) -> bool:
    """Whether a full generate has written this corpus' inbound tree.

    `read` reports an absent file as "nothing cites this", which is right for
    just under half the corpus and catastrophic for a host that has the code but
    not the data -- a deploy whose artifact rsync has not landed yet would answer
    `total: 0` for every document, i.e. that nothing in Swedish law cites
    anything, with a 200. The serving layer asks this first and refuses, the way
    it already refuses a missing catalog (rule:fail-fast). A corpus that has
    genuinely never been generated is the same answer: run generate.
    """
    return (Path(root) / TREE / BUILT).exists()


def read(root: Path, uri: str):
    """Every citation into `uri`'s document and its provisions, in `sort_key`
    order -- `[]` where nothing cites it (see `write` on why absence is
    authoritative). A corrupt file raises rather than reporting "no citations",
    which a caller cannot tell from the ordinary empty case (rule:fail-fast).

    The stored uri is checked, not assumed: `page_relpath` is not injective (two
    begrepp slugs can land on one name), and generate drops the loser's page
    rather than letting it clobber the winner's. Its citations are dropped here
    for the same reason -- returning the winner's under the loser's uri would be
    a wrong answer where no answer is the honest one.
    """
    p = path(root, uri)
    if not compress.exists(p):
        return []
    payload = compress.read_json(p)
    return (payload["citations"]
            if payload["uri"] == catalog.strip_fragment(uri) else [])


# What may follow a fragment for the longer one to be a provision *inside* it:
# an uppercase letter opens a new segment (K3P1 -> K3P1S2 stycke, K3P1M2 moment,
# K3P1S1N2 punkt; the treaty grammar's A5P1 -> A5P1La litera), and a dot is the
# EU separator (9.2 -> 9.2.S2 stycke, 9.2.a point). Nothing else does, and the
# two exclusions are the point of the rule:
#
#   digit -- continues the number, so K3P1 must not swallow K3P10, nor prop's
#            sid5 the whole of sid50
#   lowercase -- a Swedish *inserted* provision, which is a sibling and not a
#            child: "18 a §" (K1P18a) is its own paragraf beside 18 §, and a
#            naive prefix test put its 143 citations under 18 §'s
_SUBTREE_OF = re.compile(r"[A-Z.]")


def scoped(rows, uri: str):
    """`rows` narrowed to the citations landing on `uri` itself or inside it.

    A document uri takes the whole file -- that is what the file is. A fragment
    takes itself plus its own subtree: 467 164 links target a stycke, so "who
    cites 3 kap. 1 §" has to reach `#K3P1S2` and not only `#K3P1`.
    """
    if "#" not in uri:
        return list(rows)
    return [row for row in rows
            if row["target"] == uri
            or (row["target"].startswith(uri)
                and _SUBTREE_OF.match(row["target"][len(uri):]))]


def exact(rows, uri: str):
    """`rows` narrowed to the citations naming `uri` and nothing else -- what the
    old `catalog.inbound` answered. On a bare document uri that means the
    citations of the act *as such*, excluding every pinpoint into it."""
    return [row for row in rows if row["target"] == uri]


def by_source(rows) -> dict[str, int]:
    """{source: rows}, most first -- what an answer too big to return is *made
    of*. Both readers hand it the whole scope rather than the page or the
    filtered subset, so a caller that took 50 of 162 909 can still see what the
    rest holds and narrow towards it instead of paging blindly."""
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["source"]] = counts.get(row["source"], 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]))
