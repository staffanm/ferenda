"""`lagen eurlex ai-annotate <CELEX>` -- author the editorial `.ann` layer for a
sector-3 EU act (regulation/directive/decision) with an LLM.

The act's parsed artifact is flattened to a plain-text/markdown rendering and
spliced into the preamble-analyzer prompt; an OpenAI-compatible chat-completions
endpoint (Berget) returns the editorial JSON -- thematic recital groups and the
article<->recital cross-reference. We validate the shape and write it, wrapped in
`{"editorialLayer": ...}`, as a `.ann` sidecar next to the artifact, where
`generate` folds it into the act's page (lib.render.Editorial).

The LLM is called only here, on an explicit `ai-annotate` of named CELEX ids --
never as part of a corpus-wide parse/relate/generate.
"""

import json
from pathlib import Path

from ..lib import catalog, layout, llm
from .structure import flatten

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
    lines = ["# %s" % art.get("title", art["celex"]), ""]
    for b in flatten(art["structure"]):
        text = catalog.runs_text(b["text"]).strip()
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


def _validate(content):
    """Parse and shape-check the model's reply: a JSON object with exactly the
    two expected keys and no more recital groups than the prompt's hard cap.
    Raises (ValueError/AssertionError) on anything else rather than write a bad
    layer -- the message is fed back to the model on the retry."""
    layer = json.loads(llm.strip_fence(content))
    assert isinstance(layer, dict), "response is not a JSON object"
    groups = layer.get("recitalGroups")
    assert isinstance(groups, list), "response lacks a recitalGroups list"
    assert len(groups) <= MAX_RECITAL_GROUPS, \
        "too many recital groups: %d (max %d)" % (len(groups), MAX_RECITAL_GROUPS)
    assert isinstance(layer.get("articleToRecitals"), dict), \
        "response lacks an articleToRecitals object"
    return {"recitalGroups": groups,
            "articleToRecitals": layer["articleToRecitals"]}


def _author(prompt):
    """Call the model and validate; on a malformed or over-sectioned reply, retry
    once with the failure fed back as a corrective instruction. A plain re-prompt
    would be pointless -- the call is temperature 0 and deterministic -- so the
    retry must carry the reason the first answer was rejected. Raises if the
    second answer is bad too."""
    for attempt in range(2):
        try:
            return _validate(llm.complete(prompt))
        except (ValueError, AssertionError) as exc:
            if attempt:
                raise
            prompt += ("\n\nDITT FÖREGÅENDE SVAR UNDERKÄNDES: %s\n"
                       "Rätta detta och följ alla regler ovan exakt." % exc)


def annotate(celex):
    """Author and write the `.ann` editorial layer for one sector-3 CELEX; returns
    the written path."""
    assert celex.startswith("3") and len(celex) > 5 and celex[5] in "RLD", \
        ("%s: ai-annotate handles only sector-3 acts "
         "(regulation/directive/decision)" % celex)
    art_path = layout.artifact("eurlex", celex)
    assert art_path.exists(), \
        "%s: no parsed artifact at %s -- run `lagen eurlex parse %s` first" \
        % (celex, art_path, celex)
    art = json.loads(art_path.read_text())
    prompt = PROMPT.read_text().replace(PLACEHOLDER, act_markdown(art))
    layer = _author(prompt)
    out = art_path.with_suffix(".ann")
    out.write_text(json.dumps({"editorialLayer": layer},
                              ensure_ascii=False, indent=2))
    return out
