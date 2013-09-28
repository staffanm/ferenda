# -*- coding: utf-8 -*-
"""The purpose of this module is to provide classes corresponding to
most elements (except ``<style>``, ``<script>`` and similar
non-document content elements) and core attributes (except ``@style``
and the ``%events`` attributes) of HTML4.01 and HTML5. It is not
totally compliant with the HTML4.01 and HTML5 standards, but is enough
to model most real-world HTML. It contains no provisions to ensure
that elements of a particular kind only contain allowed
sub-elements.

"""
from __future__ import unicode_literals

import logging
import six

import bs4

from . import CompoundElement


def elements_from_soup(soup,
                       remove_tags=('script', 'style', 'font', 'map', 'center'),
                       keep_attributes=('class', 'id', 'dir', 'lang', 'src', 'href', 'name', 'alt')):
    """Converts a BeautifulSoup tree into a tree of
    :py:class:`ferenda.elements.html.HTMLElement` objects. Some
    non-semantic attributes and tags are removed in the process.

    :param soup: Soup object to convert
    :param remove_tags: Tags that should not be included
    :type  remove_tags: list
    :param keep_attributes: Attributes to keep
    :type  keep_attributes: list
    :returns: tree of element objects
    :rtype: ferenda.elements.html.HTMLElement

    """
    log = logging.getLogger(__name__)
    if soup.name in remove_tags:
        return None
    if soup.name not in _tagmap:
        # self.log.warning("Can't render %s" % soup.name)
        log.warning("Can't render %s" % soup.name)
        return None
    attrs = {}
    for attr in keep_attributes:
        if attr in soup.attrs:
            # print("   %s has attr %s" % (soup.name,attr))
            if isinstance(soup[attr], list):
                attrs[attr] = " ".join(soup[attr])
            else:
                attrs[attr] = soup[attr]
    # print("%s: %r" % (soup.name, attrs))

    element = _tagmap[soup.name](**attrs)

    #print("%sNode: %s" % ((depth-1)*". ",soup.name))
    for child in soup.children:
        if isinstance(child, bs4.element.Comment):
            #print("%sChild comment" % (depth*". "))
            pass
        elif isinstance(child, bs4.NavigableString):
            #print("%sChild string %r" % (depth*". ",child[:10]))
            if six.text_type(child).strip() != "":  # ignore pure whitespace between tags
                element.append(six.text_type(child))  # convert NavigableString to pure str
        else:
            #print("%sChild %s" % (depth*". ",soup.name))
            subelement = elements_from_soup(child, remove_tags, keep_attributes)
            if subelement != None:
                element.append(subelement)
    return element

# abstract class


class HTMLElement(CompoundElement):

    """Abstract base class for all elements."""


class HTML(HTMLElement):

    """Element corresponding to the ``<html>`` tag"""


class Head(HTMLElement):

    """Element corresponding to the ``<head>`` tag"""
# a title cannot contain subelements -- derive from UnicodeElement instead?


class Title(HTMLElement):

    """Element corresponding to the ``<title>`` tag"""


class Body(HTMLElement):

    """Element corresponding to the ``<body>`` tag"""

    def as_xhtml(self, uri):
        element = super(Body, self).as_xhtml(uri)
        element.set('about', uri)
        return element

# %block


class P(HTMLElement):

    """Element corresponding to the ``<p>`` tag"""

# %heading


class H1(HTMLElement):

    """Element corresponding to the ``<h1>`` tag"""


class H2(HTMLElement):

    """Element corresponding to the ``<h2>`` tag"""


class H3(HTMLElement):

    """Element corresponding to the ``<h3>`` tag"""


class H4(HTMLElement):

    """Element corresponding to the ``<h4>`` tag"""


class H5(HTMLElement):

    """Element corresponding to the ``<h5>`` tag"""


class H6(HTMLElement):

    """Element corresponding to the ``<h6>`` tag"""

# %list


class UL(HTMLElement):

    """Element corresponding to the ``<ul>`` tag"""


class OL(HTMLElement):

    """Element corresponding to the ``<ol>`` tag"""


class LI(HTMLElement):

    """Element corresponding to the ``<li>`` tag"""

# %preformatted


class Pre(HTMLElement):

    """Element corresponding to the ``<pre>`` tag"""

# other


class DL(HTMLElement):

    """Element corresponding to the ``<dl>`` tag"""


class DT(HTMLElement):

    """Element corresponding to the ``<dt>`` tag"""


class DD(HTMLElement):

    """Element corresponding to the ``<dd>`` tag"""


class Div(HTMLElement):

    """Element corresponding to the ``<div>`` tag"""


class Blockquote(HTMLElement):

    """Element corresponding to the ``<blockquote>`` tag"""


class Form(HTMLElement):

    """Element corresponding to the ``<form>`` tag"""


class HR(HTMLElement):

    """Element corresponding to the ``<hr>`` tag"""


class Table(HTMLElement):

    """Element corresponding to the ``<table>`` tag"""


class Fieldset(HTMLElement):

    """Element corresponding to the ``<fieldset>`` tag"""


class Address(HTMLElement):

    """Element corresponding to the ``<address>`` tag"""

# %fontstyle


class TT (HTMLElement):

    """Element corresponding to the ``<tt >`` tag"""


class I (HTMLElement):

    """Element corresponding to the ``<i >`` tag"""


class B (HTMLElement):

    """Element corresponding to the ``<b >`` tag"""


class U (HTMLElement):

    """Element corresponding to the ``<u >`` tag"""


class Big (HTMLElement):

    """Element corresponding to the ``<big >`` tag"""


class Small(HTMLElement):

    """Element corresponding to the ``<small>`` tag"""

# %phrase


class Em (HTMLElement):

    """Element corresponding to the ``<em >`` tag"""


class Strong (HTMLElement):

    """Element corresponding to the ``<strong >`` tag"""


class Dfn (HTMLElement):

    """Element corresponding to the ``<dfn >`` tag"""


class Code (HTMLElement):

    """Element corresponding to the ``<code >`` tag"""


class Samp (HTMLElement):

    """Element corresponding to the ``<samp >`` tag"""


class Kbd (HTMLElement):

    """Element corresponding to the ``<kbd >`` tag"""


class Var (HTMLElement):

    """Element corresponding to the ``<var >`` tag"""


class Cite (HTMLElement):

    """Element corresponding to the ``<cite >`` tag"""


class Abbr (HTMLElement):

    """Element corresponding to the ``<abbr >`` tag"""


class Acronym(HTMLElement):

    """Element corresponding to the ``<acronym>`` tag"""

# %special


class A (HTMLElement):

    """Element corresponding to the ``<a >`` tag"""


class Img (HTMLElement):

    """Element corresponding to the ``<img >`` tag"""


class Object (HTMLElement):

    """Element corresponding to the ``<object >`` tag"""


class Br (HTMLElement):

    """Element corresponding to the ``<br >`` tag"""


class Q (HTMLElement):

    """Element corresponding to the ``<q >`` tag"""


class Sub (HTMLElement):

    """Element corresponding to the ``<sub >`` tag"""


class Sup (HTMLElement):

    """Element corresponding to the ``<sup >`` tag"""


class Span (HTMLElement):

    """Element corresponding to the ``<span >`` tag"""


class BDO(HTMLElement):

    """Element corresponding to the ``<bdo>`` tag"""

# %form


class Input(HTMLElement):

    """Element corresponding to the ``<input>`` tag"""


class Select(HTMLElement):

    """Element corresponding to the ``<select>`` tag"""


class Textarea(HTMLElement):

    """Element corresponding to the ``<textarea>`` tag"""


class Label(HTMLElement):

    """Element corresponding to the ``<label>`` tag"""


class Button(HTMLElement):

    """Element corresponding to the ``<button>`` tag"""

# table


class Caption(HTMLElement):

    """Element corresponding to the ``<caption>`` tag"""


class Thead(HTMLElement):

    """Element corresponding to the ``<thead>`` tag"""


class Tfoot(HTMLElement):

    """Element corresponding to the ``<tfoot>`` tag"""


class Tbody(HTMLElement):

    """Element corresponding to the ``<tbody>`` tag"""


class Colgroup(HTMLElement):

    """Element corresponding to the ``<colgroup>`` tag"""


class Col(HTMLElement):

    """Element corresponding to the ``<col>`` tag"""


class TR(HTMLElement):

    """Element corresponding to the ``<tr>`` tag"""


class TH(HTMLElement):

    """Element corresponding to the ``<th>`` tag"""


class TD(HTMLElement):

    """Element corresponding to the ``<td>`` tag"""

# very special?


class Ins(HTMLElement):

    """Element corresponding to the ``<ins>`` tag"""


class Del(HTMLElement):

    """Element corresponding to the ``<del>`` tag"""

# new elements in HTML5 -- cannot be simply expressed in XHTML
# 1.1. Instead they're expressed as eg. '<div class="section">'


class HTML5Element(HTMLElement):
    tagname = "div"

    def _get_classname(self):
        return self.__class__.__name__.lower()
    classname = property(_get_classname)


class Article(HTML5Element):

    """Element corresponding to the ``<article>`` tag"""


class Aside(HTML5Element):

    """Element corresponding to the ``<aside>`` tag"""


class Bdi(HTML5Element):

    """Element corresponding to the ``<bdi>`` tag"""


class Details(HTML5Element):

    """Element corresponding to the ``<details>`` tag"""


class Dialog(HTML5Element):

    """Element corresponding to the ``<dialog>`` tag"""


class Summary(HTML5Element):

    """Element corresponding to the ``<summary>`` tag"""


class Figure(HTML5Element):

    """Element corresponding to the ``<figure>`` tag"""


class Figcaption(HTML5Element):

    """Element corresponding to the ``<figcaption>`` tag"""


class Footer(HTML5Element):

    """Element corresponding to the ``<footer>`` tag"""


class Header(HTML5Element):

    """Element corresponding to the ``<header>`` tag"""


class Hgroup(HTML5Element):

    """Element corresponding to the ``<hgroup>`` tag"""


class Mark(HTML5Element):

    """Element corresponding to the ``<mark>`` tag"""


class Meter(HTML5Element):

    """Element corresponding to the ``<meter>`` tag"""


class Nav(HTML5Element):

    """Element corresponding to the ``<nav>`` tag"""


class Progress(HTML5Element):

    """Element corresponding to the ``<progress>`` tag"""


class Ruby(HTML5Element):

    """Element corresponding to the ``<ruby>`` tag"""


class Rt(HTML5Element):

    """Element corresponding to the ``<rt>`` tag"""


class Rp(HTML5Element):

    """Element corresponding to the ``<rp>`` tag"""


class Section(HTML5Element):

    """Element corresponding to the ``<section>`` tag"""


class Time(HTML5Element):

    """Element corresponding to the ``<time>`` tag"""


class Wbr(HTML5Element):

    """Element corresponding to the ``<wbr>`` tag"""
# audio, video, embed, canvas and similar non structural/semantic
# elements not included

# For use by elements_from_soup. FIXME: we should be able to build
# _tagmap dynamically.
_tagmap = {'html': HTML,
           'head': Head,
           'title': Title,
           'body': Body,
           'p': P,
           'h1': H1,
           'h2': H2,
           'h3': H3,
           'h4': H4,
           'h5': H5,
           'h6': H6,
           'ul': UL,
           'ol': OL,
           'li': LI,
           'pre': Pre,
           'dl': DL,
           'dt': DT,
           'dd': DD,
           'div': Div,
           'blockquote': Blockquote,
           'form': Form,
           'hr': HR,
           'table': Table,
           'fieldset': Fieldset,
           'address': Address,
           'tt': TT,
           'i': I,
           'b': B,
           'u': U,
           'big': Big,
           'small': Small,
           'em': Em,
           'strong': Strong,
           'dfn': Dfn,
           'code': Code,
           'samp': Samp,
           'kbd': Kbd,
           'var': Var,
           'cite': Cite,
           'abbr': Abbr,
           'acronym': Acronym,
           'a': A,
           'img': Img,
           'object': Object,
           'br': Br,
           'q': Q,
           'sub': Sub,
           'sup': Sup,
           'span': Span,
           'bdo': BDO,
           'input': Input,
           'select': Select,
           'textarea': Textarea,
           'label': Label,
           'button': Button,
           'caption': Caption,
           'thead': Thead,
           'tfoot': Tfoot,
           'tbody': Tbody,
           'colgroup': Colgroup,
           'col': Col,
           'tr': TR,
           'th': TH,
           'td': TD,
           'ins': Ins,
           'del': Del,
           'article': Article,
           'aside': Aside,
           'bdi': Bdi,
           'details': Details,
           'dialog': Dialog,
           'summary': Summary,
           'figure': Figure,
           'figcaption': Figcaption,
           'footer': Footer,
           'header': Header,
           'hgroup': Hgroup,
           'mark': Mark,
           'meter': Meter,
           'nav': Nav,
           'progress': Progress,
           'ruby': Ruby,
           'rt': Rt,
           'rp': Rp,
           'section': Section,
           'time': Time,
           'wbr': Wbr
           }
