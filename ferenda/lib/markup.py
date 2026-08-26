"""Make a markup document *diffable* -- one block element per line -- without
changing what its parser reads out of it.

A patch (`lib.patch`) is a unified diff over lines, so a source that ships its
whole body on one line cannot be patched usefully: every hunk rewrites tens of
thousands of characters, the diff is unreviewable, and the least upstream
reflow breaks it. Two of the patchable sources do exactly that -- 9% of dv's
innehåll HTML records are a single line (the longest 153 838 characters), and
eurlex's Formex/OJ manifestations are single-line as a rule (median longest
line 45 508 characters; the first pair of committed OJ-HTML patches weighed
238 KB and 198 KB for a handful of real edits).

Both entry points below are *line-only* transforms: they add newlines between
elements and never inside an element's text. That distinction is what makes
them safe -- a consumer that reads an element's own text and normalises
whitespace within it cannot see whitespace *between* elements. Inserting a
newline inside a block's text would be visible: dv's `collapse` deliberately
keeps newlines (that is how `<br>` survives), so a wrapped paragraph would come
out as a different stycke.

The safety therefore rests on the consumer flattening *per element*, and that
is a precondition to check before wiring a third source in. eurlex's
`parse_html` normalises whitespace over each block it emits, so it holds there
unconditionally. dv's `parse_body` holds only while a record has block tags at
all: with none it falls back to flattening the whole document as one node, and
a newline this module inserted between two `<div>`s would then land inside that
node's text. Measured over the corpus, no dv record lacks block tags -- but the
next source might, and `block_lines` is not safe for one that does.

Both are idempotent, so re-normalising an already-normalised document is a
no-op and a patch authored against one keeps applying.
"""

import re

from lxml import etree  # ty: ignore[unresolved-import]  # lxml ships no stubs

# HTML elements that may not appear inside a paragraph's text run, so a newline
# at their boundary can never land inside flattened text. Deliberately excludes
# `pre` (whitespace is significant inside it), `br` (dv's parse turns it *into*
# a newline, and a second one would show up as an empty line in the stycke) and
# every inline element.
HTML_BLOCKS = (
    "html|head|body|title|meta|link|style|script|"
    "div|section|article|aside|nav|header|footer|main|figure|figcaption|"
    "h1|h2|h3|h4|h5|h6|p|hr|blockquote|address|"
    "table|caption|colgroup|col|thead|tbody|tfoot|tr|td|th|"
    "ul|ol|li|dl|dt|dd|form|fieldset"
)

_OPEN = re.compile(r"[ \t]*\n?[ \t]*<(?=(?:%s)[\s/>])" % HTML_BLOCKS, re.IGNORECASE)
_CLOSE = re.compile(r"(</(?:%s)>)[ \t]*\n?[ \t]*" % HTML_BLOCKS, re.IGNORECASE)


def block_lines(html):
    """`html` with every block-level element starting on its own line: a newline
    replaces the (possibly empty) whitespace run before each block *open* tag
    and after each block close tag, so a paragraph and its `</p>` stay on the
    one line. Text inside a block is left exactly as it is, including any
    newlines already there."""
    return _CLOSE.sub(r"\1\n", _OPEN.sub("\n<", html)).strip("\n")


def indent_xml(root):
    """An lxml element re-serialised with one element per line.

    `etree.indent` only writes into a `text`/`tail` that is empty or
    whitespace-only, so an element carrying real text keeps that text on one
    line and only element-content nesting is broken apart -- the same guarantee
    `block_lines` gives for HTML, enforced by the serialiser rather than by a
    tag vocabulary (Formex names its elements per document type, so there is no
    fixed block set to key on). The caller parses with its own parser: what a
    remote-supplied manifestation may be trusted to contain is the source's
    call, not lib's."""
    etree.indent(root, space="  ")
    return etree.tostring(root, encoding="unicode")
