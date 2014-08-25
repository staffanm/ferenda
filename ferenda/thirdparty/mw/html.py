# Copyright 2013 semantics GmbH
# Written by Marcus Brinkmann <m.brinkmann@semantics.de>

from __future__ import print_function, absolute_import, division

import re
from bisect import bisect_left
from lxml import etree
try:
    lxml_no_iter_list = False
    list(etree.ElementDepthFirstIterator(etree.Element("foo"), ["foo"]))
except TypeError:
    lxml_no_iter_list = True


try:
    unichr(65)
except:
    unichr = chr

# http://en.wikipedia.org/wiki/List_of_XML_and_HTML_character_entity_references
# (from the HTML 4 DTD)
entity_by_name = {
    "quot": unichr(0x0022),
    "amp": unichr(0x0026),
    "apos": unichr(0x0027),
    "lt": unichr(0x003C),
    "gt": unichr(0x003E),
    "nbsp": unichr(0x00A0),
    "iexcl": unichr(0x00A1),
    "cent": unichr(0x00A2),
    "pound": unichr(0x00A3),
    "curren": unichr(0x00A4),
    "yen": unichr(0x00A5),
    "brvbar": unichr(0x00A6),
    "sect": unichr(0x00A7),
    "uml": unichr(0x00A8),
    "copy": unichr(0x00A9),
    "ordf": unichr(0x00AA),
    "laquo": unichr(0x00AB),
    "not": unichr(0x00AC),
    "shy": unichr(0x00AD),
    "reg": unichr(0x00AE),
    "macr": unichr(0x00AF),
    "deg": unichr(0x00B0),
    "plusmn": unichr(0x00B1),
    "sup2": unichr(0x00B2),
    "sup3": unichr(0x00B3),
    "acute": unichr(0x00B4),
    "micro": unichr(0x00B5),
    "para": unichr(0x00B6),
    "middot": unichr(0x00B7),
    "cedil": unichr(0x00B8),
    "sup1": unichr(0x00B9),
    "ordm": unichr(0x00BA),
    "raquo": unichr(0x00BB),
    "frac14": unichr(0x00BC),
    "frac12": unichr(0x00BD),
    "frac34": unichr(0x00BE),
    "iquest": unichr(0x00BF),
    "Agrave": unichr(0x00C0),
    "Aacute": unichr(0x00C1),
    "Acirc": unichr(0x00C2),
    "Atilde": unichr(0x00C3),
    "Auml": unichr(0x00C4),
    "Aring": unichr(0x00C5),
    "AElig": unichr(0x00C6),
    "Ccedil": unichr(0x00C7),
    "Egrave": unichr(0x00C8),
    "Eacute": unichr(0x00C9),
    "Ecirc": unichr(0x00CA),
    "Euml": unichr(0x00CB),
    "Igrave": unichr(0x00CC),
    "Iacute": unichr(0x00CD),
    "Icirc": unichr(0x00CE),
    "Iuml": unichr(0x00CF),
    "ETH": unichr(0x00D0),
    "Ntilde": unichr(0x00D1),
    "Ograve": unichr(0x00D2),
    "Oacute": unichr(0x00D3),
    "Ocirc": unichr(0x00D4),
    "Otilde": unichr(0x00D5),
    "Ouml": unichr(0x00D6),
    "times": unichr(0x00D7),
    "Oslash": unichr(0x00D8),
    "Ugrave": unichr(0x00D9),
    "Uacute": unichr(0x00DA),
    "Ucirc": unichr(0x00DB),
    "Uuml": unichr(0x00DC),
    "Yacute": unichr(0x00DD),
    "THORN": unichr(0x00DE),
    "szlig": unichr(0x00DF),
    "agrave": unichr(0x00E0),
    "aacute": unichr(0x00E1),
    "acirc": unichr(0x00E2),
    "atilde": unichr(0x00E3),
    "auml": unichr(0x00E4),
    "aring": unichr(0x00E5),
    "aelig": unichr(0x00E6),
    "ccedil": unichr(0x00E7),
    "egrave": unichr(0x00E8),
    "eacute": unichr(0x00E9),
    "ecirc": unichr(0x00EA),
    "euml": unichr(0x00EB),
    "igrave": unichr(0x00EC),
    "iacute": unichr(0x00ED),
    "icirc": unichr(0x00EE),
    "iuml": unichr(0x00EF),
    "eth": unichr(0x00F0),
    "ntilde": unichr(0x00F1),
    "ograve": unichr(0x00F2),
    "oacute": unichr(0x00F3),
    "ocirc": unichr(0x00F4),
    "otilde": unichr(0x00F5),
    "ouml": unichr(0x00F6),
    "divide": unichr(0x00F7),
    "oslash": unichr(0x00F8),
    "ugrave": unichr(0x00F9),
    "uacute": unichr(0x00FA),
    "ucirc": unichr(0x00FB),
    "uuml": unichr(0x00FC),
    "yacute": unichr(0x00FD),
    "thorn": unichr(0x00FE),
    "yuml": unichr(0x00FF),
    "OElig": unichr(0x0152),
    "oelig": unichr(0x0153),
    "Scaron": unichr(0x0160),
    "scaron": unichr(0x0161),
    "Yuml": unichr(0x0178),
    "fnof": unichr(0x0192),
    "circ": unichr(0x02C6),
    "tilde": unichr(0x02DC),
    "Alpha": unichr(0x0391),
    "Beta": unichr(0x0392),
    "Gamma": unichr(0x0393),
    "Delta": unichr(0x0394),
    "Epsilon": unichr(0x0395),
    "Zeta": unichr(0x0396),
    "Eta": unichr(0x0397),
    "Theta": unichr(0x0398),
    "Iota": unichr(0x0399),
    "Kappa": unichr(0x039A),
    "Lambda": unichr(0x039B),
    "Mu": unichr(0x039C),
    "Nu": unichr(0x039D),
    "Xi": unichr(0x039E),
    "Omicron": unichr(0x039F),
    "Pi": unichr(0x03A0),
    "Rho": unichr(0x03A1),
    "Sigma": unichr(0x03A3),
    "Tau": unichr(0x03A4),
    "Upsilon": unichr(0x03A5),
    "Phi": unichr(0x03A6),
    "Chi": unichr(0x03A7),
    "Psi": unichr(0x03A8),
    "Omega": unichr(0x03A9),
    "alpha": unichr(0x03B1),
    "beta": unichr(0x03B2),
    "gamma": unichr(0x03B3),
    "delta": unichr(0x03B4),
    "epsilon": unichr(0x03B5),
    "zeta": unichr(0x03B6),
    "eta": unichr(0x03B7),
    "theta": unichr(0x03B8),
    "iota": unichr(0x03B9),
    "kappa": unichr(0x03BA),
    "lambda": unichr(0x03BB),
    "mu": unichr(0x03BC),
    "nu": unichr(0x03BD),
    "xi": unichr(0x03BE),
    "omicron": unichr(0x03BF),
    "pi": unichr(0x03C0),
    "rho": unichr(0x03C1),
    "sigmaf": unichr(0x03C2),
    "sigma": unichr(0x03C3),
    "tau": unichr(0x03C4),
    "upsilon": unichr(0x03C5),
    "phi": unichr(0x03C6),
    "chi": unichr(0x03C7),
    "psi": unichr(0x03C8),
    "omega": unichr(0x03C9),
    "thetasym": unichr(0x03D1),
    "upsih": unichr(0x03D2),
    "piv": unichr(0x03D6),
    "ensp": unichr(0x2002),
    "emsp": unichr(0x2003),
    "thinsp": unichr(0x2009),
    "zwnj": unichr(0x200C),
    "zwj": unichr(0x200D),
    "lrm": unichr(0x200E),
    "rlm": unichr(0x200F),
    "ndash": unichr(0x2013),
    "mdash": unichr(0x2014),
    "lsquo": unichr(0x2018),
    "rsquo": unichr(0x2019),
    "sbquo": unichr(0x201A),
    "ldquo": unichr(0x201C),
    "rdquo": unichr(0x201D),
    "bdquo": unichr(0x201E),
    "dagger": unichr(0x2020),
    "Dagger": unichr(0x2021),
    "bull": unichr(0x2022),
    "hellip": unichr(0x2026),
    "permil": unichr(0x2030),
    "prime": unichr(0x2032),
    "Prime": unichr(0x2033),
    "lsaquo": unichr(0x2039),
    "rsaquo": unichr(0x203A),
    "oline": unichr(0x203E),
    "frasl": unichr(0x2044),
    "euro": unichr(0x20AC),
    "image": unichr(0x2111),
    "weierp": unichr(0x2118),
    "real": unichr(0x211C),
    "trade": unichr(0x2122),
    "alefsym": unichr(0x2135),
    "larr": unichr(0x2190),
    "uarr": unichr(0x2191),
    "rarr": unichr(0x2192),
    "darr": unichr(0x2193),
    "harr": unichr(0x2194),
    "crarr": unichr(0x21B5),
    "lArr": unichr(0x21D0),
    "uArr": unichr(0x21D1),
    "rArr": unichr(0x21D2),
    "dArr": unichr(0x21D3),
    "hArr": unichr(0x21D4),
    "forall": unichr(0x2200),
    "part": unichr(0x2202),
    "exist": unichr(0x2203),
    "empty": unichr(0x2205),
    "nabla": unichr(0x2207),
    "isin": unichr(0x2208),
    "notin": unichr(0x2209),
    "ni": unichr(0x220B),
    "prod": unichr(0x220F),
    "sum": unichr(0x2211),
    "minus": unichr(0x2212),
    "lowast": unichr(0x2217),
    "radic": unichr(0x221A),
    "prop": unichr(0x221D),
    "infin": unichr(0x221E),
    "ang": unichr(0x2220),
    "and": unichr(0x2227),
    "or": unichr(0x2228),
    "cap": unichr(0x2229),
    "cup": unichr(0x222A),
    "int": unichr(0x222B),
    "there4": unichr(0x2234),
    "sim": unichr(0x223C),
    "cong": unichr(0x2245),
    "asymp": unichr(0x2248),
    "ne": unichr(0x2260),
    "equiv": unichr(0x2261),
    "le": unichr(0x2264),
    "ge": unichr(0x2265),
    "sub": unichr(0x2282),
    "sup": unichr(0x2283),
    "nsub": unichr(0x2284),
    "sube": unichr(0x2286),
    "supe": unichr(0x2287),
    "oplus": unichr(0x2295),
    "otimes": unichr(0x2297),
    "perp": unichr(0x22A5),
    "sdot": unichr(0x22C5),
    "vellip": unichr(0x22EE),
    "lceil": unichr(0x2308),
    "rceil": unichr(0x2309),
    "lfloor": unichr(0x230A),
    "rfloor": unichr(0x230B),
    "lang": unichr(0x2329),
    "rang": unichr(0x232A),
    "loz": unichr(0x25CA),
    "spades": unichr(0x2660),
    "clubs": unichr(0x2663),
    "hearts": unichr(0x2665),
    "diams": unichr(0x2666)
}


_attr_common = frozenset([
    # HTML
    "id", "class", "style", "lang", "dir", "title",
    # WAI-ARIA
    "role"
])

_attr_rdfa = [
    # Section 9, http://www.w3.org/TR/2008/REC-rdfa-syntax-20081014
    "about", "property", "resource", "datatype", "typeof"
]

_attr_microdata = [
    # http://www.whatwg.org/specs/web-apps/current-work/multipage/microdata.html#the-microdata-model
    "itemid", "itemprop", "itemref", "itemscope", "itemtype"
]

_attr_block = _attr_common.union(["align"])

_attr_tablealign = ["align", "char", "charoff", "valign"]

_attr_tablecell = [
    "abbr", "axis", "headers", "scope", "rowspan", "colspan",
    # deprecated
    "nowrap", "width", "height", "bgcolor"]

# Whitelist lifted from MediaWiki's sanatizer.
_attribute_whitelist = {
    "div": _attr_block,
    "center": _attr_common,
    "span": _attr_block,
    "h1": _attr_block,
    "h2": _attr_block,
    "h3": _attr_block,
    "h4": _attr_block,
    "h5": _attr_block,
    "h6": _attr_block,
    "em": _attr_common,
    "strong": _attr_common,
    "cite": _attr_common,
    "dfn": _attr_common,
    "code": _attr_common,
    "samp": _attr_common,
    "kbd": _attr_common,
    "var": _attr_common,
    "abbr": _attr_common,
    "blockquote": _attr_common.union(["cite"]),
    "sub": _attr_common,
    "sup": _attr_common,
    "p": _attr_block,
    "br": frozenset(["id", "class", "title", "style", "clear"]),
    "pre": _attr_common.union(["width"]),
    "ins": _attr_common.union(["cite", "datetime"]),
    "del": _attr_common.union(["cite", "datetime"]),
    "ul": _attr_common.union(["type"]),
    "ol": _attr_common.union(["type", "start"]),
    "li": _attr_common.union(["type", "value"]),
    "dl": _attr_common,
    "dd": _attr_common,
    "dt": _attr_common,
    "table": _attr_common.union(["summary", "width", "border", "frame",
                                 "rules", "cellspacing", "cellpadding",
                                 "align", "bgcolor"]),
    "caption": _attr_common.union(["align"]),
    "thead": _attr_common.union(_attr_tablealign),
    "tfoot": _attr_common.union(_attr_tablealign),
    "tbody": _attr_common.union(_attr_tablealign),
    "colgroup": _attr_common.union(["span", "width"], _attr_tablealign),
    "col": _attr_common.union(["span", "width"], _attr_tablealign),
    "tr": _attr_common.union(["bgcolor"], _attr_tablealign),
    "td": _attr_common.union(_attr_tablecell, _attr_tablealign),
    "th": _attr_common.union(_attr_tablecell, _attr_tablealign),
    "a": _attr_common.union(["href", "rel", "rev"]),  # rel/rev esp. for RDFa
    "img": _attr_common.union(["alt", "src", "width", "height"]),
    "tt": _attr_common,
    "b": _attr_common,
    "i": _attr_common,
    "big": _attr_common,
    "small": _attr_common,
    "strike": _attr_common,
    "s": _attr_common,
    "u": _attr_common,
    "font": _attr_common.union(["size", "color", "face"]),
    "hr": _attr_common.union(["noshade", "size", "width"]),
    "bdi": _attr_common
}


def attribute_whitelist(el_name, rdfa=False, microdata=False):
    result = _attribute_whitelist.get(el_name, frozenset([]))
    if rdfa is True:
        result = result.union(_attr_rdfa)
    if microdata is True:
        result = result.union(_attr_microdata)
    return result


def css_filter(style):
    # FIXME: This should be based on whitelisting instead.
    decode_re = re.compile(r"\\(?:(\r\n|\n|\r|\f)|([0-9a-fA-F]{1,6}[ \t\n\r\f]?)|(.)|$)")

    def decode_cb(match):
        match = match.groups()
        if match[0]:
            return ""
        elif match[1]:
            try:
                char = unichr(int(match[1], 16))
            except:
                # invalid codepoint
                char = u"\ufffd"
        elif match[2]:
            char = match[2]
        else:
            # Backslash at end of string.
            char = "\\"
        if char in frozenset(["\n", '"', "'", "\\"]):
            # If these occur in strings, they must be escaped.
            return r"\{nr:x} ".format(nr=char)
        return char

    style, _ = decode_re.subn(decode_cb, style)

    comments_re = re.compile(r"/\*.*?\*/")
    style, _ = comments_re.subn(" ", style)

    unclosed_comment_re = re.compile(r"/\*.*$")
    style = unclosed_comment_re.sub(" ", style)

    invalid_control_re = re.compile(r"[\x00-\x08\x0e-\x1f\x7f]")
    if invalid_control_re.search(style):
        return '/* invalid control char */'

    # FIXME: Better use whitelist.
    insecure_input_re = re.compile(r"expression|filter\s*:|accelerator\s*:|url\s*\(|image\s*\(|image-set\s*\(")
    if insecure_input_re.search(style):
        return '/* insecure input */'

    return style


def escape_id(id):
    return id


def iter_from_list(root, tags):
    if lxml_no_iter_list is False:
        return root.iter(tags)

    def iter_():
        for el in root.iter():
            if not tags or el.tag in tags:
                yield el
    return iter_()


ITER_PUSH = 0
ITER_POP = 1
ITER_ADD = 2


def iter_structure(root):
    # Iterate over the headings, returning also the structure.
    headings = iter_from_list(root, ["h1", "h2", "h3", "h4", "h5", "h6"])
    headings = list(headings)

    # A stack of toc numbers for the previous element and its ancestors.
    toc_nrs = []
    # A stack of levels for the previous element and its ancestors.
    levels = []

    for h_el in headings:
        level = int(h_el.tag[1])
        # Find the appropriate insertion point.  If levels are
        # skipped, all intermediate levels are treated as if they were
        # at that level.
        pos = bisect_left(levels, level)
        pop_levels = levels[pos:]
        push_level = (len(pop_levels) == 0)

        # Refresh levels and toc_nrs.
        levels = levels[:pos] + [level]
        if push_level:
            # FIXME: Could store last h_el and pass it here.
            yield (ITER_PUSH, toc_nrs[:], None)
            toc_nrs.append(1)
        else:
            # We pop one less than pop_levels, as there is no pop for
            # leave nodes.
            for idx in range(1, len(pop_levels)):
                yield (ITER_POP, toc_nrs[:-idx], None)
            toc_nrs = toc_nrs[:pos] + [toc_nrs[pos] + 1]

        # We copy the toc_nrs, so the caller can convert the generator
        # output to a list.
        yield (ITER_ADD, toc_nrs[:], h_el)
    
    for idx in range(1, len(toc_nrs) + 1):
        yield (ITER_POP,  toc_nrs[:-idx], None)
