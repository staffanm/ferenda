"""`lagen eurlex ai-annotate <CELEX>` -- author the editorial `.ann` layer for a
sector-3 EU act (regulation/directive/decision) with an LLM.

The act's parsed artifact is flattened to a plain-text/markdown rendering and
spliced into the preamble-analyzer prompt; an OpenAI-compatible chat-completions
endpoint (Berget) returns the editorial JSON -- thematic recital groups and the
article<->recital cross-reference. We validate the shape and write it, wrapped in
`{"editorialLayer": ...}`, as a `.ann` layer in the curated store
(lib.annstore, the git-backed WIKI_ROOT/ann tree), where `generate` folds it
into the act's page (lib.render.Editorial). A verified (hand-checked) layer is
never regenerated without --force.

The LLM is called only here, on an explicit `ai-annotate` of named CELEX ids --
never as part of a corpus-wide parse/relate/generate.
"""

import json
from pathlib import Path

from ..lib import annstore, compress, layout, llm
from ..lib.eu_structure import flatten
from ..lib.text import runs_text

PROMPT = Path(__file__).with_name("preamble_analyzer_prompt.txt")
PLACEHOLDER = "[PASTE FULL LEGAL ACT TEXT HERE]"
# the hard cap the prompt states -- enforced here so a model that ignores it is
# rejected (and retried) rather than producing an over-sectioned preamble
MAX_RECITAL_GROUPS = 16


def act_markdown(art):
    """The parsed artifact flattened to a plain-text/markdown rendering of the
    act -- the analyzer's input. Keeps exactly the structure the prompt keys on:
    numbered recitals, article headings, numbered paragraphs and lettered points,
    so the model can mint the dotted "4.5" / "6.2.a" provision keys it is asked for."""
    # the parsed artifact always carries a `title` key; it can be None for an act
    # whose title never got extracted, in which case the CELEX is the heading
    lines = ["# %s" % (art["title"] or art["celex"]), ""]
    for b in flatten(art["structure"]):
        text = runs_text(b["text"]).strip()
        t, num = b["type"], b.get("num")
        if t == "recital":
            lines.append("(%s) %s" % (num, text) if num else text)
        elif t == "citation":
            lines.append("- %s" % text)
        elif t == "heading":
            lines.append("\n%s %s" % ("#" * min((b.get("level") or 1) + 1, 4), text))
        elif t == "article":
            # the block text already reads "Artikel 4 – <title>"; fall back to the
            # bare number only if it is empty
            lines.append("\n## %s" % (text or "Artikel %s" % (num or "")))
        elif t == "paragraph":
            lines.append("%s%s" % ("%s. " % num if num else "", text))
        elif t == "point":
            lines.append("  (%s) %s" % (num, text) if num else "  %s" % text)
        else:                       # preamble, ruling, note, row, keyword
            lines.append(text)
    return "\n".join(lines)


def _int_list(value, where):
    """`value` shape-checked as a non-empty list of ints (a JSON `true` must not
    pass -- bool is an int subclass), returned as ints. Raises `ValueError`
    naming `where` on anything else. These are recital numbers the renderer
    iterates and ranges over (lib.render.Editorial), so a string or a nested
    object here crashes every later generate; reject it at write time instead."""
    if not isinstance(value, list) or not value:
        raise ValueError("%s is not a non-empty list" % where)
    for n in value:
        if isinstance(n, bool) or not isinstance(n, int):
            raise ValueError("%s contains a non-integer recital: %r" % (where, n))
    return value


def _validate(content):
    """Parse and shape-check the model's reply into the editorial layer: a JSON
    object with a `recitalGroups` list (each group a dict with a two-integer
    `range` lo<=hi, within the prompt's hard cap) and an `articleToRecitals` map
    of article ref -> non-empty list of integer recital numbers. Every shape the
    renderer (lib.render.Editorial) later indexes is checked *here*, so a
    malformed-but-JSON reply is fed back to the model on the retry rather than
    written to the `.ann` and crashing every subsequent generate. Raises
    `ValueError` on anything else -- not assert: the retry loop load-bears on the
    raise, which -O would strip."""
    layer = json.loads(llm.strip_fence(content))
    if not isinstance(layer, dict):
        raise ValueError("response is not a JSON object")
    groups = layer.get("recitalGroups")
    if not isinstance(groups, list):
        raise ValueError("response lacks a recitalGroups list")
    if len(groups) > MAX_RECITAL_GROUPS:
        raise ValueError("too many recital groups: %d (max %d)"
                         % (len(groups), MAX_RECITAL_GROUPS))
    for i, g in enumerate(groups):
        if not isinstance(g, dict):
            raise ValueError("recital group %d is not an object" % i)
        rng = g.get("range")
        if not (isinstance(rng, list) and len(rng) == 2):
            raise ValueError("recital group %d has no two-integer range" % i)
        lo, hi = rng
        if (isinstance(lo, bool) or isinstance(hi, bool)
                or not isinstance(lo, int) or not isinstance(hi, int)):
            raise ValueError("recital group %d has no two-integer range" % i)
        if lo > hi:
            raise ValueError("recital group %d range is inverted: %r" % (i, rng))
    a2r = layer.get("articleToRecitals")
    if not isinstance(a2r, dict):
        raise ValueError("response lacks an articleToRecitals object")
    for key, recitals in a2r.items():
        _int_list(recitals, "articleToRecitals[%r]" % key)
    return {"recitalGroups": groups, "articleToRecitals": a2r}


def annotate(celex, force=False):
    """Author and write the `.ann` editorial layer for one sector-3 CELEX; returns
    the written path. Refuses (before the LLM spend) to regenerate a verified
    layer unless `force`."""
    assert celex.startswith("3") and len(celex) > 5 and celex[5] in "RLD", \
        ("%s: ai-annotate handles only sector-3 acts "
         "(regulation/directive/decision)" % celex)
    out = annstore.path("eurlex", celex)
    annstore.guard(out, force)
    art_path = layout.artifact("eurlex", celex)
    assert compress.exists(art_path), \
        "%s: no parsed artifact at %s -- run `lagen eurlex parse %s` first" \
        % (celex, art_path, celex)
    art = json.loads(compress.read_bytes(art_path))
    prompt = PROMPT.read_text().replace(PLACEHOLDER, act_markdown(art))
    layer = llm.author(prompt, _validate)
    return annstore.write(out, {"editorialLayer": layer},
                          annstore.artifact_input("eurlex", celex), force)
