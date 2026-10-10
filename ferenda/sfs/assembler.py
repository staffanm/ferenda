"""Assembler: build a Forfattning tree from the tokenizer's event stream.

One stack machine driven by a containment rank table replaces the old
recursive-descent design where every constructor maintained its own loop
and its own list of terminating states. An Open* event closes everything
on the stack at the same or deeper rank, then nests inside what remains;
leaf events attach at their natural level.
"""

import logging
import re

from . import tokenizer as t
from .model import (
    Avdelning,
    Bilaga,
    Forfattning,
    Kapitel,
    Lista,
    Listelement,
    Listtext,
    Overgangsbestammelse,
    Overgangsbestammelser,
    Paragraf,
    Rubrik,
    Stycke,
    Underavdelning,
    UpphavdParagraf,
    UpphavtKapitel,
)
from .nf import BEGREPP_RULES

log = logging.getLogger(__name__)

# a lowercase definition in a term-list paragraf: "konsument: en fysisk person
# som ...". The term is a short phrase; one naming a stycke or a punkt
# ("tredje stycket: 90 procent", 1989:1021 1 §) is a list item's own text
re_Definition = re.compile(r"^(?P<term>[a-zåäö][^:]{0,80}): \S")
re_NotATerm = re.compile(r"stycke|punkt")

# a list of one kind nests under the latest item of a list of another: a
# letter list under a numbered item, a dash list or a roman list under a letter
# item ("3. … a) … att anlägga - ett industriområde, …", 2010:900 4 kap. 2 §),
# a letter list under a roman item. A letter item after a dash list closes the
# dash list instead: its letters most often go on with the list above it
NESTS = {
    "numrerad": {"bokstav", "romersk", "strecksats"},
    "bokstav": {"romersk", "strecksats"},
    "romersk": {"bokstav", "strecksats"},
    "strecksats": set(),
}

# containment hierarchy: opening an element at rank r closes all open
# elements at rank >= r
RANK: dict[type, int] = {
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

    def items(lista):
        return [c for c in lista.children if isinstance(c, Listelement)]

    def nests(ev):
        """Does this item open a list nested under the innermost list's latest
        item (rather than go on with an open list, or close lists)?"""
        return (lists and not any(lst.kind == ev.kind for lst in lists)
                and ev.kind in NESTS[lists[-1].kind] and items(lists[-1]))

    def attach_listitem(ev, opening=None):
        """`opening` is the words that open a nested list this item starts."""
        item = Listelement(ordinal=ev.ordinal, text=ev.text)
        if any(lst.kind == ev.kind for lst in lists):
            # the item goes on with an open list: close the lists inside it
            while lists[-1].kind != ev.kind:
                lists.pop()
            innermost = lists[-1]
            if ev.kind == "strecksats":
                item.ordinal = str(len(items(innermost)) + 1)
            innermost.children.append(item)
            return
        while lists and not nests(ev):
            assert opening is None, "only a nested list takes opening words"
            lists.pop()  # the inner list is finished
        if lists:
            sub = Lista(kind=ev.kind)
            if opening is not None:
                sub.children.append(Listtext(opening))
            if ev.kind == "strecksats":
                item.ordinal = "1"
            sub.children.append(item)
            items(lists[-1])[-1].children.append(sub)
            lists.append(sub)
            return
        # no open list: a new one attaches to the open stycke, or failing
        # that directly to the current container
        lst = Lista(kind=ev.kind)
        if ev.kind == "strecksats":
            item.ordinal = "1"
        lst.children.append(item)
        lists.append(lst)
        wrap_ob_content(lst)
        attach(lst)

    def is_definition(text):
        """A lowercase "term: …" block in a paragraf that announces a term
        list ("I denna lag avses med"): a definition of its own, whose stycke
        the list of an earlier definition must not swallow."""
        m = re_Definition.match(text)
        return (m is not None and not re_NotATerm.search(m.group("term"))
                and len(stack) > 1 and any(
                    isinstance(c, Stycke) and BEGREPP_RULES.trigger(c.text)
                    for c in stack[-2].children))

    in_numrerad = False
    # lowercase blocks after an open list, waiting for the next event to tell
    # where in the list they go: (innermost list, text)
    pending = None
    opening = None
    while (ev := tokens.next_event(in_numrerad=in_numrerad)) is not None:
        if pending is not None:
            if (isinstance(ev, t.StyckeEv) and ev.text[:1].islower()
                    and not is_definition(ev.text)):
                pending = (pending[0], pending[1] + " " + ev.text)
                continue
            # the lowercase blocks after a list item stay in the list, where
            # the source has them: they open the list nested under that item
            # when the next item starts one, else they follow the item --
            # between two items, or after the last one, ending the sentence
            lista, text = pending
            if isinstance(ev, t.ListItemEv) and nests(ev):
                opening = text
            else:
                lista.children.append(Listtext(text))
            pending = None
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
                # a stycke cannot start mid-sentence: when a list is embedded
                # in a clause ("Den som ... vållar [1. 2. 3.] döms för ..."),
                # the text after the list is the sentence continuing, not a new
                # stycke. Both the old HTML and the new JSON source carry a
                # blank line before it, so the leading case is the only signal:
                # a lowercase block right after an open list waits for the next
                # event (`pending`) and stays in the list as a Listtext, which
                # keeps the following genuine, capitalised stycke's ordinal. Scoped to an open list so the
                # lowercase definienda of a definition paragraph ("konsument:
                # ..." under "I denna lag avses med") stay their own stycken --
                # also after a definition that holds a list.
                if (lists and isinstance(stack[-1], Stycke)
                        and ev.text[:1].islower()
                        and not is_definition(ev.text)):
                    pending = (lists[-1], ev.text)
                else:
                    open_node(Stycke(text=ev.text))
            case t.ListItemEv():
                attach_listitem(ev, opening)
                opening = None
            case t.TabellEv():
                lists.clear()
                wrap_ob_content(ev.tabell)
                attach(ev.tabell)
        in_numrerad = any(lst.kind == "numrerad" for lst in lists)
    if pending is not None:
        pending[0].children.append(Listtext(pending[1]))
    return doc
