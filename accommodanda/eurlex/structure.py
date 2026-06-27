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
            while divs and (divs[-1].get("level") or 1) >= level:  # ty: ignore[unsupported-operator]
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
