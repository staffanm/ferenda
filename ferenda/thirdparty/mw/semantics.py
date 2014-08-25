# Copyright 2013 semantics GmbH
# Written by Marcus Brinkmann <m.brinkmann@semantics.de>

from __future__ import print_function, division
from __future__ import absolute_import, unicode_literals

import itertools
from collections import OrderedDict
import re
from copy import deepcopy
from contextlib import contextmanager

from lxml import etree

import sys

from grako.exceptions import FailedSemantics
from grako.ast import AST

from . mw import mwParser as Parser
from . html import entity_by_name, attribute_whitelist, css_filter, escape_id
from . html import ITER_PUSH, ITER_POP, ITER_ADD, iter_structure
from . settings import Settings
from . semstate import SemanticsState

try:
    basestring
except:
    basestring = str

try:
    unicode
except:
    unicode = str

try:
    unichr(65)
except:
    unichr = chr


def tprint(*args, **kwargs):
    kwargs['file'] = sys.stderr
    print(*args, **kwargs)


def postprocess_references(root):
    class Anonymous():
        pass

    # Ordered dictionary with keys (group, name) and value a list of
    # ref elements.
    refs = OrderedDict()

    # refs inside references only provide referencable definitions for
    # that group, so exclude them here.
    active_refs = root.xpath(".//ref[not(ancestor::references)]")
    for ref in active_refs:
        group = ref.get("group")
        name = ref.get("name")
        if name is None:
            refs[(group, Anonymous())] = [ref]
        else:
            ref_list = refs.setdefault((group, name), [])
            ref_list.append(ref)

    # A dictionary, keyed by group and value is list of ref indexes
    # for each definition.
    ref_groups = {}

    for ref_index, ((group, name), ref_list) in enumerate(refs.items()):
        ref_group = ref_groups.setdefault(group, [])
        # This is the index of the reference definition in the by-group list.
        ref_group_index = len(ref_group)
        ref_group.append(ref_index)

        for sub_index, ref in enumerate(ref_list):
            fn = etree.Element("sup")
            fn.set("class", "reference")
            if len(ref_list) == 1:
                fn_id = "cite_ref-%d" % (ref_index + 1)
            else:
                fn_id = "cite_ref-%d-%d" % (ref_index + 1, sub_index + 1)
            fn.set("id", fn_id)
            fn_link = etree.SubElement(fn, "a")
            fn_link.set("href", "#cite_note-%d" % (ref_index + 1))
            if group is None:
                fn_link.text = "[%d]" % (ref_group_index + 1)
            else:
                fn_link.text = "[%s %d]" % (group, ref_group_index + 1)

            fn.tail = ref.tail
            ref.getparent().replace(ref, fn)

    refs_as_list = list(refs.values())

    # Now generate the reference definition lists.
    all_references = root.findall(".//references")
    for references in all_references:
        group = references.get("group")
        if group not in ref_groups:
            # Delete unused reference groups.
            # FIXME (but only if extra_refs is not empty):
            # Cite error: <ref> tag defined in <references> has group
            # attribute "$GROUPNAME" which does not appear in prior
            # text.
            references.getparent().remove(references)
            continue

        ref_group = ref_groups[group]

        # Collect extra definitions
        extra_defs = {}
        extra_refs = references.findall("ref")
        for ref in extra_refs:
            name = ref.get("name")
            if name is None:
                # FIXME: Cite error: <ref> tag defined in <references>
                # has no name attribute.
                continue
            # FIXME: Could check group attribute:
            # Cite error: <ref> tag in <references> has conflicting
            # group attribute "$GROUPNAME".
            ref_list = extra_defs.setdefault(name, [])
            ref_list.append(ref)

        # Now we can process all references and build the actual
        # reference list.
        refblock = etree.Element("ol")
        refblock.set("class", "references")

        for ref_group_index, ref_index in enumerate(ref_group):
            ref_list = refs_as_list[ref_index]
            li = etree.SubElement(refblock, "li")
            li.set("id", "cite_note-%d" % (ref_index + 1))
            backlinks = etree.SubElement(li, "span")
            backlinks.set("class", "mw-cite-backlink")
            if len(ref_list) == 1:
                link = etree.SubElement(backlinks, "a")
                link.set("href", "#cite_ref-%d" % (ref_index + 1))
                link.text = unichr(0x2191)  # &uarr;
                link.tail = " "
            else:
                backlinks.text = unichr(0x2191) + " "  # &uarr;
                for sub_index, ref in enumerate(ref_list):
                    sup = etree.SubElement(backlinks, "sup")
                    link = etree.SubElement(sup, "a")
                    link.set("href", "#cite_ref-%d-%d" % (ref_index + 1, sub_index + 1))
                    link.text = "%d.%d" % (ref_group_index + 1, sub_index)  # FIXME: Use a, b, c, ... ?
                    sup.tail = " "
            def_span = etree.SubElement(li, "span")
            def_span.set("class", "reference-text")

            # Now find the ref element with the actual definition.
            def_refs = ref_list
            name = ref_list[0].get("name")
            if name is not None:
                def_refs = def_refs + extra_defs.get(name, [])
            found = False
            for ref in def_refs:
                if ref.text is not None or len(ref) > 0:
                    def_span.text = ref.text
                    # Move ref children to def_span.
                    def_span.extend(ref)
                    found = True
                    break
            if not found:
                # FIXME: Cite Error...
                def_span.text = "N/A"

        # Commit.
        refblock.tail = references.tail
        references.getparent().replace(references, refblock)


# FIXME: No edit links in preview mode.
## preprocesor: Insert a heading marker only for <h> children of <root>
## This is to stop extractSections from going over multiple tree levels
def postprocess_toc(root, settings):
    structure = list(iter_structure(root))

    # Make identifiers unique.
    ids = {}
    for action, toc_nrs, el in structure:
        if action == ITER_ADD:
            span = el[0]
            ident = span.get("id")
            if ident in ids:
                nr = ids[ident] + 1
                ids[ident] = nr
                span.set("id", ident + "_" + str(nr))
            else:
                ids[ident] = 1

    # Get forcetoc flag.
    forcetoc = root.findall(".//forcetoc")
    if len(forcetoc) == 0:
        forcetoc = False
    else:
        for el in forcetoc:
            el.getparent().remove(el)
        forcetoc = True

    # Get notoc flag.
    notoc = root.findall(".//notoc")
    if len(notoc) == 0:
        notoc = False
    else:
        for el in notoc:
            el.getparent().remove(el)
        notoc = True

    # Get first toc element (if any) and remove all others.
    tocs = root.findall(".//toc")
    for toc in tocs[1:]:
        toc.getparent().remove(toc)
    if len(tocs) == 0:
        toc = None
    else:
        toc = tocs[0]

    if toc is None and notoc and not forcetoc:
        return

    # Generate the toc.
    toc_block = etree.Element("div")
    toc_block.set("id", "toc")
    toc_block.set("class", "toc")
    toctitle = etree.SubElement(toc_block, "div")
    toctitle.set("class", "toctitle")

    head = etree.SubElement(toctitle, "h2")
    head.text = settings.get_msg("toc")

    # Running count of sections (including skipped levels).
    ident_nr = 0
    # Current container for next ul (starts with div, is li later).
    cur_el = toc_block

    # max_toc_level assumes starting with h2.
    max_toc_level = settings.max_toc_level - 1

    for action, toc_nrs, h_el in structure:
        if action == ITER_PUSH:
            if len(toc_nrs) >= max_toc_level:
                continue
            ul = etree.SubElement(cur_el, "ul")
            ul.text = "\n"
            if cur_el.tag == "li":
                # Note that cur_el[0].tag == 'a'.
                cur_el[0].tail = "\n"
            cur_el = ul
        elif action == ITER_POP:
            if len(toc_nrs) >= max_toc_level:
                continue
            # At this point, cur_el.tag == "li".
            cur_el = cur_el.getparent().getparent()
        elif action == ITER_ADD:
            if len(toc_nrs) > max_toc_level:
                continue
            if toc_nrs[-1] == 1:
                ul = cur_el
            else:
                # At this point, cur_el.tag == "li".
                ul = cur_el.getparent()

            toclevel = len(toc_nrs)
            ident_nr = ident_nr + 1
            tocsection = ident_nr
            tocnumber = ".".join(map(str, toc_nrs))

            li = etree.SubElement(ul, "li")
            li.tail = "\n"
            li.set("class", "toclevel-" + str(toclevel)
                   + " tocsection-" + str(tocsection))

            #h_el.set("data-toclevel", str(toclevel))
            #h_el.set("data-tocsection", str(tocsection))
            #h_el.set("data-tocnumber", str(tocnumber))

            lnk = etree.SubElement(li, "a")
            ident = h_el.get("id", "")
            lnk.set("href", "#" + ident)

            span1 = etree.SubElement(lnk, "span")
            span1.set("class", "tocnumber")
            span1.text = tocnumber  # + "."
            span1.tail = " "

            span2 = etree.SubElement(lnk, "span")
            span2.set("class", "toctext")
            # Copy all formatting of the header.
            span2.text = h_el.text
            span2.extend(deepcopy(h_el))

            cur_el = li

    # FIXME: Could be configurable (it's hard-coded in MediaWiki)
    min_toc = 4

    if ident_nr == 0 or (toc is None and not forcetoc and ident_nr < min_toc):
        # Bail out.
        if toc is not None:
            toc.getparent().remove(toc)
        return

    # Locate the place for the TOC.
    first_h_el = None
    for action, toc_nrs, h_el in structure:
        if action == ITER_ADD:
            first_h_el = h_el
            break

    if toc is not None:
        toc_block.tail = toc.tail
        toc.getparent().replace(toc, toc_block)
    elif first_h_el is not None:
        first_h_parent = first_h_el.getparent()
        first_h_parent.insert(first_h_parent.index(first_h_el), toc_block)
    else:
        # Shouldn't happen.
        root.insert(0, toc_block)


class SemanticsTracer(object):
    """Wrap a semantics class and add extra trace output for debugging."""

    def __init__(self, semantics, trace=True):
        self.semantics = semantics
        self.trace = trace

    def __getattribute__(self, name):
        semantics = super(SemanticsTracer, self).__getattribute__("semantics")
        trace = super(SemanticsTracer, self).__getattribute__("trace")
        attr = None
        try:
            attr = getattr(semantics, name)
        except Exception as e:
            if trace:
                def newfunc(ast):
                    tprint('AST %s (not implemented)' % name)
                    tprint('= %s' % repr(ast))
                    return ast
                return newfunc
            raise e
        if not trace:
            return attr
        if not hasattr(attr, '__call__'):
            return attr

        def newfunc(ast):
            tprint('AST %s' % name)
            try:
                result = attr(ast)
            except Exception as e:
                tprint('- %s' % repr(ast))
                raise e
            if ast == result:
                tprint('= %s' % repr(ast))
            else:
                tprint('- %s' % repr(ast))
                tprint('+ %s' % repr(result))
            return result
        return newfunc


class mwSemantics(object):
    # Goal: Something like
    # http://www.mediawiki.org/wiki/Parsoid/MediaWiki_DOM_spec

    def __init__(self, context, settings=None, headings=None):
        self._context = context
        if settings is None:
            settings = Settings()
        self.settings = settings
        # Headings are accessed by end position.
        if headings is None:
            self.headings = None
        else:
            self.headings = dict([(h["end"], h) for h in headings])

    @contextmanager
    def _state(self):
        state = SemanticsState(self._context._state)
        yield state
        state = state.as_hashable()
        self._context._state = state

    def push_no_h6(self, ast):
        with self._state() as state:
            state.push_to("no", r"======([ \t]*(?:<!--((?!-->).|\n)*(-->|$)))*[ \t]*(\n|$)")  # use multiline?
        return ast

    def push_no_h5(self, ast):
        with self._state() as state:
            state.push_to("no", r"=====([ \t]*(?:<!--((?!-->).|\n)*(-->|$)))*[ \t]*(\n|$)")
        return ast

    def push_no_h4(self, ast):
        with self._state() as state:
            state.push_to("no", r"====([ \t]*(?:<!--((?!-->).|\n)*(-->|$)))*[ \t]*(\n|$)")
        return ast

    def push_no_h3(self, ast):
        with self._state() as state:
            state.push_to("no", r"===([ \t]*(?:<!--((?!-->).|\n)*(-->|$)))*[ \t]*(\n|$)")
        return ast

    def push_no_h2(self, ast):
        with self._state() as state:
            state.push_to("no", r"==([ \t]*(?:<!--((?!-->).|\n)*(-->|$)))*[ \t]*(\n|$)")
        return ast

    def push_no_h1(self, ast):
        with self._state() as state:
            state.push_to("no", r"=([ \t]*(?:<!--((?!-->).|\n)*(-->|$)))*[ \t]*(\n|$)")
        return ast

    # Inline newline handling.
    def push_no_nl(self, ast):
        with self._state() as state:
            state.push_to("no", r"\n")
        return ast

    def pop_no(self, ast):
        with self._state() as state:
            state.pop_from("no")
        return ast

    def check_no(self, ast):
        ctx = self._context
        with self._state() as state:
            no_list = state.get_list("no")
            if len(no_list) == 0:
                return
            # As the no list may use regex flags, we must check separately.
            for item in no_list:
                # FIXME: ctx._buffer vs ctx.buf (ModelContext)
                if ctx._buffer.matchre(item):
                    raise FailedSemantics("inline negative lookahead reject")
        return ast

    def pop_ifnot(self, ast):
        with self._state() as state:
            state.pop_from("ifnot")
        return ast

    def check_ifnot(self, ast):
        ctx = self._context
        with self._state() as state:
            ifnot_re = state.peek_at("ifnot")
            if ifnot_re is None:
                return
            # FIXME: ctx._buffer vs ctx.buf (ModelContext)
            if ctx._buffer.matchre(ifnot_re):
                raise FailedSemantics("inline ifnot negative lookahead reject")
        return ast

    def document(self, ast):
        html = etree.Element("html")
        body = etree.SubElement(html, "body")
        if isinstance(ast, list):
            pass
        else:
            body.extend(ast.blocks)

        # Post-processing.
        el_list = html.findall(".//*[mw-attr]")
        for el in el_list:
            attrs = el.findall("mw-attr")
            for attr in attrs:
                # FIXME: This might need some polishing.
                name = etree.tostring(attr.find("name"), encoding=unicode,
                                      method="text")
                value = etree.tostring(attr.find("value"), encoding=unicode,
                                       method="text")
                el.set(name, value)
                #tail = attr.tail
                #if tail is not None:
                #    attr.getparent().text = tail
                el.remove(attr)

        # Strip the level attribute from list items, which was only
        # used in constructing the lists.
        el_list = html.findall(".//li[@level]")
        el_list += html.findall(".//dd[@level]")
        el_list += html.findall(".//dt[@level]")
        for el in el_list:
            el.attrib.pop("level")

        postprocess_references(html)
        postprocess_toc(html, self.settings)

        return html

    def heading(self, ast):
        pos = self._context._buffer._pos
        headings = self.headings
        if headings is None:
            return ast
        heading = headings.get(pos, None)
        if heading is None:
            return ast

        # ast is a h element.
        ast = deepcopy(ast)
        span = etree.SubElement(ast, "span")
        span.set("class", "mw-editsection")

        bracket_left = etree.SubElement(span, "span")
        bracket_left.set("class", "mw-editsection-bracket")
        bracket_left.text = "["

        link = etree.SubElement(span, "a")
        title = heading["title"]
        if title is None:
            # FIXME
            title = "(none)"
        link.set("href", self.settings.make_url(title, action="edit", section=heading["section"]))
        link.set("title", "Edit section: " + ast[0].get("id"))
        link.text = "edit"

        bracket_right = etree.SubElement(span, "span")
        bracket_right.set("class", "mw-editsection-bracket")
        bracket_right.text = "]"

        return ast

    def heading_content(self, ast):
        # FIXME: Allow some inline elements.
        return "".join(ast)

    def _h_el(self, level, ast):
        el = etree.Element("h" + str(level))
        text = ast.strip()

        span = etree.SubElement(el, "span")
        span.text = text
        ident = etree.tostring(el, encoding=unicode, method="text")
        # FIXME: May need various canonical forms:
        # One as link target in toc, one as hint in edit link.
        span.set("class", "mw-headline")
        span.set("id", ident)

        el.tail = "\n"
        return el

    def h6(self, ast):
        return self._h_el(6, ast)

    def h5(self, ast):
        return self._h_el(5, ast)

    def h4(self, ast):
        return self._h_el(4, ast)

    def h3(self, ast):
        return self._h_el(3, ast)

    def h2(self, ast):
        return self._h_el(2, ast)

    def h1(self, ast):
        return self._h_el(1, ast)

    def html_heading(self, ast):
        level = int(ast.name[1:])
        el = self._h_el(level, ast.content)
        self._set_attributes(el, ast.attribs)
        return el

    def toc(self, ast):
        el = etree.Element("toc")
        return el

    def notoc(self, ast):
        el = etree.Element("notoc")
        return el

    def forcetoc(self, ast):
        el = etree.Element("forcetoc")
        return el

    def horizontal_rule_block(self, ast):
        el = etree.Element("hr")
        el.tail = "\n"
        return el

    def push_bol_skip_ul(self, ast):
        with self._state() as state:
            state.push_to("bol_skip", r"\*")
        return ast

    def push_bol_skip_ol(self, ast):
        with self._state() as state:
            state.push_to("bol_skip", r"#")
        return ast

    def push_bol_skip_dl(self, ast):
        with self._state() as state:
            state.push_to("bol_skip", r"[:;]")
        return ast

    def pop_bol_skip(self, ast):
        with self._state() as state:
            state.pop_from("bol_skip")
        return ast

    def check_bol_skip(self, ast):
        ctx = self._context
        with self._state() as state:
            bol_skip = state.get("bol_skip", None)
            if bol_skip is None:
                return
            bol_skip_re = "".join(bol_skip)
            if not ctx._buffer.matchre(bol_skip_re):
                raise FailedSemantics("begin of line skip reject")
        return ast

    def ul_block(self, ast):
        el = etree.Element("ul")
        el.tail = "\n"
        el.extend(ast.li)
        return el

    def ol_block(self, ast):
        el = etree.Element("ol")
        el.tail = "\n"
        el.extend(ast.li)
        return el

    def dl_block(self, ast):
        el = etree.Element("dl")
        el.tail = "\n"
        # Flatten list of potential lists (if first element is
        # inline_dd, grako doesn't flatten automatically).
        for li in ast.li:
            if isinstance(li, list):
                el.extend(li)
            else:
                el.append(li)
        #el.extend(ast.li)
        return el

    def dl_dd(self, ast):
        el = deepcopy(ast)
        el.tag = "dd"
        return el

    def list_li(self, ast):
        el = etree.Element("li")
        inline = ast.inline
        if inline is not None:
            inline = inline + ["\n"]
        self._collect_inline(el, inline)
        if "sublists" in ast:
            el.extend(ast.sublists)
        return el

    def list_dt(self, ast):
        el = etree.Element("dt")
        if "inline_dd" in ast and ast.inline_dd is not None:
            inline = ast.inline_dd
            self._collect_inline(el, inline.dt)
            dd = etree.Element("dd")
            content = inline.dd
            if content is not None:
                content = content + ["\n"]
            self._collect_inline(dd, content)
            # No sublist allowed in this case.  FIXME: Might need to
            # change to grako-list.
            return [el, dd]
        else:
            inline = ast.inline
            if inline is not None:
                inline = inline + ["\n"]
            self._collect_inline(el, inline)
        if "sublists" in ast:
            el.extend(ast.sublists)
        return el

    def push_ifnot_dt(self, ast):
        with self._state() as state:
            state.push_to("ifnot", r":")
        return ast

    def wspre_inline(self, ast):
        el = etree.Element("pre")
        content = ast.content
        # QUIRK: Add newline at the end, unless there is one already.
        if len(content) > 0:
            last_el = content[-1]
            if isinstance(last_el, etree._Element):
                if last_el.tail is None:
                    last_el.tail = "\n"
                else:
                    if len(last_el.tail) > 0 and last_el.tail[-1] != "\n":
                        last_el.tail = last_el.tail + "\n"
            else:
                if len(last_el) > 0 and last_el[-1] != "\n":
                    content[-1] = last_el + "\n"

        self._collect_inline(el, content)
        return el

    def push_bol_skip_wspre(self, ast):
        with self._state() as state:
            state.push_to("bol_skip", r" ")
        return ast

    def push_wspre_off(self, ast):
        with self._state() as state:
            state.push_to("wspre_off", True)
        return ast

    def pop_wspre(self, ast):
        with self._state() as state:
            state.pop_from("wspre_off")
        return ast

    def set_wspre_on(self, ast):
        with self._state() as state:
            stack = state.get("wspre_off")
            if stack is not None:
                stack[-1] = False
        return ast

    def check_wspre(self, ast):
        ctx = self._context
        with self._state() as state:
            wspre_off = state.peek_at("wspre_off")
            if wspre_off is True:
                raise FailedSemantics("wspre off")
        return ast

    def table_block(self, ast):
        rows = []
        if ast.rows:
            if ast.rows.first is not None:
                rows.append(ast.rows.first)
            rows.extend(ast.rows.rest)

        el = etree.Element("table")
        if ast.caption is not None:
            el.append(ast.caption)
        #tbody = etree.SubElement(el, "tbody")
        #tbody.extend(rows)
        el.extend(rows)
        self._set_attributes(el, ast.attribs)
        if ast.indent is not None:
            for _ in range(len(ast.indent)):
                dl_el = etree.Element("dl")
                dd_el = etree.SubElement(dl_el, "dd")
                dd_el.append(el)
                el = dl_el
        return el

    def table_caption(self, ast):
        el = etree.Element("caption")
        self._collect_blocks(el, ast.content.blocks)
        self._set_attributes(el, ast.attribs)
        return el

    def table_row_first(self, ast):
        el = etree.Element("tr")
        content = itertools.chain.from_iterable([c['cells'] for c in ast.content])
        el.extend(content)
        # The first (implicitely defined) row can't have any attributes.
        return el

    def table_row(self, ast):
        el = etree.Element("tr")
        content = itertools.chain.from_iterable([c['cells'] for c in ast.content])
        # FIXME: Maybe skip empty rows.
        el.extend(content)
        self._set_attributes(el, ast.attribs)
        return el

    def table_header(self, ast):
        el_list = ast.inline + [ast.final]
        # Work around an oddity of grako when dealing with lists of lists.
        return {'cells': el_list}

    def table_header_cell_inline(self, ast):
        el = etree.Element("th")
        self._trim_inline(ast.text)
        self._collect_inline(el, ast.text)
        self._set_attributes(el, ast.attribs)
        return el

    def _collect_blocks(self, el, blocks):
        if blocks is None:
            return
        if len(blocks) == 1 and blocks[0].tag == "p":
            p = blocks[0]
            el.text = p.text
            if len(p) > 0:
                last = p[-1]
#                if last.tail:
#                    last.tail = last.tail.rstrip()
                # Moves children of p to el.
                el.extend(p)
            else:
                el.text = el.text.rstrip()
        else:
            el.extend(blocks)

    def _table_cell(self, el, ast):
        if ast.content is not None:
            self._collect_blocks(el, ast.content.blocks)
        self._set_attributes(el, ast.attribs)
        return el

    def table_header_cell(self, ast):
        el = etree.Element("th")
        return self._table_cell(el, ast)

    def table_data(self, ast):
        if ast.final is not None:
            el_list = ast.inline + [ast.final]
        # Work around an oddity of grako when dealing with lists of lists.
        return {'cells': el_list}

    def table_data_cell_inline(self, ast):
        el = etree.Element("td")
        self._trim_inline(ast.text)
        self._collect_inline(el, ast.text)
        self._set_attributes(el, ast.attribs)
        return el

    def table_data_cell(self, ast):
        el = etree.Element("td")
        return self._table_cell(el, ast)

    def push_ifnot_table_data(self, ast):
        with self._state() as state:
            state.push_to("ifnot", r"\|\|")
        return ast

    def push_ifnot_table_header(self, ast):
        with self._state() as state:
            state.push_to("ifnot", r"!!|\|\|")
        return ast

    def push_no_tableline(self, ast):
        with self._state() as state:
            state.push_to("no", r"^[ \t]*(\||\!)")
        return ast

    def _collect_inline(self, el, ast):
        if ast is None:
            return
        # Add a list of inline elements to an Element.
        # FIXME: Currently must be called before _set_attributes.
        if len(el) > 0:
            last_el = el[-1]
        else:
            last_el = None
        for child in ast:
            if isinstance(child, basestring):
                text = child
                if last_el is None:
                    if el.text is None:
                        el.text = text
                    else:
                        el.text = el.text + text
                else:
                    if last_el.tail is None:
                        last_el.tail = text
                    else:
                        last_el.tail = last_el.tail + text
            else:
                el.append(child)
                last_el = child

    def _trim_inline(self, children):
        # Remove trailing whitespace from a list of inline elements.
        if children is None:
            return
        # FIXME: Is this sufficient to strip all trailing whitespace?
        while len(children) > 0:
            last_child = children[-1]
            if not isinstance(last_child, basestring):
                break
            last_child = last_child.rstrip()
            if last_child == "":
                children = children[:-1]
                # continue
            else:
                children[-1] = last_child
                break

    def _maybe_add_newline(self, content):
        if len(content) > 0:
            last_el = content[-1]
            if isinstance(last_el, etree._Element):
                if last_el.tail is None:
                    last_el.tail = "\n"
                else:
                    if len(last_el.tail) > 0 and last_el.tail[-1] != "\n":
                        last_el.tail = last_el.tail + "\n"
            else:
                if len(last_el) > 0 and last_el[-1] != "\n":
                    content[-1] = last_el + "\n"
        return content

    def paragraph(self, ast):
        el = etree.Element("p")
        content = []
        if ast.content is not None:
            content = ast.content
        # QUIRK: Add newline at the end, unless there is one already.
        self._maybe_add_newline(content)
        self._collect_inline(el, content)
        return el

    def paragraph_only_br(self, ast):
        el = etree.Element("br")
        el.tail = "\n"
        return AST({"content": [el]})

    def paragraph_br(self, ast):
        el = etree.Element("br")
        el.tail = "\n"
        return el

    def internal_link(self, ast):
        settings = self.settings
        el = etree.Element("a")
        try:
            # FIXME: target could be arbitrary complicated, need to
            # extract the text from the XML representation.
            # Maybe just move that stuff into an element, and process
            # it with XSL text() later.
            # THIS CAN FAIL AT RUNTIME IF NOT ALL CHILD ELEMENTS ARE
            # STRING(ABLE)!
            target = "".join(ast.target).strip()
        except:
            target = "BROKEN"
        name = settings.canonical_page_name(target)
        exists = settings.test_page_exists(name)
        title = settings.expand_page_name(name[0], name[1])
        cls = None
        if exists:
            url = settings.make_url(name)
        else:
            url = settings.make_url(name, action="edit", redlink="1")
            title = settings.expand_page_name(*name) + " (" + settings.get_msg("missing") + ")"
            cls = "new"

        # Order of attributes matters for tests.
        el.set("href", url)
        if cls is not None:
            el.set("class", cls)
        el.set("title", title)
        self._trim_inline(ast.text)
        if ast.text and len(ast.text) > 0:
            inline = ast.text
            if ast.suffix:
                inline = inline + [ast.suffix]
            self._collect_inline(el, inline)
        elif ast.suffix is not None:
            el.text = target + ast.suffix
        else:
            el.text = target
        return el

    def push_ifnot_intlink_target(self, ast):
        with self._state() as state:
            state.push_to("ifnot", r"\||\]\]")
        return ast

    def push_ifnot_intlink(self, ast):
        with self._state() as state:
            state.push_to("ifnot", r"\]\]")
        return ast

    def more_link_chars(self, ast):
        return "".join(ast)

    def link_chars(self, ast):
        return "".join(ast)

    def external_link(self, ast):
        el = etree.Element("a")
        target = ast.target.strip()
        self._trim_inline(ast.text)
        if ast.text and len(ast.text) > 0:
            self._collect_inline(el, ast.text)
            el.set("rel", "nofollow")
            el.set("class", "external text")
        else:
            # FIXME: autonumber
            el.text = target
        el.set("href", target)
        return el

    def push_ifnot_extlink(self, ast):
        with self._state() as state:
            state.push_to("ifnot", r"\]")
        return ast

    def plain_link(self, ast):
        el = etree.Element("a")
        el.set("rel", "nofollow")
        el.set("class", "external free")
        target = "".join(ast)
        el.set("href", target)
        el.text = target
        return el

    def italic(self, ast):
        el = etree.Element("i")
        self._collect_inline(el, ast.content)
        return el

    def bold(self, ast):
        el = etree.Element("b")
        self._collect_inline(el, ast.content)
        return el

    def bold_italic_both(self, ast):
        i_el = etree.Element("i")
        b_el = etree.SubElement(i_el, "b")
        self._collect_inline(b_el, ast.content)
        return i_el

    def italic_bold(self, ast):
        i_el = etree.Element("i")
        b_el = etree.SubElement(i_el, "b")
        self._collect_inline(b_el, ast.bold_content)
        self._collect_inline(i_el, ast.italic_content)
        return i_el

    def bold_italic(self, ast):
        b_el = etree.Element("b")
        i_el = etree.SubElement(b_el, "i")
        self._collect_inline(i_el, ast.italic_content)
        self._collect_inline(b_el, ast.bold_content)
        return b_el

    def comment(self, ast):
        return None

    def html_attribute_value(self, ast):
        return "".join(ast)

    def _set_attributes(self, el, attribs):
        if attribs is None:
            return
        if el.tag == "ref" or el.tag == "references":
            whitelist = frozenset(["name", "group"])
        else:
            whitelist = attribute_whitelist(el.tag)
        for attrib in attribs:
            name = attrib.name.lower()
            value = attrib.value
            if name.startswith("data-"):
                pass
            elif name not in whitelist:
                continue
            elif name == "role" and value != "presentation":
                continue

            if name == "style":
                value = css_filter(value)
            elif name == "id":
                value = escape_id(value)

            el.set(name, value)

    def html_inline(self, ast):
        el = etree.Element(ast.name)
        if ast.content is not None:
            self._collect_inline(el, ast.content)
        self._set_attributes(el, ast.attribs)
        return el

    def push_ifnot_html_tag(self, ast):
        with self._state() as state:
            state.push_to("ifnot", r"(?i)</" + ast.lower() + "[ \t\n]*>")
        return ast

    def html_named_entity(self, ast):
        name = ast.name.lower()
        entity = entity_by_name.get(name)
        if entity is None:
            return "&" + name + ";"
        else:
            return entity

    def html_numbered_entity(self, ast):
        if ast.hexnumber is not None:
            try:
                return unichr(int(ast.hexnumber, 16))
            except:
                return "&#x" + ast.hexnumber + ";"
        else:
            try:
                return unichr(int(ast.number))
            except:
                return "&#" + ast.number + ";"

    def nowiki(self, ast):
        # We know for sure that the only list elements are strings.
        if ast.content is None:
            return ""
        return "".join(ast.content)

    def pre_nowiki(self, ast):
        # We know for sure that the only list elements are strings.
        if ast.content is None:
            return ""
        return "".join(ast.content)

    def pre(self, ast):
        el = etree.Element("pre")
        if ast.content is not None:
            el.text = "".join(ast.content)
        self._set_attributes(el, ast.attribs)
        return el

    def ref(self, ast):
        el = etree.Element(ast.name.lower())
        if ast.content is not None:
            self._collect_blocks(el, ast.content.blocks)
        self._set_attributes(el, ast.attribs)
        return el

    def html_block(self, ast):
        el = etree.Element(ast.name.lower())
        if ast.content is not None:
            self._collect_blocks(el, ast.content.blocks)
        self._set_attributes(el, ast.attribs)
        return el

    def html_block_no_wspre(self, ast):
        el = etree.Element(ast.name.lower())
        if ast.content is not None:
            self._collect_blocks(el, ast.content.blocks)
        self._set_attributes(el, ast.attribs)
        return el

    def html_p(self, ast):
        el = etree.Element(ast.name.lower())
        self._collect_inline(el, ast.content)
        self._set_attributes(el, ast.attribs)
        return el

    def html_table(self, ast):
        el = etree.Element(ast.name.lower())
        if ast.content is not None:
            el.extend(ast.content)
        self._set_attributes(el, ast.attribs)
        return el

    def html_table_tr(self, ast):
        el = etree.Element(ast.name.lower())
        if ast.content is not None:
            el.extend(ast.content)
        self._set_attributes(el, ast.attribs)
        return el

    def html_table_cell(self, ast):
        el = etree.Element(ast.name.lower())
        if ast.content is not None:
            self._collect_blocks(el, ast.content.blocks)
        self._set_attributes(el, ast.attribs)
        return el

    def html_list(self, ast):
        el = etree.Element(ast.name.lower())
        if ast.content is not None:
            el.extend(ast.content)
        self._set_attributes(el, ast.attribs)
        return el

    def html_list_item(self, ast):
        el = etree.Element(ast.name.lower())
        if ast.content is not None:
            # FIXME: Unwrap inital p (see test "HTML nested bullet
            # list, closed tags (bug 5497)")
            # FIXME: Also do the unwrapping in other places, such as:
            # dl_item, table cells, ...
            self._collect_blocks(el, ast.content.blocks)
        self._set_attributes(el, ast.attribs)
        return el

    def html_dl(self, ast):
        el = etree.Element(ast.name.lower())
        if ast.content is not None:
            el.extend(ast.content)
        self._set_attributes(el, ast.attribs)
        return el

    def html_dl_item(self, ast):
        el = etree.Element(ast.name.lower())
        if ast.content is not None:
            self._collect_blocks(el, ast.content.blocks)
        self._set_attributes(el, ast.attribs)
        return el
