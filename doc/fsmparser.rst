Parsing document structure
===========================

In many scenarios, the basic steps in parsing source documents are
similar. If your source does not contain properly nested structures
that accurately represent the structure of the document (such as
well-authored XML documents), you will have to re-create the structure
that the author intended. Usually, text formatting, section numbering 
and other clues contain just enough information to do that.

In many cases, your source document will naturally be split up in a
large number of "chunks". These chunks may be lines or paragraphs in a
plaintext documents, or tags of a certain type in a certain location
in a HTML document. Regardless, it is often easy to generate a list of
such chunks. See, in particular, :doc:`readers`.

.. note:: 

   For those with a background in computer science and formal
   languages, a chunk is sort of the same thing as a token, but
   whereas a token typically is a few characters in length, a chunk is
   typically one to several sentences long. Splitting up a documents
   in chunks is also typically much simpler than the process of
   tokenization.

These chunks can be fed to a *finite state machine*, which looks at
each chunk, determines what kind of *structural element* it probably
is (eg. a headline, the start of a chapter, a item in a bulleted
list...) by looking at the chunk in the context of previous chunks,
and then explicitly re-creates the document structure that the author
(presumably) intended.

FSMParser
---------

The framework contains a class for creating such state machines,
:class:`~ferenda.FSMParser`. It is used with a set of the following objects:

============ =======================================================
Object       Purpose
============ =======================================================
Recognizers  Functions that look at a chunk and determines if 
             it is a particular structural element.
Constructors Functions that creates a document element from a chunk
             (or series of chunks)
States       Identifiers for the current state of the document being
             parsed, ie. "in-preamble", "in-ordered-list"
Transitions  mapping (current state(s), recognizer) ->
             (new state, constructor)
============ =======================================================

You initialize the parser with the transition table (which contains
the other objects), then call it's parse() method with a iterator of
chunks, an initial state, and an initial constructor. The result of
parse is a nested document object tree.

A simple example
----------------

Consider a very simple document format that only has three kinds of
structural elements: a normal paragraph, preformatted text, and
sections. Each section has a title and may contain paragraphs or
preformatted text, which in turn may not contain anything else. All
chunks are separated by double newlines

The section is identified by a header, which is any single-line string
followed by a line of = characters of the same length. Any time a new
header is encountered, this signals the end of the current section::

  This is a header
  ================

A preformatted section is any chunk where each line starts with at
least two spaces::

    # some example of preformatted text
    def world(name):
        return "Hello", name

A paragraph is anything else::

  This is a simple paragraph.
  It can contain short lines and longer lines. 

(You might recognize this format as a very simple form of
ReStructuredText).

Recognizers for these three elements are easy to build:

.. literalinclude:: examples/fsmparser-example.py
  :start-after: # begin recognizers
  :end-before: # end recognizers

The ``elements`` module contains ready-built classes which we can use
to build our constructors:

.. literalinclude:: examples/fsmparser-example.py
  :start-after: # begin constructors
  :end-before: # end constructors

Note that any constructor which may contain sub-elements must itself
call the :meth:`~ferenda.FSMParser.make_children` method of the
parser. That method takes a parent object, and then repeatedly creates
child objects which it attaches to that parent object, until a exit
condition is met. Each call to create a child object may, in turn,
call make_children (not so in this very simple example).

The final step in putting this together is defining the transition
table, and then creating, configuring and running the parser:

.. literalinclude:: examples/fsmparser-example.py
  :start-after: # begin main
  :end-before: # end main

The result of this parse is the following document object tree (passed
through :func:`~ferenda.elements.serialize`):

.. literalinclude:: examples/fsmparser-result.xml

Writing complex parsers
-----------------------

Recognizers
^^^^^^^^^^^


Recognizers are any callables that can be called with the parser
object as only parameter (so no class- or instancemethods). Objects
that implement ``__call__`` are OK, as are ``lambda`` functions.

One pattern to use when creating parsers is to have a method on your
docrepo class which defines a number of nested functions, then creates
a transition table using those functions, create the parser with that
transition table, and then return the initialized parser object. Your
main parse method can then call this method, break the input document
into suitable chunks, then call parse on the recieved parser object.

Constructors
^^^^^^^^^^^^

Like recognizers, constructors may be any callable, and they are
called with the parser object as the only parameter.

Constructors that return elements which in themselves do not contain
sub-elements are simple to write -- just return the created element
(see eg ``make_paragraph`` or ``make_preformatted`` above).

Constructors that are to return elements that may contain subelement
must first create the element, then call
parser.:meth:`ferenda.FSMParser.make_children` with that element as a
single argument. ``make_children`` will treat that element as a list,
and append any sub-elements created to that list, before returning it.


The parser object
^^^^^^^^^^^^^^^^^

The parser object is passed to every recognizer and constructor. The
most common use is to read the next available chunk from it's reader
property -- this is an instance of a simple wrapper around the stream
of chunks. The reader has two methods: ``peek`` and ``next``, which
both returns the next available chunk, but ``next`` also consumes the
chunk in question. A recognizer typically calls
``parser.reader.peek()``, a constructor typically calls
``parser.reader.next()``.

The parser object also has the following properties

============  ===============================================================
Property      Description
============  ===============================================================
currentstate  The current state of the parser, using whatever value for
              state that was defined in the transition table
              (typically a string)
debug         boolean that indicates whether to emit debug messages 
              (by default False)
============  ===============================================================

There is also a ``parser._debug()`` method that emits debug messages,
indicating current parser nesting level and current state, if
``parser.debug`` is ``True``

The transition table
^^^^^^^^^^^^^^^^^^^^

The transition table is a mapping between ``(currentstate(s), successful
recognizer)`` and ``(constructor-or-false,newstate-or-None)``

The transition table is used in the following way: All recognizers
that can be applicable in the current state are tried in the specified
order until one of them returns True. Using this pair of
(currentstate, recognizer), the corresponding value tuple is looked up
in the transition table.

``constructor-or-False``: ...

``newstate-or-None``: ...

The key in the transition table can also be a callable, which is
called with (currentstate,symbol,parser?) and is expected to return a 
``(constructor-or-false,newstate-or-None)`` tuple

Tips for debugging your parser
------------------------------

Two useful commands in the :class:`~ferenda.Devel` module::

  $ # sets debug, prints serialize(parser.parse(...))
  $ ./ferenda-build.py devel fsmparse parser < chunks
  $ # sets debug, returns name of matching function
  $ ./ferenda-build.py devel fsmanalyze parser <currentstate> < chunk
