#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""This module contains classes that are based on native types (lists,
dicts, string, datetime), but adds support for general attributes. The
attributes are set when the object is created (as keyword arguments to
the construct). Once an object has been instansiated, new attributes
cannot be added, but existing attributes can be changed.

The main purpose of using these classes is that they can be readily
converted to XHTML by the
:py:meth:`ferenda.DocumentRepository.render_xhtml` method.

The module also contains the convenience functions
:py:func:`serialize` and :py:func:`deserialize`, to convert object
hierarchies to and from strings.

"""
from __future__ import unicode_literals

import datetime
import re
import sys
import logging
import ast
import xml.etree.cElementTree as ET

from lxml.builder import ElementMaker
from operator import itemgetter

import six
from six import text_type as str
from six import binary_type as bytes
from rdflib import Graph, Namespace, Literal, URIRef
import pyparsing

from ferenda import util

DCT = Namespace(util.ns['dct'])
RDF = Namespace(util.ns['rdf'])
XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"
log = logging.getLogger(__name__)
E = ElementMaker(namespace="http://www.w3.org/1999/xhtml",
                 nsmap={None: "http://www.w3.org/1999/xhtml"})

def serialize(root):
    """Given any :py:class:`~ferenda.elements.AbstractElement` *root*
    object, returns a XML serialization of *root*, recursively.

    """
    t = __serializeNode(root)
    _indentTree(t)
    return ET.tostring(t, 'utf-8').decode('utf-8') + "\n"


def deserialize(xmlstr, caller_globals):
    """Given a XML string created by :py:func:`serialize`, returns a
    object tree of :py:class:`AbstractElement` derived objects that is
    identical to the initial object structure.

    .. note::

       This function is highly insecure -- use only with trusted data

    """
    # print "Caller globals()"
    # print repr(caller_globals.keys())
    # print "Callee globals()"
    # print repr(globals().keys())
    # print repr(locals().keys())
    if (isinstance(xmlstr, str)):
        xmlstr = xmlstr.encode('utf-8')
    t = ET.fromstring(xmlstr)
    return  __deserializeNode(t, caller_globals)


class AbstractElement(object):
    """Base class for all elements. You should only inherit from this if
    you define new types directly based on python types.

    """
    def __new__(cls):
        obj = super(AbstractElement, cls).__new__(cls)
        object.__setattr__(obj, '__initialized', False)
        return obj

    def __init__(self, *args, **kwargs):
        for (key, val) in list(kwargs.items()):
            object.__setattr__(self, key, val)

        # Declare this instance ready for usage. Note that derived
        # objects must do their own initialization first, before
        # calling the superclass constructor (i.e. this function),
        # since this effectively "seals" the instance.
        #
        # (we need to call object.__setattr__ directly to bypass our
        # own __setattr__ implementation)
        object.__setattr__(self, '__initialized', True)

    def __setattr__(self, name, value):
        if object.__getattribute__(self, '__initialized'):
            # initialization phase is over -- no new attributes should
            # be created. Check to see if the attribute exists -- if it
            # doesn't, we raise an AttributeError (with a sensible
            # error message)
            try:
                object.__getattribute__(self, name)
                object.__setattr__(self, name, value)
            except AttributeError:
                raise AttributeError("Can't set attribute '%s' on object '%s' after initialization" % (name, self.__class__.__name__))
        else:
            # Still in initialization phase -- ok to create new
            # attributes
            object.__setattr__(self, name, value)

    def _get_tagname(self):
        return self.__class__.__name__.lower()

    tagname = property(_get_tagname)
    """The tag used for this element in the resulting XHTML (the default implementation simply uses the class name, lowercased)."""

    classname = None
    """If set, this property gets converted to a ``@class`` attribute in the resulting XHTML."""
    
    def as_xhtml(self, uri=None):
        """Converts this object to a ``lxml.etree`` object (with children)

        :param uri: If provided, gets converted to an ``@about`` attribute in the resulting XHTML.
        :type uri: str

        """
        attrs = {}
        for stdattr in ('class', 'id', 'dir', 'lang', 'src', 'href', 'name', 'alt', 'role'):
            if hasattr(self,stdattr):
                attrs[stdattr] = getattr(self,stdattr)
        return E(self.tagname, attrs) 

class UnicodeElement(AbstractElement, str):
    """Based on :py:class:`str`, but can also have other
properties (such as ordinal label, date of enactment, etc)."""

    # immutable objects (like strings, unicode, etc) must provide a __new__ method
    def __new__(cls, arg='', *args, **kwargs):
        if not isinstance(arg, str):
            raise TypeError("%r is not a str" % arg)
        # obj = str.__new__(cls, arg)
        obj = str.__new__(cls,arg)
        object.__setattr__(obj, '__initialized', False)
        return obj

    def as_xhtml(self, uri=None):
        res = super(UnicodeElement, self).as_xhtml(uri)
        if self:
            res.text = str(self)
        return res
        

class CompoundElement(AbstractElement, list):
    """Based on :py:class:`list` and contains other :py:class:`AbstractElement` objects, but can also have properties of it's own."""
    def __new__(cls, arg=[], *args, **kwargs):
        # ideally, we'd like to do just "obj = list.__new__(cls,arg)"
        # but that doesn't seem to work
        obj = list.__new__(cls)
        obj.extend(arg)
        object.__setattr__(obj, '__initialized', False)
        return obj

    def __str__(self):
        return self.as_plaintext()

    def _cleanstring(self, s):

        # valid chars according to the XML spec
        def _valid(i):
            return (
                0x20 <= i <= 0xD7FF 
                or i in (0x9, 0xA, 0xD)
                or 0xE000 <= i <= 0xFFFD
                or 0x10000 <= i <= 0x10FFFF
                )
            
        return ''.join(c for c in s if _valid(ord(c)))

    def as_plaintext(self):
        """Returns the plain text of this element, including child elements."""
        res = []
        for subpart in self:
            if isinstance(subpart, str):
                res.append(util.normalize_space(subpart))
            elif (isinstance(subpart, AbstractElement) or hasattr(subpart, 'as_plaintext')):
                res.append(subpart.as_plaintext())
        # the rule for concatenating children into a plaintext string is:
        # filter out all empty children, then place single space between the others.
        return " ".join(filter(None,res))
        
    def as_xhtml(self, uri=None):
        children = []
        # start by handling all children recursively
        for subpart in self:
            if (isinstance(subpart, AbstractElement) or hasattr(subpart, 'as_xhtml')):
                node = subpart.as_xhtml(uri)
                if node is not None:
                    children.append(node)
            elif isinstance(subpart, str):
                children.append(self._cleanstring(subpart))
            else:
                log.warning("as_xhtml: Can't render %s instance" %
                            subpart.__class__.__name__)
                # this is a reasonable attempt
                children.append(str(subpart))

        # Then massage a list of attributes for the main node
        attrs = {}

        if self.classname  is not None:
            attrs['class'] = self.classname
            
        # copy (a subset of) standard xhtml attributes
        for stdattr in ('class', 'id', 'dir', 'lang', 'src', 'href', 'name', 'alt', 'role', 'typeof'):
            if hasattr(self,stdattr):
                attrs[stdattr] = getattr(self,stdattr)

        # create extra attributes depending on circumstances
        if hasattr(self,'uri') and self.uri:
            attrs['about'] = self.uri
            
        if hasattr(self,'uri') and self.uri and hasattr(self,'meta') and self.meta:
            assert isinstance(self.meta,Graph), "self.meta is %r, not rdflib.Graph" % type(self.meta)
            # we sort to get a predictable order (by predicate)
            for (s,p,o) in sorted(self.meta, key=itemgetter(1)):
                if s != URIRef(self.uri):
                    continue
                if p == RDF.type:
                    attrs['typeof'] = self.meta.qname(o)
                    # attrs['rev'] = self.meta.qname(DCT.isPartOf)
                elif p == DCT.title:
                    attrs['property'] = self.meta.qname(p)
                    attrs['content'] = o.toPython()
                else:
                    children.insert(0, self._span(s,p,o,self.meta))

        # for each childen that is a string, make sure it doesn't
        # contain any XML illegal characters
        return E(self.tagname, attrs, *children)

    def _span(self, subj, pred, obj, graph):
        """Returns any triple as a span element with rdfa attributes. Object
           can be a uriref or literal, subject must be a
           uriref. Bnodes not supported. Recursively creates sub-span
           elements with for each uriref object that is the subject in
           another triple in graph.
        """
        children = []
        if isinstance(obj,Literal):
            o_python = obj.toPython()
            if isinstance(o_python, datetime.date):
                o_python = o_python.isoformat()
            attrs = {
                # 'about':self.uri,
                'property':self.meta.qname(pred),
                'content': o_python
            }

            if obj.datatype:
                attrs['datatype'] = self.meta.qname(obj.datatype)
            else:
                # only datatype-less literals can have language
                attrs[XML_LANG] = obj.language if obj.language else ''
        elif isinstance(obj,URIRef):
            attrs = {
                # 'about':self.uri,
                # 'about': str(obj),
                'rel':self.meta.qname(pred),
                'href':str(obj)
            }
            for sub_pred, sub_obj in graph.predicate_objects(subject=obj):
                children.append(self._span(obj, sub_pred, sub_obj, graph))

        # Theoretical, obj could be a BNode, but that should never happen. If
        # it does, just silently ignore it.
        # else:
        #     raise ValueError("Type %s not supported as object" % type(obj))

        return E('span', attrs, *children)

        

# Abstract classes intendet to use with multiple inheritance, which
# adds common properties
class TemporalElement(AbstractElement):
    """A TemporalElement has a number of temporal properties
    (``entryintoforce``, ``expires``) which states the temporal frame
    of the object.

    This class is intended to be inherited using multiple inheritance
    together with some main element type.

    >>> class TemporalHeading(UnicodeElement, TemporalElement):
    ...     pass
    >>> c = TemporalHeading("This heading has a start and a end date",
    ...                      entryintoforce=datetime.date(2013,1,1),
    ...                      expires=datetime.date(2013,12,31))
    >>> c.in_effect(datetime.date(2013,7,1))
    True
    >>> c.in_effect(datetime.date(2014,7,1))
    False

    """
    # can't initialize these 2 fields, since they get serialized, and
    # this clashes with test case files.
    
#     def __init__(self, *args, **kwargs):
#         self.entryintoforce = None
#         self.expires = None
#         super(TemporalElement, self).__init__(*args, **kwargs)

    def in_effect(self, date=None):
        """Returns True if the object is in effect at *date*."""
        return (date >= self.entryintoforce) and (date <= self.expires)

class PredicateElement(AbstractElement):
    """Inheriting from this gives the subclass a ``predicate`` attribute,
    which describes the RDF predicate to which the class is the RDF
    subject (eg. if you want to model the title of a document, you
    would inherit from UnicodeElement and this, and then set
    ```predicate`` to ``rdflib.URIRef('http://purl.org/dc/elements/1.1/title')``.
    """
    def __init__(self, *args, **kwargs):
        if 'predicate' in kwargs:
            self.predicate = kwargs['predicate']
            # switch the full uriref
            # (http://rinfo.lagrummet...#paragraf) to one using a
            # namespace prefix, if we know of one:
            shorten = False
            for (prefix, ns) in list(util.ns.items()):
                if kwargs['predicate'].startswith(ns):
                    predicateuri = kwargs['predicate']
                    kwargs['predicate'] = kwargs[
                        'predicate'].replace(ns, prefix + ":")
                    # print "Shorten predicate %s to: %s" % (predicateuri, kwargs['predicate'])
                    shorten = True
            #if not shorten:
            #   print "Couldn't shorten predicate: %s" % self.predicate
        else:
            # From the RDF Schema spec: 'This is the class of
            # everything. All other classes are subclasses of this
            # class.'
            from rdflib import RDFS
            self.predicate = RDFS.Resource
        super(PredicateElement, self).__init__(*args, **kwargs)


class OrdinalElement(AbstractElement):
    """A OrdinalElement has a explicit ordinal number. The ordinal does
    not need to be strictly numerical, but can be eg. '6 a' (which is
    larger than 6, but smaller than 7). Classes inherited from this
    can be compared with each other.

    This class is intended to be inherited using multiple inheritance
    together with some main element type.

    >>> class OrdinalHeading(UnicodeElement, OrdinalElement):
    ...     pass
    >>> a = OrdinalHeading("First", ordinal="1")
    >>> b = OrdinalHeading("Second", ordinal="2")
    >>> c = OrdinalHeading("In-between", ordinal="1 a")
    >>> a < b
    True
    >>> a < c
    True
    >>> b < c
    False

    """

    def __init__(self, *args, **kwargs):
        self.ordinal = None
        super(OrdinalElement, self).__init__(*args, **kwargs)

    def __lt__(self, other):
        return util.numcmp(self.ordinal, other.ordinal) < 0

    def __le__(self, other):
        return util.numcmp(self.ordinal, other.ordinal) <= 0

    def __eq__(self, other):
        return self.ordinal == other.ordinal

    def __ne__(self, other):
        return self.ordinal != other.ordinal

    def __gt__(self, other):
        return util.numcmp(self.ordinal, other.ordinal) > 0

    def __ge__(self, other):
        return util.numcmp(self.ordinal, other.ordinal) >= 0


class Link(UnicodeElement): 
    """A unicode string with also has a ``.uri`` attribute"""
    tagname = 'a'

    def __repr__(self):
        return 'Link(\'%s\', uri=%s)' % (self, self.uri)

    def as_xhtml(self, uri):
        element = super(Link, self).as_xhtml(uri)
        if hasattr(self,'uri'):
            element.set('href', self.uri)
        return element
        

class LinkSubject(PredicateElement, Link):
    """A unicode string that has both ``predicate`` and ``uri``
    attributes, i.e. a typed link. Note that predicate should be a
    string that represents a Qname, eg 'dct:references', not a proper
    rdflib object.

    """
    def as_xhtml(self, uri):
        element = super(LinkSubject, self).as_xhtml(uri)
        if hasattr(self,'predicate'):
            element.set('rel', self.predicate)
        return element


class Body(CompoundElement):
    def as_xhtml(self, uri):
        element = super(Body, self).as_xhtml(uri)
        element.set('about', uri)
        return element
class Title(CompoundElement): pass
class Page(CompoundElement, OrdinalElement):
    tagname = "div"
    classname = "page"
class Nav(CompoundElement): pass

class SectionalElement(CompoundElement):
    tagname = "div"

    def _get_classname(self):
        return self.__class__.__name__.lower()
    classname = property(_get_classname)

    def as_xhtml(self, baseuri):
        if hasattr(self, 'uri'):
            newuri = self.uri
        else:
            newuri = baseuri + "#S%s" % self.ordinal
        element = super(SectionalElement, self).as_xhtml(baseuri)
        if not hasattr(self, 'uri') or not hasattr(self, 'meta'):
            element.set('property', 'dct:title')
            element.set('content', self.title)
            element.set('typeof', 'bibo:DocumentPart')
            element.set('about', newuri)
            # NOTE: we don't set xml:lang for either the main @content
            # or the @content in the below <span> -- the data does not
            # originate from RDF and so isn't typed like that.
            if hasattr(self,'ordinal'):
                attrs = {'about': newuri,
                         'property': 'bibo:chapter',
                         'content': self.ordinal}
                element.insert(0,E('span',attrs))
            if hasattr(self,'identifier'):
                attrs = {'about': newuri,
                         'property': 'dct:identifier',
                         'content': self.identifier}
                element.insert(0,E('span',attrs))
            if element.text: # make sure that naked PCDATA comes after the elements we've inserted
                element[-1].tail = element.text
                element.text = None

        return element
    

class Section(SectionalElement): pass

class Subsection(SectionalElement): pass

class Subsubsection(SectionalElement): pass

class Paragraph(CompoundElement):
    tagname = 'p'
    
class Preformatted(Paragraph):
    tagname = 'pre'

class Heading(CompoundElement, OrdinalElement):
    tagname = 'h1' # fixme: take level into account

class Footnote(CompoundElement): pass
class OrderedList(CompoundElement):
    tagname = 'ol'
    
class UnorderedList(CompoundElement):
    tagname = 'ul'
# 
# class DefinitionList(CompoundElement):
#     tagname = 'dl'
#     
# class Term(CompoundElement): pass
# class Definition(CompoundElement): pass
class ListItem(CompoundElement, OrdinalElement):
    tagname = 'li'


def __serializeNode(node, serialize_hidden_attrs=False):
    # print "serializing: %r" % node

    # Special handling of pyparsing.ParseResults -- deserializing of
    # these won't work (easily)
    if isinstance(node, pyparsing.ParseResults):
        xml = util.parseresults_as_xml(node)
        return ET.XML(xml)

    # We use type() instead of isinstance() because we want to
    # serialize str derived types using their correct class names
    if type(node) == str:
        nodename = "str"
    elif type(node) == bytes:
        nodename = "bytes"
    else:
        nodename = node.__class__.__name__
    e = ET.Element(nodename)
    if hasattr(node, '__dict__'):
        for key in [x for x in list(node.__dict__.keys()) if serialize_hidden_attrs or not x.startswith('_')]:
            val = node.__dict__[key]
            if val is None:
                continue
            if (isinstance(val, (str,bytes))):
                e.set(key, val)
            else:
                e.set(key, repr(val))

    if isinstance(node, str):
        if node:
            e.text = str(node)
    elif isinstance(node, bytes):
        if node:
            e.text = node.decode()
    elif isinstance(node, int):
        e.text = str(node)
    elif isinstance(node, list):
        for x in node:
            e.append(__serializeNode(x))
    else:
        e.text = repr(node)
        # raise TypeError("Can't serialize %r (%r)" % (type(node), node))
    return e

def __deserializeNode(elem, caller_globals):
    # print "element %r, attrs %r" % (elem.tag, elem.attrib)
    # kwargs = elem.attrib

    # specialcasing first -- class objects for these native objects
    # can't be created by the"caller_globals[elem.tag]" call below
    if elem.tag == 'int':
        i = 0
        cls = i.__class__
    elif elem.tag == 'str':
        i = ''
        cls = i.__class__
    elif elem.tag == 'bytes':
        i = b''
        cls = i.__class__
    elif elem.tag == 'dict':
        i = {}
        cls = i.__class__
    else:
        # print "creating cls for %s" % elem.tag
        cls = caller_globals[elem.tag]

    if str == cls or str in cls.__bases__:
        c = cls(elem.text, **elem.attrib)

    elif bytes == cls or bytes in cls.__bases__:
        c = cls(elem.text.encode(), **elem.attrib)

    elif int == cls or int in cls.__bases__:
        c = cls(int(elem.text), **elem.attrib)

    elif dict == cls or dict in cls.__bases__:
        c = cls(ast.literal_eval(elem.text), **elem.attrib)

    elif datetime.date == cls or datetime.date in cls.__bases__:
        m = re.match(r'[\w\.]+\((\d+), (\d+), (\d+)\)', elem.text)
        c = cls(int(m.group(1)), int(m.group(2)), int(m.group(3)), **elem.attrib)

    else:
        c = cls(**elem.attrib)
        for subelem in elem:
            # print "Recursing"
            c.append(__deserializeNode(subelem, caller_globals))

    return c

# in-place prettyprint formatter
# http://infix.se/2007/02/06/gentlemen-_indentElement-your-xml
def _indentTree(elem, level=0):
    i = "\n" + level * "  "
    if len(elem) > 0:
        if not elem.text or not elem.text.strip():
            elem.text = i + "  "
        for e in elem:
            _indentElement(e, level + 1)
            if not e.tail or not e.tail.strip():
                e.tail = i + "  "
        if not e.tail or not e.tail.strip():
            e.tail = i
# This should never happen
#    else:
#        if level and (not elem.tail or not elem.tail.strip()):
#            elem.tail = i


def _indentElement(elem, level=0):
    i = "\n" + level * "  "
    if len(elem):
        if not elem.text or not elem.text.strip():
            elem.text = i + "  "
        for elem in elem:
            _indentElement(elem, level + 1)
        if not elem.tail or not elem.tail.strip():
            elem.tail = i
    else:
        if level and (not elem.tail or not elem.tail.strip()):
            elem.tail = i


