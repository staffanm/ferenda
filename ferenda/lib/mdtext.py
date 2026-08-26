"""One definition of "the artifact as markdown" -- the readable-text sibling of
:mod:`ferenda.lib.text`'s plain text, for consumers that want a document
body they can show or embed rather than the tree-formed artifact JSON: the
API's ``/document?format=md`` and the MCP ``get_document`` tool.

Same contract as text.py: pure functions over the artifact dict, dispatching on
node ``type`` only -- never on which source the artifact came from. The node
vocabulary is the union the sources emit (docs/api/README.md "Per-source
shapes"); a type this module does not know renders through the generic rule
(its runs as a paragraph, then its children), so a new source degrades to
readable prose rather than to nothing. The tuned types:

  * SFS-shaped: ``avdelning``/``kapitel``/``paragraf`` containers, ``stycke``
    with its ``beteckning`` ("1 §") bolded, ``rubrik`` levels as headings,
    ``punkt`` lists, ``tabell``/``rad`` as pipe tables, the amendment register
    as its own section;
  * eurlex-shaped: ``heading`` divisions and ``article`` as headings,
    ``paragraph``/``point``/``recital`` with the act's own markers
    ("1.", "a)", "(42)");
  * förarbete-shaped: ``avsnitt`` headings, lagtext ``kapitel``/``paragraf``
    markers, ``fotnot`` in italics, ``ruta`` as a blockquote.

Link runs become inline markdown links, so the citation graph survives into
the text. Body text is emitted as written -- no markdown escaping -- because
legal prose practically never opens a line with markdown syntax, and escaping
every ``*`` and ``[`` costs more readability than it buys; only link labels
and table cells are escaped, where a stray bracket or pipe breaks structure.
"""

import re

from .text import BODY_SECTIONS, presented_consolidation

# the whitespace collapse for one markdown block: a raw newline inside a run
# could open a new block mid-paragraph ("- " at line start becomes a list)
_WS = re.compile(r"\s+")


def _inline(runs):
    """Inline runs -> markdown inline text, link dicts as [text](uri)."""
    if runs is None:
        return ""
    if isinstance(runs, str):
        runs = [runs]
    out = []
    for run in runs:
        if isinstance(run, str):
            out.append(run)
            continue
        label, uri = run.get("text", ""), run.get("uri")
        if uri and label:
            out.append("[%s](%s)" % (
                label.replace("[", "\\[").replace("]", "\\]"), uri))
        else:
            out.append(label)
    return _WS.sub(" ", "".join(out)).strip()


def _h(depth, text):
    return "#" * max(1, min(depth, 6)) + " " + text


def _decap(label):
    """A shouting division designation with its word re-cased: "KAPITEL I" ->
    "Kapitel I". Only the designation word is a word -- the numeral after it
    stays as the source set it -- and a label that does not shout is left
    exactly as published (see eurlex/render._division_label for the survey
    behind that rule)."""
    if not label or not label.isupper():
        return label
    word, _, rest = label.partition(" ")
    return " ".join(x for x in (word.capitalize(), rest) if x)


def _cell(runs):
    return _inline(runs).replace("|", "\\|")


def _table(node, out):
    rows = [(r.get("cells") or [], r.get("th"))
            for r in node.get("children") or [] if isinstance(r, dict)]
    rows = [(cells, th) for cells, th in rows if cells]
    if not rows:
        return
    width = max(len(cells) for cells, _ in rows)
    lines = []
    if not rows[0][1]:
        # a pipe table needs a header row; a table whose first row is body
        # text gets an empty one so no cell is promoted to a heading
        lines.append("|" + " |" * width)
    lines.append("| " + " | ".join(["---"] * width) + " |")
    if rows[0][1]:
        lines.insert(0, "| " + " | ".join(
            (_cell(c) for c in rows[0][0] + [[]] * (width - len(rows[0][0]))))
            + " |")
        rows = rows[1:]
    for cells, _ in rows:
        cells = cells + [[]] * (width - len(cells))
        lines.append("| " + " | ".join(_cell(c) for c in cells) + " |")
    out.append("\n".join(lines))


def _walk(nodes, depth, out):
    for node in nodes or []:
        if isinstance(node, dict):
            _block(node, depth, out)


def _block(n, depth, out):
    t = n.get("type")
    body = _inline(n.get("text"))
    children = n.get("children")

    if t == "rubrik":
        if body:
            out.append(_h((n.get("level") or 1) + 1, body))
        return
    if t in ("avsnitt", "sektion"):
        level = n.get("level") or 1
        if body:
            out.append(_h(level + 1, body))
        _walk(children, level + 2, out)
        return
    if t == "heading":                      # eurlex division: label + title
        level = n.get("level") or 1
        head = " – ".join(x for x in (_decap(n.get("label")), body) if x)
        if head:
            out.append(_h(level + 1, head))
        _walk(children, level + 2, out)
        return
    if t == "article":                      # eurlex: label "Artikel 5", text = title
        label = n.get("label") or ("Artikel %s" % (n.get("num") or n.get("id") or "")).strip()
        out.append(_h(depth, " – ".join(x for x in (_decap(label), body) if x)))
        _walk(children, depth + 1, out)
        return
    if t == "artikel":                      # coe: text is the full printed heading
        out.append(_h(depth, body or "Artikel %s" % n.get("ordinal", "")))
        _walk(children, depth + 1, out)
        return
    if t in ("avdelning", "kapitel", "paragraf"):
        # SFS-shaped containers carry no text of their own (their rubrik child
        # is the heading); a förarbete lagtext block carries its printed marker
        # ("8 kap.", "1 §") as text
        for key, note in (("ikrafttrader", "Träder i kraft"),
                          ("upphor", "Upphör att gälla")):
            if n.get(key):
                out.append("*%s %s:*" % (note, n[key]))
        if body:
            out.append("**%s**" % body)
        _walk(children, depth, out)
        return
    if t == "stycke":
        if n.get("beteckning"):
            body = "**%s** %s" % (n["beteckning"], body) if body \
                else "**%s**" % n["beteckning"]
        elif n.get("ordinal"):              # dv/hudoc numbered paragraphs
            body = "%s. %s" % (n["ordinal"], body) if body else body
        if body:
            out.append(body)
        _walk(children, depth, out)
        return
    if t == "punkt":
        ordinal = str(n.get("ordinal") or "")
        if ordinal.isdigit():
            out.append("%s. %s" % (ordinal, body))
        elif ordinal:
            out.append("- %s) %s" % (ordinal, body))
        else:
            out.append("- " + body)
        _walk(children, depth, out)
        return
    if t == "lista":
        _walk(children, depth, out)
        return
    if t in ("tabell", "table"):
        if body:                            # a förarbete table's caption
            out.append("*%s*" % body)
        _table(n, out)
        return
    if t == "rad":                          # a stray row outside a tabell
        out.append("| " + " | ".join(_cell(c) for c in n.get("cells") or []) + " |")
        return
    if t == "recital":
        out.append("(%s) %s" % (n["num"], body) if n.get("num") else body)
        return
    if t == "paragraph":                    # eurlex numbered article paragraph
        if body:
            out.append("%s. %s" % (n["num"], body) if n.get("num") else body)
        _walk(children, depth, out)
        return
    if t == "point":
        indent = "  " * ((n.get("depth") or 1) - 1)
        num = str(n.get("num") or "")
        marker = "%s) " % num if num.isalnum() else ""
        out.append(indent + "- " + marker + body)
        _walk(children, depth, out)
        return
    if t == "note":                         # OJ footnote, printed "(1) EUT C …"
        out.append("(%s) %s" % (n["num"], body) if n.get("num") else body)
        return
    if t == "ruling":
        out.append("%s. %s" % (n["num"], body) if n.get("num") else body)
        return
    if t == "fotnot":
        if body:
            out.append("*%s*" % body)
        return
    if t == "ruta":
        if body:
            out.append("> " + body)
        return
    # everything else -- eurlex preamble/citation/keyword/stycke, dv containers,
    # upphavd, signatur, bild, a footnote dict {num, text}, future types: its
    # runs as a paragraph (the printed number parenthesised), then its children
    if body:
        num = n.get("num") or n.get("ordinal")
        out.append("(%s) %s" % (num, body) if num else body)
    _walk(children, depth, out)


def _doc_title(art, fallback):
    """The one-line name the markdown opens with: the artifact's own full title
    ("Räntelag (1975:635)"), the printed identifier prefixed where the title
    does not already carry it ("Prop. 2017/18:199: En stärkt minoritetspolitik"),
    or whichever of the two exists. `fallback` covers the artifacts that carry
    no name of their own (a kommentar has only `annotates` + uri) -- the
    caller's catalog title."""
    meta = art.get("metadata") or {}
    props = meta.get("properties") or {}
    title = props.get("dcterms:title") or art.get("title") or meta.get("title")
    ident = art.get("identifier") or art.get("label")
    if title and ident and ident.lower() not in title.lower():
        return "%s: %s" % (ident, title)
    return title or ident or fallback or ""


def _summary(art):
    """The document's own abstract, whichever field its source stores it in:
    avg ``sammanfattning``, hudoc ``summary``, dv ``metadata.sammanfattning``."""
    meta = art.get("metadata") or {}
    return (art.get("sammanfattning") or art.get("summary")
            or (meta.get("sammanfattning") if isinstance(meta, dict) else None))


def _amendments(art, out):
    """The SFS/föreskrift amendment register as its own section: one heading
    per amendment with the register facts as a bullet list, then its
    övergångsbestämmelser. Included because the register is part of what the
    document says (text.document_text reads the same content)."""
    amendments = art.get("amendments") or []
    if not amendments:
        return
    out.append("## Ändringar")
    for a in amendments:
        props = a.get("properties") or {}
        # SFS register rows carry rdf-ish properties; a föreskrift's are flat
        out.append("### " + (props.get("dcterms:identifier")
                             or a.get("identifier") or a.get("uri") or ""))
        facts = [(label, value) for label, value in (
            ("Omfattning", props.get("rpubl:andrar")),
            ("Ikraftträder", props.get("rpubl:ikrafttradandedatum")),
            ("Beslutad", a.get("beslutsdatum")),
            ("Förarbeten", ", ".join(a.get("forarbeten") or []) or None),
        ) if value]
        if facts:
            out.append("\n".join("- %s: %s" % f for f in facts))
        _walk(a.get("content"), 4, out)


def node_markdown(node):
    """One body node's subtree as markdown -- the pinpoint-sized answer."""
    out = []
    _block(node, 2, out)
    return "\n\n".join(out)


def document_markdown(art, title=None):
    """The whole document as markdown: title, abstract, the presented body
    (the latest consolidation where one exists, same selection as
    text.body_sections), footnotes under their own heading, and the amendment
    register. `title` is the caller's catalog title, used only when the
    artifact names itself no other way."""
    out = []
    name = _doc_title(art, title)
    if name:
        out.append("# " + _WS.sub(" ", name).strip())
    summary = _summary(art)
    if summary:
        out.append(_inline(summary))
    cons = presented_consolidation(art)
    if cons:
        _walk(cons["structure"], 2, out)
    else:
        for section in BODY_SECTIONS:
            nodes = art.get(section)
            if section == "footnotes" and nodes:
                out.append("## Noter")
            _walk(nodes, 2, out)
    _amendments(art, out)
    return "\n\n".join(out)
