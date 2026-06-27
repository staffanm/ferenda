"""Assembler: build a Forfattning tree from the tokenizer's event stream.

One stack machine driven by a containment rank table replaces the old
recursive-descent design where every constructor maintained its own loop
and its own list of terminating states. An Open* event closes everything
on the stack at the same or deeper rank, then nests inside what remains;
leaf events attach at their natural level.
"""

import logging

from ..lib import util
from . import tokenizer as t
from .model import (Avdelning, Bilaga, Forfattning, Kapitel, Lista,
                    Listelement, Overgangsbestammelse, Overgangsbestammelser,
                    Paragraf, Rubrik, Stycke, Underavdelning, UpphavdParagraf,
                    UpphavtKapitel)

log = logging.getLogger(__name__)

# containment hierarchy: opening an element at rank r closes all open
# elements at rank >= r
RANK = {
    Forfattning: 0,
    Overgangsbestammelser: 1,
    Bilaga: 1,
    Avdelning: 2,
    Overgangsbestammelse: 2,  # only ever nested in Overgangsbestammelser
    Underavdelning: 3,
    Kapitel: 4,
    Paragraf: 5,
    Stycke: 6,
}


def assemble(tokens):
    doc = Forfattning(ikrafttrader=tokens.preamble())
    stack = [doc]
    lists = []  # stack of open Lista objects, nested inside top stycke/container

    def close_to(rank):
        lists.clear()
        while RANK[type(stack[-1])] >= rank:
            stack.pop()

    def attach(node):
        stack[-1].children.append(node)

    def open_node(node, rank=None):
        close_to(rank if rank is not None else RANK[type(node)])
        wrap_ob_content(node)
        attach(node)
        stack.append(node)

    def wrap_ob_content(node):
        # content directly in the OB section (no SFS-number line seen) gets
        # wrapped in an Overgangsbestammelse assumed to belong to the act itself
        # -- the common case for a law with a single set of transitional
        # provisions (which implicitly carry the act's own SFS number), so this
        # is routine, not a problem worth warning about
        if (isinstance(stack[-1], Overgangsbestammelser) and
                not isinstance(node, Overgangsbestammelse)):
            log.debug("%s: övergångsbestämmelse without SFS number, "
                      "assuming the act's own", tokens.basefile)
            ob = Overgangsbestammelse(sfsnr=tokens.basefile)
            stack[-1].children.append(ob)
            stack.append(ob)

    def attach_leaf(node, rank):
        """Close down to the container that can hold this leaf, then append."""
        close_to(rank)
        wrap_ob_content(node)
        attach(node)

    def attach_listitem(ev):
        item = Listelement(ordinal=ev.ordinal, text=ev.text)
        while lists:
            innermost = lists[-1]
            if innermost.kind == ev.kind:
                if ev.kind == "strecksats":
                    item.ordinal = str(len(innermost.children) + 1)
                innermost.children.append(item)
                return
            if innermost.kind == "numrerad" and innermost.children:
                # bokstavs- and strecksatslistor nest under the latest
                # numbered item
                sub = Lista(kind=ev.kind)
                if ev.kind == "strecksats":
                    item.ordinal = "1"
                sub.children.append(item)
                innermost.children[-1].children.append(sub)
                lists.append(sub)
                return
            lists.pop()  # kind mismatch: the inner list is finished
        # no open list: a new one attaches to the open stycke, or failing
        # that directly to the current container
        lst = Lista(kind=ev.kind)
        if ev.kind == "strecksats":
            item.ordinal = "1"
        lst.children.append(item)
        lists.append(lst)
        wrap_ob_content(lst)
        attach(lst)

    in_numrerad = False
    while (ev := tokens.next_event(in_numrerad=in_numrerad)) is not None:
        match ev:
            case t.OpenAvdelning():
                open_node(Avdelning(ordinal=ev.ordinal, rubrik=ev.rubrik,
                                    underrubrik=ev.underrubrik))
            case t.OpenUnderavdelning():
                open_node(Underavdelning(ordinal=ev.ordinal, rubrik=ev.rubrik))
            case t.OpenKapitel():
                open_node(Kapitel(ordinal=ev.ordinal, rubrik=ev.rubrik,
                                  upphor=ev.upphor,
                                  ikrafttrader=ev.ikrafttrader))
            case t.OpenParagraf():
                open_node(Paragraf(ordinal=ev.ordinal, moment=ev.moment,
                                   upphor=ev.upphor,
                                   ikrafttrader=ev.ikrafttrader))
                open_node(Stycke(text=ev.first_stycke))
            case t.OpenOBSection():
                open_node(Overgangsbestammelser(rubrik=ev.rubrik))
            case t.OpenOB():
                if not any(isinstance(n, Overgangsbestammelser)
                           for n in stack):
                    # an SFS-number line without the customary separator
                    # heading still starts the transitional provisions
                    log.debug("%s: övergångsbestämmelser without separator"
                              " heading", tokens.basefile)
                    open_node(Overgangsbestammelser(
                        rubrik="[Övergångsbestämmelser]"))
                close_to(RANK[Overgangsbestammelse])
                ob = Overgangsbestammelse(sfsnr=ev.sfsnr)
                attach(ob)
                stack.append(ob)
            case t.OpenBilaga():
                open_node(Bilaga(rubrik=ev.rubrik, upphor=ev.upphor,
                                 ikrafttrader=ev.ikrafttrader))
            case t.UpphavtKapitelEv():
                attach_leaf(UpphavtKapitel(ordinal=ev.ordinal, text=ev.text),
                            RANK[Kapitel])
            case t.UpphavdParagrafEv():
                attach_leaf(UpphavdParagraf(ordinal=ev.ordinal, text=ev.text),
                            RANK[Paragraf])
            case t.RubrikEv():
                attach_leaf(Rubrik(text=ev.text, underrubrik=ev.underrubrik,
                                   upphor=ev.upphor,
                                   ikrafttrader=ev.ikrafttrader),
                            RANK[Paragraf])
            case t.StyckeEv():
                # a stycke cannot start mid-sentence: when a numbered list is
                # embedded in a clause ("Den som ... vållar [1. 2. 3.] döms för
                # ..."), the text after the list is the sentence continuing, not
                # a new stycke. Both the old HTML and the new JSON source carry a
                # blank line before it, so the leading case is the only signal --
                # a lowercase block right after an open list folds back into the
                # stycke that owns the list (keeping the following genuine,
                # capitalised stycke's ordinal). Scoped to an open list so the
                # lowercase definienda of a definition paragraph ("konsument: ..."
                # under "I denna lag avses med", which carry no list) stay their
                # own stycken.
                if (lists and isinstance(stack[-1], Stycke)
                        and ev.text[:1].islower()):
                    stack[-1].text = util.normalize_space(
                        stack[-1].text + " " + ev.text)
                else:
                    open_node(Stycke(text=ev.text))
            case t.ListItemEv():
                attach_listitem(ev)
            case t.TabellEv():
                lists.clear()
                wrap_ob_content(ev.tabell)
                attach(ev.tabell)
        in_numrerad = any(lst.kind == "numrerad" for lst in lists)
    return doc
