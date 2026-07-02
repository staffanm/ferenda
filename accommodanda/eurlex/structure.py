"""Group an EU act's flat block sequence into its containment hierarchy.

Formex (and the OJ HTML behind it) is deeply nested -- an act is a preamble
(citations + recitals) followed by enacting terms divided into
parts > titles > chapters > sections, the articles within them, and each
article's paragraphs and points, with annexes after. The parser flattens that to
an ordered `Block` list that keeps the signals: a `heading` carries its division
`level`, an `article`/`paragraph`/`point` its `num` and citation `id` (anchor).
`nest` rebuilds the tree from those signals:

  * a `heading` opens an `avdelning` (division) nested under the nearest open
    division of lower `level` (parts > titles > chapters > sections);
  * an `article` is contained by the current deepest division (or the root) and
    holds the `paragraph`s that follow; a `paragraph` holds its `point`s;
  * preamble matter (title/keyword/citation/recital/preamble) and trailing
    matter (ruling/signature) stay where they fall, as leaves.

Citation ids are untouched -- an article keeps its anchor `id` (`celex#5`), a
point its `celex#5.2.a` -- so nesting changes the shape, never the targets.
`flatten` is the inverse document-order view (a safety net / linear consumer).
"""

# block kinds that contain others (everything else is a leaf)
_DIVISION = "heading"
_ARTICLE = "article"
_PARAGRAPH = "paragraph"
_POINT = "point"
# leaves that end an article's run (trailing matter), vs. preamble matter which
# simply precedes the first article
_CLOSERS = ("ruling", "signature")


def nest(blocks):
    """Flat EU-act block dicts -> a nested `structure` list."""
    root = []
    divs = []                 # open division nodes, increasing `level`
    article = parag = None    # current open article / paragraph

    def parent():
        return divs[-1]["children"] if divs else root

    for b in blocks:
        t = b.get("type")
        if t == _DIVISION:
            level = b.get("level") or 1
            while divs and (divs[-1].get("level") or 1) >= level:  # ty: ignore[unsupported-operator]  # artifact block dicts are untyped; level is int when present
                divs.pop()
            node = {**b, "children": []}
            parent().append(node)
            divs.append(node)
            article = parag = None
        elif t == _ARTICLE:
            node = {**b, "children": []}
            parent().append(node)
            article, parag = node, None
        elif t == _PARAGRAPH:
            node = {**b, "children": []}
            (article["children"] if article else parent()).append(node)
            parag = node
        elif t == _POINT:
            target = parag or article
            (target["children"] if target else parent()).append(dict(b))
        else:
            parent().append(dict(b))
            if t in _CLOSERS:
                article = parag = None
    return root


def flatten(structure):
    """The inverse of `nest`: the document-order flat block list (a container
    becomes its own block, sans `children`, followed by its flattened children)."""
    out = []
    for node in structure:
        if "children" in node:
            out.append({k: v for k, v in node.items() if k != "children"})
            out.extend(flatten(node["children"]))
        else:
            out.append(node)
    return out


def subarticle_key(t, num, cur_article, cur_parag):
    """The citation anchor for a sub-article block -- the **dotted** `4.5` / `6.2.a`
    grammar, from the block's running article/paragraph context. A paragraph is
    `article.paragraph`, a point `article.paragraph.point` (or `article.point` for a
    point sitting directly under the article, like a definitions-article entry).
    None when the block cannot anchor (no article context or no number). The one
    canonical sub-article id grammar -- shared by the renderer (the node id it
    mints), the wiki commentary headings (`## Artikel 5.2 a` -> "5.2.a") and the
    guidance linker's `.ann` keys -- so every layer lands on the same node."""
    if not (cur_article and num):
        return None
    if t == _PARAGRAPH:
        return "%s.%s" % (cur_article, num)
    if t == _POINT:
        return ("%s.%s.%s" % (cur_article, cur_parag, num) if cur_parag
                else "%s.%s" % (cur_article, num))
    return None


def anchored_blocks(structure):
    """Walk the act in document order yielding `(anchor, block)` for every block
    a citation (or a guidance link) can target: an article (anchor = its id or
    number), a sub-article paragraph/point (the `subarticle_key` paren form), and
    a numbered recital (`recital-N`). Blocks that cannot anchor are skipped. The
    running article/paragraph context is tracked exactly as `render_eurlex` does,
    so these anchors are the ones the renderer actually mints."""
    cur_article = cur_parag = None
    for b in flatten(structure):
        t = b.get("type")
        num = b.get("num")
        if t == _ARTICLE:
            cur_article, cur_parag = b.get("id") or num, None
            if cur_article:
                yield cur_article, b
        elif t == _PARAGRAPH:
            cur_parag = num
            key = subarticle_key(t, num, cur_article, cur_parag)
            if key:
                yield key, b
        elif t == _POINT:
            key = subarticle_key(t, num, cur_article, cur_parag)
            if key:
                yield key, b
        elif t == "recital" and (num or "").isdigit():
            yield "recital-%s" % num, b
