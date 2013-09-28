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
(eg. :py:class:`~ferenda.elements.Page`,
:~py:class:`ferenda.elements.SectionalElement` and
:~py:class:`ferenda.elements.Footnote`)

Each element constructor (or at least those derived from
:py:class:`~ferenda.elements.CompoundElement`) takes a list as an
argument (same as :py:class:`list`), but also any number of keyword
arguments. This enables you to construct a simple document like this:

.. literalinclude:: examples/elementclasses.py
   :start-after: # begin makedoc
   :end-before: # end makedoc
  
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
your own code can create new classes for this. Example:

.. literalinclude:: examples/elementclasses.py
   :start-after: # begin derived-class
   :end-before: # end derived-class
  

Mixin classes
-------------

As the above example shows, it's possible and even recommended to use
multiple inheritance to compose objects by subclassing two classes --
one main class who's semantics you're extending, and one mixin class
that contains particular properties. The following classes are useful
as mixins:

* :py:class:`~ferenda.elements.OrdinalElement`: for representing
  elements with some sort of ordinal numbering. An ordinal element has
  an ``ordinal`` property, and different ordinal objects can be
  compared or sorted. The sort is based on the ordinal property. The
  ordinal property is a string, but comparisons/sorts are done in a
  natural way, i.e. "2" < "2 a" < "10".

* :py:class:`~ferenda.elements.TemporalElement`: for representing
  things that has a start and/or a end date. A temporal element has
  an ``in_effect`` method which takes a date (or uses today's date if
  none given) and returns true if that date falls between the start
  and end date.


Rendering to XHTML
------------------

The built-in classes are rendered as XHTML by the built-in method
:py:meth:`~ferenda.DocumentRepository.render_xhtml`, which first
creates a ``<head>`` section containing all document-level metadata
(i.e. the data you have specified in your documents ``meta``
property), and then calls the ``as_xhtml`` method on the root body
element. The method is called with ``doc.uri`` as a single argument,
which is then used as the RDF subject for all triples in the document
(except for those sub-elements which themselves have a ``uri``
property)

All built-in element classes derive from
:class:`~ferenda.elements.AbstractElement`, which contains a generic
implementation of :meth:`~ferenda.elements.AbstractElement.as_xhtml`,
that recursively creates a lxml element tree from itself and it's
children.

Your own classes can specify how they are to be rendered in XHTML by
overriding the :data:`~ferenda.elements.AbstractElement.tagname` and
:data:`~ferenda.elements.AbstractElement.classname` properties, or for
full control, the :meth:`~ferenda.elements.AbstractElement.as_xhtml`
method.

As an example, the class :class:`~ferenda.elements.SectionalElement`
overrides ``as_xhtml`` to the effect that if you provide
``identifier``, ``ordinal`` and ``title`` properties for the object, a
resource URI is automatically constructed and four RDF triples are
created (rdf:type, dct:title, dct:identifier, and bibo:chapter):

.. literalinclude:: examples/elementclasses.py
   :start-after: # begin as-xhtml
   :end-before: # end as-xhtml

...which results in:

.. literalinclude:: examples/elementclasses-part.xhtml
  
However, this is a convenience method of SectionalElement, amd may not
be appropriate for your needs. The general way of attaching metdata to
document parts, as specified in :ref:`parsing-metadata-parts`, is to
provide each document part with a ``uri`` and ``meta`` property. These
are then automatically serialized as RDFa statements by the default
``as_xhtml`` implementation.


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



