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
import xml.etree.cElementTree as ET
from lxml.builder import ElementMaker
from operator import itemgetter

import six
from six import text_type as str
from rdflib import Graph, Namespace, Literal, URIRef
try:
    import pyparsing
    pyparsing_available = True
except ImportError:
    pyparsing_available = False

from ferenda import util

DCT = Namespace(util.ns['dct'])
RDF = Namespace(util.ns['rdf'])
XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"
log = logging.getLogger(__name__)
E = ElementMaker(namespace="http://www.w3.org/1999/xhtml")

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
        """Converts this object to a :py:class:`lxml.etree` object (with children)

        :param uri: If provided, gets converted to an ``@about`` attribute in the resulting XHTML.
        :type uri: str

        """
        
        attrs = {}
        for stdattr in ('class', 'id', 'dir', 'lang', 'src', 'href', 'name', 'alt', 'role'):
            if hasattr(self,stdattr):
                attrs[stdattr] = getattr(self,stdattr)
        return E(self.tagname, attrs, str(self))



class UnicodeElement(AbstractElement, six.text_type):
    """Based on :py:class:`str`, but can also have other
properties (such as ordinal label, date of enactment, etc)."""

    # immutable objects (like strings, unicode, etc) must provide a __new__ method
    def __new__(cls, arg='', *args, **kwargs):
        if not isinstance(arg, six.text_type):
            if sys.version_info < (3,0,0):
                raise TypeError("%r is not unicode" % arg)
            else:
                raise TypeError("%r is not str" % arg)
        # obj = str.__new__(cls, arg)
        obj = six.text_type.__new__(cls,arg)
        object.__setattr__(obj, '__initialized', False)
        return obj


class IntElement(AbstractElement, int):
    """Based on :py:class:`int`, but can also have other properties."""

    # immutable objects must provide a __new__ method
    def __new__(cls, arg=0, *args, **kwargs):
        if not isinstance(arg, int):
            raise TypeError("%r is not int" % arg)
        obj = int.__new__(cls, arg)
        object.__setattr__(obj, '__initialized', False)
        return obj


class DateElement(AbstractElement, datetime.date):
    """Based on :py:class:`datetime.date`, but can also have other properties."""

    # immutable objects must provide a __new__ method
    def __new__(cls, arg=datetime.date.today(), *args, **kwargs):
        if not isinstance(arg, datetime.date):
            raise TypeError("%r is not datetime.date" % arg)
        obj = datetime.date.__new__(cls, arg.year, arg.month, arg.day)
        object.__setattr__(obj, '__initialized', False)
        return obj


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
        else:
            raise ValueError("Type %s not supported as object" % type(obj))

        return E('span', attrs, *children)

        
class MapElement(AbstractElement, dict):
    """Based on :py:class:`dict`, but can also have other properties."""
    def __new__(cls, arg={}, *args, **kwargs):
        # ideally, we'd like to do just "obj = dict.__new__(cls,arg)"
        # but that doesn't seem to work
        obj = dict.__new__(cls, arg)
        obj.update(arg)
        object.__setattr__(obj, '__initialized', False)
        return obj

# Abstract classes intendet to use with multiple inheritance, which
# adds common properties
class TemporalElement(object):
    """A TemporalElement has a number of temporal properties
    (``entryintoforce``, ``expires``) which states the temporal frame
    of the object.

    This class is intended to be inherited using multiple inheritance
    together with some main element type.

    >>> class TemporalHeading(UnicodeElement, TemporalElement):
    ...     pass
    >>> c = TemporalHeading(["This heading has a start and a end date"])
    >>> c.entryintoforce = datetime.date(2013,1,1)
    >>> c.expires = datetime.date(2013,12,31)
    >>> c.in_effect(datetime.date(2013,7,1))
    True
    >>> c.in_effect(datetime.date(2014,7,1))
    False

    """
    def __init__(self):
        self.entryintoforce = None
        self.expires = None

        
    def in_effect(self, date=None):
        """Returns True if the object is in effect at *date* (or today, if date is not provided)."""
        if not date:
            date = datetime.date.today()
        return (date >= self.entryintoforce) and (date <= self.expires)


class OrdinalElement(object):
    """A OrdinalElement has a explicit ordinal number. The ordinal does
    not need to be strictly numerical, but can be eg. '6 a' (which is
    larger than 6, but smaller than 7). Classes inherited from this
    can be compared with each other.

    This class is intended to be inherited using multiple inheritance
    together with some main element type.

    >>> class OrdinalHeading(UnicodeElement, OrdinalElement):
    ...     pass
    >>> a = OrdinalHeading(["First"], ordinal="1")
    >>> b = OrdinalHeading(["Second"], ordinal="2")
    >>> c = OrdinalHeading(["In-between"], ordinal="1 a")
    >>> a < b
    True
    >>> a < c
    True
    >>> b < c
    False

    """

    def __init__(self):
        self.ordinal = None

    # FIXME: do a proper mostly-numerical compariom using util.numcmp
    def __lt__(self, other):
        return self.ordinal < other.ordinal

    def __le__(self, other):
        return self.ordinal <= other.ordinal

    def __eq__(self, other):
        return self.ordinal == other.ordinal

    def __ne__(self, other):
        return self.ordinal != other.ordinal

    def __gt__(self, other):
        return self.ordinal > other.ordinal

    def __ge__(self, other):
        return self.ordinal == other.ordinal


from ferenda import util


class PredicateType(object):
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
        super(PredicateType, self).__init__(*args, **kwargs)


class Link(UnicodeElement): 
    """A unicode string with also has a ``.uri`` attribute"""
    tagname = 'a'
    def __repr__(self):
        return 'Link(\'%s\',uri=%r)' % (six.text_type.__repr__(self), self.uri)

    def as_xhtml(self, uri):
        element = super(Link, self).as_xhtml(uri)
        if hasattr(self,'uri'):
            element.set('href', self.uri)
        return element
        

class LinkSubject(PredicateType, Link):
    """A unicode string that has both ``predicate`` and ``uri``
attributes, i.e. a typed link. Note that predicate should be a string that represents a Qname, eg 'dct:references', not a proper rdflib object."""
    def as_xhtml(self, uri):
        element = super(LinkSubject, self).as_xhtml(uri)
        if hasattr(self,'predicate'):
            element.set('rel', self.predicate)
        return element
        
    pass  # A RDFish link





# Commented this out in order to keep documented API surface smaller
# -- let's see what breaks
# 

# # Examples of other mixins and inherited classes
# class EvenMixin():
#     def iseven(self):
#         return (len(self.keyword) % 2 == 0)
# 
# 
# class DerivedUnicode(UnicodeElement, EvenMixin):
#     # an example on how to customize object initialization, while still
#     # letting the base class do it's initialization
#     def __init__(self, *args, **kwargs):
#         if kwargs['keyword']:
#             self.keyword = kwargs['keyword'].upper()
#             del kwargs['keyword']
#         super(DerivedUnicode, self).__init__(*args, **kwargs)
# 
# 
# class DerivedList(CompoundElement, EvenMixin):
#     pass
# 
# 
# class DerivedDict(MapElement, EvenMixin):
#     pass
# 
# 
# class DerivedInt(IntElement, EvenMixin):
#     pass
# 
# 
# class DerivedDate(DateElement, EvenMixin):
#     pass
# 
# 
# class RDFString(PredicateType, UnicodeElement):
#     # N.B: if we inherit from (UnicodeElement,PredicateType)
#     # instead, PredicateType.__init__ never gets called! But this way,
#     # AbstractElement.__init__ never gets called. I think i must
#     # read descrintro again...
#     pass
# 
# 
class UnicodeSubject(PredicateType, UnicodeElement): pass

class Body(CompoundElement):
    def as_xhtml(self, uri):
        element = super(Body, self).as_xhtml(uri)
        element.set('about', uri)
        return element
class Title(CompoundElement): pass
class Page(CompoundElement, OrdinalElement): pass
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
# 
# if __name__ == '__main__':
# 
#     # print "Testing DerivedUnicode"
#     u = DerivedUnicode('blahonga', keyword='myunicode')
#     # print "\trepr(u): %s"   % repr(u)
#     # print "\tu[1:4]: %r"    % u[1:4]
#     # print "\tu.keyword: %r" % u.keyword
#     # print "\tu.iseven: %r"  % u.iseven()
# 
#     # print "Testing DerivedList"
#     l = DerivedList(['x', 'y', 'z'], keyword='mylist')
#     # print "\tl[1]: %r"      % l[1]
#     # print "\tl.keyword: %r" % l.keyword
#     # print "\tl.iseven: %r"  % l.iseven()
# 
#     # print "Testing DerivedDict"
#     d = DerivedDict({'a': 'foo', 'b': 'bar'}, keyword='mydict')
#     # print "\td['a']: %r"    % d['a']
#     # print "\td.keyword: %r" % d.keyword
#     # print "\td.iseven: %r"  % d.iseven()
# 
#     # print "Testing DerivedInt"
#     i = DerivedInt(42, keyword='myint')
#     # print "\ti: %r"    % i
#     # print "\ti+5: %r"  % (i+5)
#     # print "\ti.keyword: %r" % d.keyword
#     # print "\ti.iseven: %r"  % d.iseven()
# 
#     # print "Testing DerivedDate"
#     nativedate = datetime.date(2008, 3, 15)
#     dt = DerivedDate(nativedate, keyword='mydate')
#     # print "\tdt: %r"    % dt
#     # print "\tdt.keyword: %r" % dt.keyword
#     # print "\tdt.iseven: %r"  % dt.iseven()
# 
#     # print "Testing RDFString"
#     r = RDFString('Typisk dokumentrubrik', keyword='mysubject')
#     # print "\trepr(r): %s"   % repr(r)
#     # print "\tr[1:4]: %r"    % r[1:4]
#     # print "\tr.keyword: %r" % r.keyword
#     # print "\tr.predicate: %r" % r.predicate
#     from rdflib import URIRef
#     r.predicate = URIRef('http://purl.org/dc/terms/title')
#     # print "\tr.predicate: %r" % r.predicate
# 
#     c = DerivedList([u, l, d, i, dt, r])
#     x = serialize(c)
#     print(x)
#     print()
#     y = deserialize(x, globals())
#     print((serialize(y)))



# http://infix.se/2007/02/06/gentlemen-_indentElement-your-xml
def _indentTree(elem, level=0):
    i = "\n" + level * "  "
    if len(elem):
        if not elem.text or not elem.text.strip():
            elem.text = i + "  "
        for e in elem:
            _indentElement(e, level + 1)
            if not e.tail or not e.tail.strip():
                e.tail = i + "  "
        if not e.tail or not e.tail.strip():
            e.tail = i
    else:
        if level and (not elem.tail or not elem.tail.strip()):
            elem.tail = i


def __serializeNode(node, serialize_hidden_attrs=False):
    # print "serializing: %r" % node

    # Special handling of pyparsing.ParseResults -- deserializing of
    # these won't work (easily)
    if pyparsing_available and isinstance(node, pyparsing.ParseResults):
        return ET.XML(node.asXML())

    # We use type() instead of isinstance() because we want to
    # serialize str derived types using their correct class names
    if type(node) == six.text_type:
        nodename = "str"
    elif type(node) == six.binary_type:
        nodename = "bytes"
    else:
        nodename = node.__class__.__name__
    e = ET.Element(nodename)
    if hasattr(node, '__dict__'):
        for key in [x for x in list(node.__dict__.keys()) if serialize_hidden_attrs or not x.startswith('_')]:
            val = node.__dict__[key]
            if (isinstance(val, (six.text_type,six.binary_type))):
                e.set(key, val)
            else:
                e.set(key, repr(val))

    if isinstance(node, (six.text_type,six.binary_type)):
        if node:
            e.text = node
    elif isinstance(node, int):
        e.text = str(node)
    elif isinstance(node, list):
        for x in node:
            e.append(__serializeNode(x))
    elif isinstance(node, dict):
        for x in list(node.keys()):
            k = ET.Element("Key")
            k.append(__serializeNode(x))
            e.append(k)

            v = ET.Element("Value")
            v.append(__serializeNode(node[x]))
            e.append(v)
    else:
        e.text = repr(node)
        # raise TypeError("Can't serialize %r (%r)" % (type(node), node))
    return e

def __deserializeNode(elem, caller_globals):
    # print "element %r, attrs %r" % (elem.tag, elem.attrib)
    #kwargs = elem.attrib specialcasing first -- classobjects for
    # these native objects can't be created by the"caller_globals[elem.tag]" call below
    if elem.tag == 'int':
        i = 0
        classobj = i.__class__
    elif elem.tag == 'str':
        i = ''
        classobj = i.__class__

#    flake8 craps out on byte literals?!
#    elif elem.tag == 'bytes':
#        i = b''
#        classobj = i.__class__
    elif elem.tag == 'unicode':
        raise ValueError("Cannot deserialize 'unicode' (should be str?)")
    else:
        # print "creating classobj for %s" % elem.tag
        classobj = caller_globals[elem.tag]

    testclass = classobj(**elem.attrib)

    if isinstance(testclass, str):
        c = classobj(str(elem.text), **elem.attrib)
    elif isinstance(classobj(**elem.attrib), int):
        c = classobj(int(elem.text), **elem.attrib)

    elif isinstance(testclass, str):
        if elem.text:
            c = classobj(str(elem.text), **elem.attrib)
        else:
            c = classobj(**elem.attrib)

    elif isinstance(testclass, datetime.date):
        m = re.match(r'\w+\((\d+), (\d+), (\d+)\)', elem.text)
        basedate = datetime.date(
            int(m.group(1)), int(m.group(2)), int(m.group(3)))
        c = classobj(basedate, **elem.attrib)

    elif isinstance(testclass, dict):
        c = classobj(**elem.attrib)
        # FIXME: implement this

    else:
        c = classobj(**elem.attrib)
        for subelem in elem:
            # print "Recursing"
            c.append(__deserializeNode(subelem, caller_globals))

    return c

# in-place prettyprint formatter


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
