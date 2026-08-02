"""Fragment id -> the human pinpoint a reader recognises ("K2P16S5" -> "2 kap.
16 § 5 st").

Factored out of ``lib/page`` so the serving layer can name a pinpoint without
importing the renderer: ``lib/pins`` labels a citation-resolved search hit with
the provision it points at, and pulling all of page.py (and markupsafe) into the
API's search path for one string transform is weight the endpoint should not
carry. The same split ``lib/coe_ids`` makes for the CoE article grammar.
"""

import re

FRAG_LABEL = {"K": "kap.", "P": "§", "O": "mom.", "S": "st", "N": "p", "M": "men."}
_FRAG_SEG = re.compile(r"([KPOSNM])([0-9a-zåäö]+)")


def human_fragment(frag):
    """A fragment id -> a human pinpoint: "K2P16S5" -> "2 kap. 16 § 5 st";
    "sid39" -> "s. 39"; change markers ("L1988:187") and unknowns -> ""."""
    if not frag:
        return ""
    if frag.startswith("sid"):
        return "s. " + frag[3:]
    coe = re.fullmatch(
        r"A((?:\d+[A-Za-z]?|[IVXLCDM]+)(?:\.\d+)?)(?:-(\d+))?"
        r"(?:P(\d+)(?:-(\d+))?)?(?:L([a-z])(?:-(\d+))?)?", frag)
    if coe:
        parts = ["artikel %s" % coe.group(1)]
        if coe.group(3):
            parts.append("punkt %s" % coe.group(3))
        if coe.group(5):
            parts.append("led %s" % coe.group(5))
        instance = coe.group(6) or coe.group(4) or coe.group(2)
        if instance:
            parts.append("variant %s" % instance)
        return " ".join(parts)
    segs = _FRAG_SEG.findall(frag)
    return " ".join("%s %s" % (val, FRAG_LABEL[letter]) for letter, val in segs)


# an EU act anchors an article on its bare number ("32", "6.1", "6.1.c") -- a
# shape `human_fragment` deliberately does not type, since its callers in stats
# and eurlex/render handle those anchors themselves
_EU_ARTICLE = re.compile(r"\d+[a-z]?(?:\.\d+)*(?:\.[a-z])?")


def pinpoint_label(frag):
    """A fragment id -> the pinpoint to *show a reader*, covering the bare EU
    article anchor as well as the Swedish and CoE forms `human_fragment` types.
    Returns '' for an id with no reader-facing form."""
    return human_fragment(frag) or (
        "artikel " + frag if frag and _EU_ARTICLE.fullmatch(frag) else "")
