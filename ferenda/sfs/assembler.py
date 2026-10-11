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
# som ...", or a term alone over the list that defines it ("vrak: ‖ 1. …",
# 1994:1009 9 kap. 1 §). The term is a short phrase; one naming a stycke or a
# punkt ("tredje stycket: 90 procent", 1989:1021 1 §) is a list item's own text
re_Definition = re.compile(r"^(?P<term>[a-zåäö][^:]{0,80}):(?: \S|$)")
re_NotATerm = re.compile(r"stycke|punkt")

# a list of one kind nests under the latest item of a list of another: a
# letter list under a numbered item, a dash list or a roman list under a letter
# item ("3. … a) … att anlägga - ett industriområde, …", 2010:900 4 kap. 2 §),
# a letter list under a roman item. Any other new list nests only under an
# item that does not end its sentence (Assembler nests): a letter list after a
# dash list most often is a list of its own
NESTS = {
    "numrerad": {"bokstav", "romersk", "strecksats"},
    "bokstav": {"romersk", "strecksats"},
    "romersk": {"bokstav", "strecksats"},
    "strecksats": set(),
}

# a list item that ends without punctuation or a joining word goes on in the
# blocks after it ("2. Genom förordningen upphävs ‖ kungörelsen (1937:864) …,
# ‖ kungörelsen (1963:456) …,", 1984:545 övergångsbestämmelser). A colon
# announces what follows, so it ends nothing ("– företag som uppfyller mer än
# ett av följande villkor: ‖ a) …", 1995:1554 1 kap. 3 §)
re_ItemEnd = re.compile(r"(?:[,.;]|\b(?:och|eller|samt))$")


def ends(text):
    """Does the block end its sentence or its item, also inside a closing
    bracket or quote ("(Se vidare anvisningarna.)", 1928:370)?"""
    return re_ItemEnd.search(text.rstrip().rstrip(")\"”’»")) is not None


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
        item (rather than go on with an open list, or close lists)? A list of
        a kind that NESTS does not name nests under an item that does not end
        its sentence ("(e) Bevisupptagning må vägras endast i följande fall:
        ‖ (1) …", 1958:522 artikel 7)."""
        if (not lists or any(lst.kind == ev.kind for lst in lists)
                or not items(lists[-1])):
            return False
        return (ev.kind in NESTS[lists[-1].kind]
                or not ends(items(lists[-1])[-1].text))

    def attach_listitem(ev, opening=None):
        """`opening` is the blocks of words that open a nested list this item
        starts."""
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
            assert not opening, "only a nested list takes opening words"
            lists.pop()  # the inner list is finished
        if lists:
            sub = Lista(kind=ev.kind)
            sub.children.extend(Listtext(text) for text in opening or ())
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

    def place_pending(lista, blocks, ev):
        """The lowercase blocks after a list item stay in the list, where the
        source has them. Two or more blocks after an item that does not end
        its sentence are the entries of a list without markers, nested under
        that item. Else the blocks open the list nested under the item when
        the next item starts one, or they follow the item -- between two
        items, or after the last one, ending the sentence. Each block stays a
        text of its own, as the source prints it ("i Nederländerna … ‖ i
        Sverige …", 1983:22); a blank line in the middle of a sentence is an
        error in the source, which a patch removes. Returns the blocks that
        open the next item's nested list, if any."""
        last = lista.children[-1]
        if (len(blocks) > 1 and isinstance(last, Listelement)
                and not ends(last.text)
                and not (isinstance(ev, t.ListItemEv) and nests(ev))):
            last.children.append(Lista(kind="omarkerad", children=[
                Listelement(ordinal=str(n), text=text)
                for n, text in enumerate(blocks, 1)]))
            return None
        if isinstance(ev, t.ListItemEv) and nests(ev):
            return blocks
        lista.children.extend(Listtext(text) for text in blocks)
        return None

    def is_label(text):
        """A lowercase block between a stycke and a list, both without an end:
        a group label that goes on with the stycke's sentence ("Vid
        tillämpning av första stycket gäller ‖ i fråga om byggnads- och
        markarbeten ‖ 1. att …", 1983:55). It must not open a stycke of its
        own, or the stycken after it get the wrong number."""
        return (not lists and isinstance(stack[-1], Stycke)
                and text[:1].islower() and not is_definition(text)
                and not ends(text) and not ends(stack[-1].text))

    def place_label(text, ev):
        """The group label opens the list that `ev` starts, or else it is a
        stycke after all."""
        if isinstance(ev, t.ListItemEv):
            attach_listitem(ev)
            lists[0].children.insert(0, Listtext(text))
            labelled.append(lists[0])
        else:
            open_node(Stycke(text=text))

    in_numrerad = False
    # lowercase blocks after an open list, waiting for the next event to tell
    # where in the list they go: (innermost list, [block text, ...])
    pending = None
    opening = None
    # a group label waiting for the list item after it, and the lists that
    # open with a group label
    label = None
    labelled = []
    while (ev := tokens.next_event(in_numrerad=in_numrerad)) is not None:
        if label is not None:
            place_label(label, ev)
            label = None
            if isinstance(ev, t.ListItemEv):
                in_numrerad = any(lst.kind == "numrerad" for lst in lists)
                continue
        if pending is not None:
            if (isinstance(ev, t.StyckeEv) and ev.text[:1].islower()
                    and not is_definition(ev.text)):
                pending[1].append(ev.text)
                continue
            opening = place_pending(*pending, ev)
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
                # also after a definition that holds a list. An open list, not
                # an open stycke, is what the block follows: a numbered
                # övergångsbestämmelse puts its list straight in the
                # Overgangsbestammelse ("2. Genom förordningen upphävs ‖
                # förordningen (1981:107) …,", 1983:412 övergångsbestämmelser
                # 1988:681)
                if (lists and ev.text[:1].islower()
                        and not is_definition(ev.text)):
                    pending = (lists[-1], [ev.text])
                elif is_label(ev.text):
                    label = ev.text
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
        place_pending(*pending, None)
    if label is not None:
        place_label(label, None)
    for lista in labelled:
        group_labels(lista)
    return doc


def group_labels(lista):
    """A list that opens with a group label, and whose count starts again
    after each later label, holds one group per label: "i Danmark ‖ a) … b) …
    ‖ i Finland ‖ a) … b) … c) …" (1983:163). Each label becomes an entry of
    a list without markers, with its items nested under it, so that every
    item gets an id of its own. A list whose count goes on after a label
    keeps the labels as text between its items."""
    groups = []
    for child in lista.children:
        if isinstance(child, Listtext):
            groups.append((child.text, []))
        else:
            groups[-1][1].append(child)
    if (len(groups) > 1 and all(items for _, items in groups)
            and len({items[0].ordinal for _, items in groups}) == 1):
        lista.children = [
            Listelement(ordinal=str(n), text=text,
                        children=[Lista(kind=lista.kind, children=items)])
            for n, (text, items) in enumerate(groups, 1)]
        lista.kind = "omarkerad"
