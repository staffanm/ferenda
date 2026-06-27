"""Segment a court decision's flat body into its instance/ruling structure.

The body (Rubrik/Stycke in document order) is grouped into the decision tree the
DV structural golden expects (REWRITE.md §4):

    delmal[ordinal]            split case (I, II) -- wraps the instances
      instans[court]           an instance stage
        betankande             föredragande/revisionssekreterare proposal
          domskal / domslut
        dom                    the court's own ruling
          domskal / domslut
        skiljaktig / tillagg   dissenting opinion / concurring addition

A small RANK-driven stack machine, like the SFS assembler -- recognizers
classify a block by the editorial cues the old `dv.py` keyed on, and a marker of
rank R closes everything of rank >= R before opening its node, inserting the
implicit parents the cue implies (a domskäl opens inside a dom; a dom inside an
instans). Plain paragraphs are not part of the skeleton; anything before the
first instans (the headnote/keyword preamble) simply has no open node.

Far simpler than the old FSMParser: no per-court state machine and no
recognizer-priority tables, just the markers, the nesting ranks, and one rule
that the precise court name (from the appellant-action wording, e.g. "väckte
talan vid Stockholms tingsrätt") refines the coarse one a dom anchor carries
("Tingsrätten (...) anförde").

The output is a pure skeleton (structural nodes only, no text) -- exactly what
the golden compares; paragraph membership for rendering is a later concern.
"""

import re

# nesting depth of each structural kind (outermost = 1)
RANK = {"delmal": 1, "instans": 2,
        "betankande": 3, "dom": 3, "skiljaktig": 3, "tillagg": 3,
        "domskal": 4, "domslut": 4}

# a court name in its full form (the form the golden records on an instans).
# Ordered longest-first within each family so the fullest surface form wins.
_COURT = (r"Högsta domstolen|Högsta förvaltningsdomstolen"
          r"|Hovrätten (?:över|för) [A-ZÅÄÖ][\wåäö]+(?: och [A-ZÅÄÖ]?[\wåäö]+)?"
          r"|(?:Svea|Göta) hovrätt"
          r"|[A-ZÅÄÖ][a-zåäö]+ (?:tingsrätt|hovrätt)"
          r"|(?:Förvaltningsrätten|Länsrätten|Kammarrätten) i [A-ZÅÄÖ][a-zåäö]+(?: län)?"
          r"|Mark- och miljööverdomstolen|Migrationsöverdomstolen")

# --- recognizers -------------------------------------------------------------
# Each returns the attributes captured, or None. The order they are tried in
# `classify` matters only where two could fire on one block (noted there).

# a split case: a lone roman numeral, optionally with its målnr ("I", "II.",
# "I (UM1001-08)") -- never a word starting with I ("Inledning")
RE_DELMAL = re.compile(r"^(I{1,3}|IV|VI{0,3}|IX|X)\.?(?:\s*\((\w+[-\s]?\d+)\))?$")

# a full court name on its own, for deciding whether a dom anchor's court is
# specific enough to start a new instance (vs a coarse "Tingsrätten")
RE_COURT_FULL = re.compile(r"(?:%s)$" % _COURT)

# court tokens an appellant-action wording may use, including abbreviations the
# golden records in full
_WCOURT = _COURT + r"|HD|HFD|Regeringsrätten"
_FULLEN = {"HD": "Högsta domstolen", "HFD": "Högsta förvaltningsdomstolen",
           "Regeringsrätten": "Regeringsrätten"}

# the appellant-action wording that opens an instance and names its court in
# full -- the precise name a dom anchor below usually abbreviates
RE_INSTANS = re.compile(
    r"(?:väckte (?:talan|åtal)(?:[^.]*?) vid|överklagade[^.]*?(?:till|hos|i)"
    r"|ansökte (?:vid|hos)|yrkade att) (%s)\b" % _WCOURT)
RE_REVISION = re.compile(r"sökte revision")        # -> Högsta domstolen

# the dom anchor: a court (often abbreviated -- Tingsrätten/HovR:n/HD) with its
# constitution/date, then a ruling verb. Opens the court's own dom + domskäl.
RE_DOM = re.compile(
    r"^((?:%s|Tingsrätten|TR:n|Hovrätten|HovR:n|HD|Migrationsverket)"
    r"(?:, [Mm]ark- och miljö(?:över)?domstolen)?)\s*\([^)]*\)"
    r"\s*(?:yttrade|anförde|meddelade|fastställde|stadfäste|beslöt"
    r"|fattade|förklarade|fann|uttalade)" % _COURT)

# the föredragande/revisionssekreterare proposal HD decides on
RE_BETANKANDE = re.compile(r"^(?:Målet|HD) avgjorde(?:s|) (?:målet )?efter "
                           r"föredragning|^Föredrag(?:anden|ningen)\b")

# a bare reasoning heading (no court anchor)
RE_DOMSKAL = re.compile(r"^(?:Skäl|Domskäl|Skälen för (?:avgörandet|beslutet)"
                        r"|(?:HovR:ns|Hovrättens|Tingsrättens) domskäl)\b")

# the operative ruling: a "<Court>s avgörande" heading, a bare "Domslut"/"Slut",
# or HD/HFD stating the disposition
RE_DOMSLUT = re.compile(
    r"^(?:Domslut|Slut|Avgörande"
    r"|(?:%s|HD|HovR:n|Hovrätten|Tingsrätten)[: ]?s? (?:avgörande|domslut)"
    r"|(?:HD|Hovrätten|Tingsrätten) (?:avslår|avvisar|fastställer|meddelar"
    r"|förklarar|ändrar|undanröjer|bifaller|lämnar))\b" % _COURT)

RE_SKILJAKTIG = re.compile(
    r"^(?:Justitie|Kammarrätts|Regerings|Försäkrings)råde[nt]\b.{0,90}?"
    r"\bvar (?:av )?skiljaktig")
RE_TILLAGG = re.compile(
    r"^Justitieråde[nt]\b.{0,90}?(?:tillade för egen del|gjorde[^.]{0,30}?tillägg)")


def classify(text):
    """(kind, attrs) for a body block's text, or None for a plain paragraph.
    attrs may carry `court` (instans/dom) or `ordinal` (delmal)."""
    m = RE_DELMAL.match(text)
    if m:
        return ("delmal", {"ordinal": m.group(1)})
    if RE_BETANKANDE.match(text):
        return ("betankande", {})
    if RE_SKILJAKTIG.match(text):
        return ("skiljaktig", {})
    if RE_TILLAGG.match(text):
        return ("tillagg", {})
    m = RE_DOM.match(text)                       # before domslut/domskal/instans
    if m:
        return ("dom", {"court": _norm_court(m.group(1))})
    if RE_DOMSLUT.match(text):
        return ("domslut", {})
    if RE_DOMSKAL.match(text):
        return ("domskal", {})
    m = RE_INSTANS.search(text)
    if m:
        return ("instans", {"court": _FULLEN.get(m.group(1), m.group(1))})
    if RE_REVISION.search(text):
        return ("instans", {"court": "Högsta domstolen"})
    return None


def _specific(court):
    """True for a full court name (one that should start a new instance), False
    for a coarse anchor form (Tingsrätten/Hovrätten) or None."""
    return bool(court and RE_COURT_FULL.match(court))


# abbreviated courts a dom anchor may use; only used to fill an instans that has
# no full name yet (a precise name from the wording always wins)
_ABBREV = {"TR:n": None, "Tingsrätten": None, "HovR:n": None,
           "Hovrätten": None, "HD": "Högsta domstolen"}


def _norm_court(court):
    return _ABBREV.get(court, court)


def _block_text(block):
    """Plain text of a block, whether an Avgorande Rubrik/Stycke (``.text`` is a
    str) or an artifact dict (``text`` may be an inline-run list)."""
    text = block.text if hasattr(block, "text") else block.get("text")
    if isinstance(text, list):
        return "".join(r if isinstance(r, str) else r.get("text", "")
                       for r in text)
    return text or ""


def nest(blocks):
    """Group body `blocks` into the **content-bearing** instance/ruling structure:
    the skeleton of nested ``{type, court?, ordinal?, children}`` nodes, with every
    block -- the structural markers and the plain prose between them -- attached as
    a leaf to the node it falls under, in document order. So the artifact carries
    the decision tree *with* its text; the structural golden's reducer
    (`skeleton_from_artifact`) drops the prose leaves, so the skeleton it compares
    is exactly what the pure segmenter produced. Blocks before the first instans
    (the headnote/keyword preamble) sit at the root."""
    root = []
    stack = []                       # [(rank, node)]

    def top():
        return stack[-1] if stack else None

    def close_to(rank):
        while stack and stack[-1][0] >= rank:
            stack.pop()

    def attach(block):
        (stack[-1][1]["children"] if stack else root).append(block)

    def push(kind, **attrs):
        node = {"type": kind, "children": []}
        node.update({k: v for k, v in attrs.items() if v is not None})
        (stack[-1][1]["children"] if stack else root).append(node)
        stack.append((RANK[kind], node))
        return node

    def open_instans(court):
        close_to(2)
        if not (top() and top()[1]["type"] == "delmal"):
            close_to(2)               # detach to root if not under a delmal
        push("instans", court=court)

    def current_instans():
        for rank, node in reversed(stack):
            if node["type"] == "instans":
                return node
        return None

    for block in blocks:
        hit = classify(_block_text(block))
        if not hit:
            attach(block)             # plain prose -> the current node (or root)
            continue
        kind, attrs = hit

        if kind == "delmal":
            close_to(1)
            push("delmal", ordinal=attrs.get("ordinal"))

        elif kind == "instans":
            open_instans(attrs.get("court"))

        elif kind in ("dom", "betankande", "skiljaktig", "tillagg"):
            court = attrs.get("court")
            inst = current_instans()
            # a dom anchor naming a *different* specific court is the next stage
            new_stage = (kind == "dom" and _specific(court) and inst is not None
                         and inst.get("court") and inst["court"] != court)
            if inst is None or new_stage:
                open_instans(court)
                inst = current_instans()
            if kind == "dom" and _specific(court) and not inst.get("court"):
                inst["court"] = court            # refine the coarse/missing name
            if kind == "betankande" and any(c["type"] == "betankande"
                                            for c in inst["children"]):
                attach(block)                     # föredragning already opened it
                continue
            close_to(3)
            push(kind)
            if kind in ("dom", "betankande"):    # reasoning follows by default
                push("domskal")

        elif kind in ("domskal", "domslut"):
            if not (top() and top()[0] == 3):    # need a dom/betankande parent
                close_to(4)
                if not (top() and top()[1]["type"] in ("dom", "betankande")):
                    if current_instans() is None:
                        open_instans(None)
                    push("dom")
            close_to(4)
            push(kind)

        attach(block)                 # the marker's own text -> the node it opened
    return root


def flatten(structure):
    """Document-order prose leaves of a content-bearing DV structure -- the
    structural wrapper nodes (instans/dom/domskäl/…) are transparent, their prose
    children hoisted -- for the linear renderer."""
    out = []
    for node in structure:
        if node.get("type") in RANK:          # a structural wrapper -> descend
            out.extend(flatten(node["children"]))
        else:
            out.append(node)
    return out
