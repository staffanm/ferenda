Building structured documents
=============================

Any structured documents can be viewed as a tree of higher-level
elements (such as chapters or sections) that contains smaller elements
(like subsections or lists) that each in turn contains even smaller
elements (like paragraphs or list items). When using ferenda, you can
create documents by creating such trees of elements. The
:py:mod:`ferenda.elements` module contains classes for such elements.

Most of the classes can be used like python lists (and are, in fact,
subclasses of :py:class:`list`). Unlike the aproach used by
``xml.etree.ElementTree`` and ``BeautifulSoup``, where all
objects are of a specific class, and a object property determines the
type of element, the element objects are of different classes if the
elements are different. This means that elements representing a
paragraph are :py:class:`ferenda.elements.Paragraph`, and elements
representing a document section are
:py:class:`ferenda.elements.Section` and so on. The core
:py:mod:`ferenda.elements` module contains around 15 classes that
covers many basic document elements, and the submodule
:py:mod:`ferenda.elements.html` contains classes that correspond to
all HTML tags. There is some functional overlap between these two
module, but :py:mod:`ferenda.elements` contains several constructs
which aren't directly expressible as HTML elements
(eg. :py:class:`ferenda.elements.Page`,
:py:class:`ferenda.elements.SectionalElement` and
:py:class:`ferenda.elements.Footnote`)

Each element constructor (or at least those derived from
:py:class:`~ferenda.elements.CompoundElement`) takes a list as an
argument (same as :py:class:`list`), but also any number of keyword
arguments. This enables you to construct a simple document like this::

  from ferenda.elements import Body, Heading, Paragraph, Footnote
  
  doc = Body([Heading(["About Doc 43/2012 and it's interpretation"],predicate="dct:title"),
              Paragraph(["According to Doc 43/2012",
                         Footnote(["Available at http://example.org/xyz"]),
                         " the bizbaz should be frobnicated"])
             ])

.. note::

   Since :py:class:`~ferenda.elements.CompoundElement` works like
   :py:class:`list`, which is initialized with any iterable, you
   should normalliy initialize it with a single-element list of
   strings. If you initialize it directly with a string, the
   constructor will treat that string as an iterable and create one
   child element for every character in the string.

Creating your own element classes
---------------------------------

The exact structure of documents differ greatly. A general document
format such as XHTML or ODF cannot contain special constructs for
preamble recitals of EC directives or patent claims of US patents. But
your own code can create new classes for this. Example::

  from ferenda.elements import CompoundElement, OrderedElement
  
  class Preamble(CompoundElement): pass
  class PreambleRecital(CompoundElement,OrderedElement):
      tagname = "div"
      rdftype = "eurlex:PreambleRecital"
  
  doc = Preamble([PreambleRecital("Un",ordinal=1)],
                 [PreambleRecital("Deux",ordinal=2)],
                 [PreambleRecital("Trois",ordinal=3)])
  

Mixin classes
-------------

As the above example shows, it's possible and even recommended to use
multiple inheritance to compose objects by subclassing two classes --
one main class who's semantics you're extending, and one mixin class
that contains particular properties. The following classes are useful
as mixins:

* :py:class:`~ferenda.elements.OrdinalElement` (for representing
  elements with some sort of ordinal numbering)
* :py:class:`~ferenda.elements.PredicateType` (for representing
  literal data that are typed using a RDF predicaet)
* :py:class:`~ferenda.elements.TemporalElement` (for representing
  things that has a start and/or a end date


Rendering to XHTML
------------------

The built-in classes are rendered as XHTML by the built-in method
:py:meth:`~ferenda.DocumentRepository.render_xhtml`. Your own classes
can specify how they are to be rendered in XHTML by overriding the
:data:`~ferenda.elements.AbstractElement.tagname` and
:data:`~ferenda.elements.AbstractElement.classname` properties, or for
full control, the :data:`~ferenda.elements.AbstractElement.as_xhtml`
method.


Convenience methods
-------------------

Your element tree structure can be serialized to well-formed XML using
the :py:func:`~ferenda.elements.serialize` method. Such a
serialization can be turned back into the same tree using
:py:func:`~ferenda.elements.deserialize`. This is primarily useful
during debugging.

You might also find the
:data:`~ferenda.elements.CompoundElement.as_plaintext` method
useful. It works similar to
:data:`~ferenda.elements.AbstractElement.as_xhtml`, but returns a
plaintext string with the contents of an element, including all
sub-elements

The :py:mod:`ferenda.elements.html` module contains the method
:py:func:`~ferenda.elements.html.elements_from_soup` which converts a
BeautifulSoup tree into the equivalent tree of element objects.



