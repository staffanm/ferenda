"""Per-artifact row building: everything relate derives from one artifact on
its own, with no catalog connection in sight.

Two blocks, both pure functions of an artifact dict:

  * **edge extraction** -- the generic walk that pulls every inline citation
    out of any source's node tree, plus the typed edges a source states as
    metadata (a förarbete's genomför-direktiv statements, a dom's nyckelord).
  * **document rows** -- the `documents` row a source's artifact becomes: its
    kind, label, title, date, expiry, publisher and snippet.

`catalog` imports this module for `_index_document` and `rebuild`; nothing here
imports back, so the pair stays acyclic.
"""

import re
from functools import partial

from . import labels, text
from .markdown import begrepp_uri
from .pinpoint import pinpoint_label

# this module sits *below* catalog (catalog imports it to build its rows), so it
# takes the local-id strip from util rather than importing catalog back.
from .util import local as _local

# --------------------------------------------------------------------------
# edge extraction -- one generic walk over any artifact node tree
# --------------------------------------------------------------------------

def collect_links(node, anchor, page, out):
    """Walk an artifact node tree, appending (anchor, page, run) for every
    inline link, attributed to the nearest enclosing node `id` and the printed
    page it sits on (sources parsed from a PDF tag their blocks with one; the
    rest carry None throughout). Handles the two leaf carriers of runs: a node's
    `text` list and a table `rad`'s `cells` (a list of cells, each itself a runs
    list)."""
    if isinstance(node, dict):
        anchor = node.get("id") or anchor
        page = node.get("page") or page
        for key, value in node.items():
            if key == "text" and isinstance(value, list):
                out += [(anchor, page, run) for run in value
                        if isinstance(run, dict) and "uri" in run]
            elif key == "cells":
                for cell in value:
                    out += [(anchor, page, run) for run in cell
                            if isinstance(run, dict) and "uri" in run]
            else:
                collect_links(value, anchor, page, out)
    elif isinstance(node, list):
        for item in node:
            collect_links(item, anchor, page, out)


def implements_links(art):
    """The genomför-direktiv edges a förarbete artifact carries (extracted from
    its författningskommentar into the `implements` section): each statement ->
    one edge per EU directive article it transposes, anchored to the page the
    statement sits on (the förarbete's `#sid{N}`, so inbound pinpoints the page).
    The stronger *implements* relation, kept as a typed section because the
    parser cannot splice it back into the flat PDF text as an inline link."""
    out = []
    for rec in art.get("implements", []):
        anchor = "sid%d" % rec["page"] if rec.get("page") else None
        for uri in rec.get("uris", []):
            out.append((anchor, rec.get("page"),
                        {"uri": uri, "predicate": rec["predicate"],
                         "text": rec.get("sentence")}))
    return out


def artifact_links(art):
    """Every inline citation in an artifact, from the body-bearing sections
    of either source: SFS `structure` + the amendments' `content`, DV `body`,
    a föreskrift's presented consolidation (which replaces its base
    `structure` -- text.body_sections owns that choice, so the graph carries
    exactly the citations the rendered page shows), plus a förarbete's
    `implements` (genomför-direktiv) edges and generic top-level `references`
    for relations expressed by source metadata rather than a literal body
    span (HUDOC's article facet, treaty crosswalks). Entries are
    (anchor, page, run) -- the body walk is the only producer that knows a
    printed page, so the metadata edges below carry None."""
    out = []
    for nodes in text.body_sections(art):
        collect_links(nodes, None, None, out)
    for amendment in art.get("amendments", []):
        collect_links(amendment.get("content"), None, None, out)
    out += implements_links(art)
    # Source metadata can carry legal relations that have no literal span in
    # the body (HUDOC's article facet, a treaty's Swedish implementation).
    # Keep the contract generic: every producer emits ordinary link-run dicts.
    out += [(None, None, run) for run in art.get("references", [])]
    return out


def subject_links(art):
    """Concept (begrepp) edges from a court decision's `nyckelord`. nyckelord are
    metadata, not body text, so the inline-link walk misses them; each tags the
    case with a concept (`dcterms:subject`), so the concept page lists the cases
    tagged with it -- the case→concept half of the keyword graph."""
    return [(None, {"uri": begrepp_uri(n), "predicate": "dcterms:subject",
                    "text": n})
            for n in art.get("metadata", {}).get("nyckelord", []) if n.strip()]


# --------------------------------------------------------------------------
# document rows
# --------------------------------------------------------------------------

# For these sources the catalog's naming IS labels' naming: label is
# `short_id` and title is `official_title`, from the same
# `labels.document_labels` call that stamps the descriptive column -- one
# authority, so the citation line and the page eyebrow cannot drift apart.
# Verified against the live catalog before the merge (2026-08-08): 0 of
# 2,463 sampled rows differed. Only `kind` stays per-source, as data.
# The bespoke builders below each say why their label/title deliberately
# differ from labels' forms.
_LABELLED_KIND = {
    # lag vs forordning rather than one 'law': a förordning is subordinate to
    # the lag that delegates to it, and collapsing the two made the norm
    # hierarchy unreadable from the catalog -- 2025:1506 and 2025:1507 are one
    # rung apart, not the same kind of thing (`labels.sfs_is_statute`)
    "sfs": lambda art, lb: ("lag" if labels.sfs_is_statute(
        lb.official_title, _local(art["uri"])) else "forordning"),
    "forarbete": lambda art, lb: art.get("doctype", "forarbete"),
    "kommentar": lambda art, lb: "kommentar",
    "begrepp": lambda art, lb: "begrepp",
    "avg": lambda art, lb: art.get("org", "avg"),      # the organ (jo/jk/…)
    "rs": lambda art, lb: art.get("org", "rs"),        # the agency (fk/imy/…)
    "guidance": lambda art, lb: art.get("serie") or art["utgivare"],
    "lawreview": lambda art, lb: art["journal"],       # the journal (svjt/jp)
    "coe": lambda art, lb: art.get("doctype", "treaty"),
}


def _labelled_document(source, art, path):
    lb = labels.document_labels(source, art)
    return (art["uri"], source, _LABELLED_KIND[source](art, lb),
            lb.short_id, lb.official_title, str(path))


def dv_document(art, path):
    # bespoke: the catalog label is the WHOLE name-prefixed case label
    # ("Meteoriten (NJA 2025 s. 897)") that listings and every inbound citation
    # line print, where labels' short_id is the bare id ("NJA 2025 s. 897") for
    # the page eyebrow. Stamped onto the artifact at parse time
    # (build.dv_parse_run, via lib.casenaming.case_label), so the catalog stays
    # a pure consumer. labels.dv_fallback_label owns the pre-stamp fallback
    # chain (shared with labels._dv so the two never drift).
    label = labels.dv_fallback_label(art)
    return (art["uri"], "dv", "case", label, label, str(path))


def _eurlex_document(art, path):
    # bespoke: kind is the doctype (regulation/directive/judgment/treaty);
    # label is the CELEX (the short id citations use), where labels' short_id
    # is the printed designation ("(EU) 2016/679"). A judgment's
    # inbound-citation name is the case citation stamped at parse
    # ("C-311/18 (Schrems II)"), not its "Domstolens dom (...)" Formex title;
    # an act keeps its full title.
    label = art.get("celex") or _local(art["uri"])
    title = (art.get("label") if art.get("doctype") == "judgment"
             else art.get("title")) or label
    return (art["uri"], "eurlex", art.get("doctype", "eurlex"),
            label, title, str(path))


def _foreskrift_document(art, path):
    # bespoke: kind is the författningssamling (fffs/nfs/…), label the short id
    # citations + the bemyndigande margin use ("FFFS 2013:10"). The title is
    # the artifact's own, where labels' official_title interpolates the
    # designation into it ("Skolverkets föreskrifter (SKOLFS 2024:598) om …").
    label = art.get("identifier") or _local(art["uri"])
    title = art.get("metadata", {}).get("title") or label
    return (art["uri"], "foreskrift", art.get("fs", "foreskrift"),
            label, title, str(path))


def hudoc_document(art, path):
    # bespoke: label is the ECLI (a stable machine id; the itemid as fallback),
    # where labels' short_id is the application number ("no. 8906/19") the page
    # eyebrow shows.
    label = art.get("ecli") or art.get("itemid") or _local(art["uri"])
    title = art.get("title") or label
    return (art["uri"], "hudoc", art.get("doctype", "case-law"),
            label, title, str(path))


def icrc_document(art, path):
    # bespoke: kind is the doctype (treaty/protocol/declaration), label the
    # treaty's full identifier ("Geneva Convention (I) on Wounded and Sick …"),
    # where labels' short_id is the curated abbreviation ("GK I").
    label = art.get("identifier") or ("ICRC " + art.get("number", ""))
    title = art.get("title") or label
    return (art["uri"], "icrc", art.get("doctype", "treaty"),
            label, title, str(path))


def untc_document(art, path):
    # bespoke: kind is the doctype (treaty/protocol), label the treaty title
    # (its identifier; the MTDSG id is the number), where labels' short_id is
    # the curated abbreviation ("CRC", "CMW").
    label = art.get("identifier") or ("MTDSG " + art.get("number", ""))
    title = art.get("title") or label
    return (art["uri"], "untc", art.get("doctype", "treaty"),
            label, title, str(path))


def icc_document(art, path):
    # bespoke: kind is the decision type (judgment/sentence/…), label the
    # DOCUMENT number ("ICC-01/05-01/13-1964", the citation form for the
    # specific decision), where labels' short_id is the CASE number the page
    # eyebrow shows. Title is the case name.
    label = art.get("docnumber") or _local(art["uri"])
    title = art.get("title") or label
    return (art["uri"], "icc", art.get("doctype", "judgment"),
            label, title, str(path))


def icj_document(art, path):
    # kind is the decision type (judgment/advisory opinion/order); label is the
    # Court's own citing form ("ICJ 70 (Judgment, 1986-06-27)"), where labels'
    # short_id is the bare case number the page eyebrow shows. Title is the
    # case name, which is how every citation to an ICJ decision reads.
    label = art.get("identifier") or _local(art["uri"])
    title = art.get("title") or label
    return (art["uri"], "icj", art.get("doctype", "dom"),
            label, title, str(path))


# the column `_expired_date` fills is compared against an ISO date, so only an
# ISO date may go into it
RE_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


#: the `expired` value for a document whose issuer states that it is superseded
#: but never when. Any date already past would do; this one reads as what it is
#: in a database dump and cannot collide with a real repeal date. The artifact
#: keeps the truth -- no `upphavd` -- so a page still prints no date it was
#: never told.
EXPIRED_UNDATED = "0001-01-01"


def _expired_date(art: dict) -> str | None:
    """The date a document stopped stating law, if its metadata declares one --
    else None. Stored on the documents row so that once the date has passed the
    document drops out of every *listing* of the corpus -- the browse trees
    (`facets._rows`), the feeds (`feeds.entries`) and search results
    (`search.REPEALED_IN_FORCE`) -- and off the context rail
    (`page._inbound_groups`, the I3 rule). It stays reachable by direct link,
    and stays *in* the search index carrying this date, so the query filter is
    the only thing between it and a reader: an advanced "search expired" option
    is a query change, not a reindex.

    Three kinds of document declare one, for the same reason. A statute names its
    repeal date (`rpubl:upphavandedatum`). A rättsligt ställningstagande is in
    force until the agency withdraws it, and a withdrawn one no longer says how
    the agency reads the rule -- which is the only reason it was on that
    paragraf's rail. Reading a paragraf whose rail listed thirteen
    ställningstaganden, twelve of them withdrawn, is what this covers. An EU act
    carries the date CELLAR says it stopped being in force, stamped on the
    artifact as a plain `expired` key (`cellar.notice_repeal_date`) --
    32016R0679 article 94 repealed 31995L0046 with effect from 2018-05-24.

    A date has to be an ISO one, because this column is compared against one.
    Where the issuer names a *successor* but no usable date, the document is
    expired all the same, at `EXPIRED_UNDATED`: it said that this wording was
    replaced, and reading the absence of a day as the absence of a repeal would
    leave a superseded wording listed as current -- the error this column exists
    to prevent. The EBA is the case it was added for; its version pages carry no
    repeal marker at all and their only date is an application date.

    That reaches one document outside guidance:
    `rs/kkv/2019:1`, which Konkurrensverket declares upphävt and replaced by
    2022:2 while dating it in prose ("20 oktober 2025"). Hiding it is what the
    rest of this docstring already argues for -- a withdrawn ställningstagande
    no longer says how the agency reads the rule, which is the only reason it
    was on that paragraf's rail.

    A withdrawal with neither a date nor a successor still stays listed: that is
    an issuer saying less than it knows, not a document we can place in time."""
    metadata = art.get("metadata", {})
    if metadata.get("status") == "upphävt":
        withdrawn = metadata.get("upphavd") or ""
        if RE_ISO_DATE.match(withdrawn):
            return withdrawn
        # an issuer that states *that* a document is superseded but never
        # *when*: the EBA's version pages carry no repeal marker at all, and
        # their only date is an application date. Reading the absence of a day
        # as the absence of a repeal would leave a superseded wording listed as
        # current, which is the error this column exists to prevent.
        return EXPIRED_UNDATED if (metadata.get("ersattAv")
                                   or metadata.get("ersattAvKalla")) else None
    declared = (art.get("expired")
                or metadata.get("properties", {}).get("rpubl:upphavandedatum"))
    return declared if declared and RE_ISO_DATE.match(declared) else None


def document_date(art: dict) -> str | None:
    """ONE date per document (ISO yyyy-mm-dd), for chronological ordering of
    listings and inbound references. This is a *projection*, not a key
    convention: the per-source keys deliberately name different events (a
    ruling's avgörandedatum, an agency decision's beslutsdatum, a väglednings
    antagen, a statute's utfärdandedatum, a föreskrifts tryck/ikraft dates, a
    treaty's opening/adoption/conclusion) and must not be collapsed onto one
    key -- a document has several dates; this chain just picks the one a
    listing sorts by. None when the artifact carries no date (the renderer
    sorts undated entries last)."""
    props = art.get("metadata", {}).get("properties", {})
    return (art.get("date") or art.get("avgorandedatum")
            or art.get("metadata", {}).get("beslutsdatum")
            or art.get("metadata", {}).get("antagen")
            or art.get("metadata", {}).get("utkomFranTryck")
            or props.get("rpubl:utfardandedatum")
            or props.get("rpubl:avgorandedatum")
            or props.get("rpubl:beslutsdatum"))


def _document_description(art, source):
    """A source's own one-line description of a document, for the browse listing --
    a court decision's sammanfattning (the referatrubrik that heads the entry after
    its number). None where a source has no such abstract, so the listing shows the
    short_title alone."""
    if source == "dv":
        return art.get("metadata", {}).get("sammanfattning")
    return None


_SNIPPET_LEN = 340


# node types that read as furniture, not prose: headings in both grammars,
# an EU act's preamble formalities ("med beaktande av …" citation nodes) and
# footnotes. The first *recital* is the act's own opening statement and wins.
_NOT_PROSE = frozenset({"heading", "citation", "note"})


def cut_snippet(text):
    """A snippet cut to one length, on a word boundary, with an ellipsis where
    it was cut. Relate stamps every `documents.snippet` through this, and
    /api/v1/card cuts a provision's own words the same way, so the two read
    alike in the same popover (rule:second-use-goes-to-lib)."""
    if len(text) > _SNIPPET_LEN:
        return text[:_SNIPPET_LEN].rsplit(" ", 1)[0] + " …"
    return text


def _prose_candidates(nodes):
    """Depth-first over an artifact tree: every non-furniture node whose text
    runs join to a real paragraph (>= 80 chars), uncut."""
    for node in nodes:
        if not isinstance(node, dict):
            continue
        kind = node.get("type") or ""
        if "rubrik" not in kind and kind not in _NOT_PROSE:
            body = text.runs_text(node.get("text") or []).strip()
            # an ISSN line is a författningssamling's masthead, not
            # prose; and a low alphanumerics-and-spaces ratio is OCR debris
            # off a scanned page (".-lascs.srii<~nt I J / …"), not a
            # paragraph -- digits stay welcome, lagtext is full of them
            if (len(body) >= 80 and not body.startswith("ISSN ")
                    and sum(c.isalnum() or c == " " for c in body)
                    / len(body) >= .8):
                yield body
        yield from _prose_candidates(node.get("children") or [])


def first_prose(art):
    """The document's own opening prose: the first non-furniture node whose
    text runs join to a real paragraph (>= 80 chars), cut at a word boundary
    around 340 chars. Structure-generic -- a förarbete lands on its first
    running paragraph, a wiki concept on its defining paragraph (`body` is
    the wiki artifacts' tree). None when the artifact opens with nothing
    prose-like (scanned page-image documents, bare registries)."""
    for prose in _prose_candidates(art.get("structure") or
                                   art.get("body") or []):
        return cut_snippet(prose)
    return None


def _first_of_type(nodes, kind):
    for node in nodes:
        if isinstance(node, dict):
            if node.get("type") == kind:
                return node
            found = _first_of_type(node.get("children") or [], kind)
            if found:
                return found
    return None


def _paragraf_prose(art):
    """An författning's opening as a reader cites it: the first paragraf's
    first stycke, led by its designation -- "1 kap. 1 § Fast egendom är
    jord. …". The walk to the paragraf skips every heading by construction;
    a paragraf carries its stycken as children (or, in older shapes, its own
    text runs)."""
    par = _first_of_type(art.get("structure") or [], "paragraf")
    if not par:
        return None
    stycke = _first_of_type(par.get("children") or [], "stycke")
    body = text.runs_text((stycke or par).get("text") or []).strip()
    if not body:
        return None
    # a stycke that introduces a list carries the items as punkt children --
    # quote the first item and say with an ellipsis that the list goes on
    punkt = _first_of_type((stycke or par).get("children") or [], "punkt")
    if punkt and text.runs_text(punkt.get("text") or []).strip():
        body = "%s %s …" % (
            body, text.runs_text(punkt.get("text") or []).strip())
    where = pinpoint_label(par.get("id") or "")
    return cut_snippet(("%s %s" % (where, body)) if where else body)


def _word_cap(text, words=50):
    """A narrative snippet capped at `words` words, ellipsis when cut."""
    parts = text.split()
    if len(parts) <= words:
        return text
    return " ".join(parts[:words]) + " …"


def _numbered_ground(art):
    """An EU court decision's own opening: its first numbered paragraph --
    "Begäran om förhandsavgörande avser tolkningen av artikel 4.1 …" --
    capped at 50 words. The keyword strings and quoted legislation that
    precede it (a judgment quotes whole recitals) are passed over."""
    def walk(nodes):
        for node in nodes:
            if isinstance(node, dict):
                if node.get("type") == "paragraph" and node.get("num"):
                    body = text.runs_text(node.get("text") or []).strip()
                    # an old judgment numbers its section headings too
                    # ("Facts and procedure") -- a ground is a sentence
                    if len(body) >= 40:
                        return _word_cap(body)
                found = walk(node.get("children") or [])
                if found:
                    return found
        return None
    return walk(art.get("structure") or [])


def _recital_prose(art):
    """An EU act's own opening statement: the first preamble recital, led by
    its number -- "(1) Skyddet för fysiska personer …"."""
    recital = _first_of_type(art.get("structure") or [], "recital")
    if not recital:
        return None
    body = text.runs_text(recital.get("text") or []).strip()
    if not body:
        return None
    num = recital.get("num")
    return cut_snippet(("(%s) %s" % (num, body)) if num else body)


# how an international court's decision opens before any substance: bench
# rosters, composition lines and the ICC's cover-page furniture ("Decision
# to be notified …", "SITUATION IN …") -- all census-found, none prose
_ROSTER = re.compile(r"^(Before\s*:|Before\s+(?:Judge|President)\b"
                     r"|Present\s*:"
                     r"|Present\s+(?:President|Vice-President|Judges?)\b"
                     r"|Composed\b|Composée\b"
                     r"|The Court,|The International Court of Justice,"
                     r"|Decision to be notified|Judgment to be notified"
                     r"|To be notified"
                     r"|SITUATION IN |IN THE CASE OF )")


def _document_snippet(art, source):
    """What the details panel opens with, per what each source actually has:
    the dv sammanfattning; an författning's (SFS or föreskrift) first
    paragraf with its "1 §"/"1 kap. 1 §" designation; an EU act's first
    recital with its "(1)" and an EU court decision its first numbered
    ground; a hudoc case's conclusions ("Violation of P1-1"
    -- its body text opens with procedural boilerplate); an ICC/ICJ
    decision's first paragraph past the bench roster; a journal article's
    first paragraph capped at 50 words; and the opening prose for everyone
    else."""
    described = _document_description(art, source)
    if described:
        return described
    if source in ("sfs", "foreskrift"):
        return _paragraf_prose(art) or first_prose(art)
    if source == "hudoc":
        return "; ".join(
            art.get("metadata", {}).get("conclusions") or []) or None
    if source == "lawreview":
        # the article's own first paragraph, capped at 50 words -- long
        # enough to say what it is about, short enough that mined OCR text
        # cannot ramble (the garbage gate in _prose_candidates still refuses
        # debris outright)
        for prose in _prose_candidates(art.get("structure") or []):
            return _word_cap(prose)
        return None
    if source in ("icc", "icj"):
        for candidate in _prose_candidates(art.get("structure") or []):
            if not _ROSTER.match(candidate):
                return cut_snippet(candidate)
        return None
    if source == "eurlex":
        # case law opens on its first numbered ground; only the *acts* take
        # the recital path -- a judgment quotes whole recitals of the act it
        # interprets, and the recital finder would happily serve those.
        # Case law is the CELEX's own sector: a 6-leading number, AG
        # opinions included
        if art["uri"].rsplit("/", 1)[-1].startswith("6"):
            ground = _numbered_ground(art)
            if ground:
                return ground
        else:
            recital = _recital_prose(art)
            if recital:
                return recital
    # a pre-Formex EU act (and the odd treaty) opens with its own title as a
    # plain text node -- a snippet that echoes the title says nothing the
    # panel does not already show, so skip past it to the next paragraph
    title = (art.get("title") or "").strip().casefold()
    for prose in _prose_candidates(art.get("structure") or
                                   art.get("body") or []):
        if title and prose[:60].strip().casefold() == title[:60].strip():
            continue
        return cut_snippet(prose)
    return None


def _document_publisher(art: dict) -> str | None:
    """The issuing organization, normalized only structurally (not renamed).

    It is catalogued because legacy Atom publisher filters are public request
    parameters; serving one must not reopen and parse the whole artifact corpus.
    """
    metadata = art.get("metadata", {})
    return (metadata.get("publisher")
            or metadata.get("properties", {}).get("dcterms:publisher")
            or art.get("publisher"))


def display_title(art, title):
    """The human title a document shows wherever it is named to a reader -- the
    page heading, a search hit, a listing entry: the act's established short name
    plus its citing acronym when the artifact carries them
    ("Cyberresiliensförordningen (CRA)"), else the given `title` (the full
    heading). Field-driven, not source-keyed -- any source that stamps
    `shortname`/`abbr` gets the same treatment; the rest fall back to their title,
    which for every other source already is the page heading."""
    name = art.get("shortname") or title
    abbr = art.get("abbr")
    return "%s (%s)" % (name, abbr) if abbr else name


# source -> its document-row builder. Module-level so `document_row` (~500k
# calls per full relate) doesn't rebuild this dispatch dict on every call.
_DOCUMENT_BUILDERS = {
    source: partial(_labelled_document, source) for source in _LABELLED_KIND
} | {
    "dv": dv_document, "eurlex": _eurlex_document,
    "foreskrift": _foreskrift_document, "hudoc": hudoc_document,
    "icrc": icrc_document, "untc": untc_document, "icc": icc_document,
    "icj": icj_document,
}


def document_row(art, path, source):
    return _DOCUMENT_BUILDERS[source](art, path)
