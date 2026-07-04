"""The canonical walk over an EU act's artifact tree, and the one sub-article id
grammar every layer shares.

This is artifact-format machinery, not source code: it reads the published EU-act
artifact (the JSON source of truth) the way the layout module reads the URI/path
grammar. Three consumers must agree on the *same* anchors -- the eurlex parser
that mints them, the renderer that turns a node into an addressable heading, and
the wiki guidance layer that links commentary onto a point -- so the grammar lives
here, once, keyed on artifact node metadata (`rule:second-use-goes-to-lib`).

`nest` (the parse-time tree *builder*) stays in `eurlex/structure`: it is genuine
source parsing, run only by eurlex. `flatten` is its document-order inverse; the
eurlex parser imports these block-kind constants back from here so the producer
and the consumers share one vocabulary.
"""

# block kinds that carry a citable anchor (the artifact node `type` values)
ARTICLE = "article"
PARAGRAPH = "paragraph"
POINT = "point"


def flatten(structure):
    """The inverse of `eurlex.structure.nest`: the document-order flat block list (a
    container becomes its own block, sans `children`, followed by its flattened
    children)."""
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
    if t == PARAGRAPH:
        return "%s.%s" % (cur_article, num)
    if t == POINT:
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
        if t == ARTICLE:
            cur_article, cur_parag = b.get("id") or num, None
            if cur_article:
                yield cur_article, b
        elif t == PARAGRAPH:
            cur_parag = num
            key = subarticle_key(t, num, cur_article, cur_parag)
            if key:
                yield key, b
        elif t == POINT:
            key = subarticle_key(t, num, cur_article, cur_parag)
            if key:
                yield key, b
        elif t == "recital" and (num or "").isdigit():
            yield "recital-%s" % num, b
