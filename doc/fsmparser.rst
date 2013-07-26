Parsing document structure
===========================

In many scenarios, the basic steps in parsing source documents are
similar. If your source does not contain properly nested structures
that accurately represent the structure of the document (such as
well-authored XML documents), you will have to re-create the structure
that the author intended. Usually, text formatting and other clues
contain just enough information to do that.

In many cases, your source document will naturally be split up in a
large number of "chunks". These chunks may be lines or paragraphs in a
plaintext documents, or tags of a certain type in a certain location
in a HTML document. Regardless, it is often easy to generate a list of
such chunks.

These chunks can be fed to a *finite state machine*, which looks at
each chunk, determines what kind of *structural element* it probably
is (eg. a headline, the start of a chapter, a item in a bulleted
list...) by looking at the chunk in the context of previous chunks,
and then explicitly re-creates the document structure that the author
probably intended.

FSMParser
---------

The framework contains a class for creating such state machines,
``ferenda.FSMParser``. By defining a couple of _recognizers_
(functions that look at a chunk and determines if it is a particular
structural element), a couple of _constructors_ (functions that
creates a document element), some _states_ (identifiers for the
current state of the document being parsed) and a _transition_table_
(a mapping that determines parsing moves to a new state depending on
the current state and what the recognizer classifies the current chunk
as), you can instatiate this class, feed it the transition table and a
stream of chunks and have it generate a nested document object tree.

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

.. literalinclude:: fsmparser-example.py
  :lines: 3-17

The ``elements`` module contains ready-built classes which we can use
to build our constructors:

.. literalinclude:: fsmparser-example.py
  :lines: 19-33

Note that any constructor which may contain sub-elements must itself
call the ``make_children`` method of the parser. That method takes a
parent object, and then repeatedly creates child objects which it
attaches to that parent object, until a exit condition is met. Each
call to create a child object may, in turn, call make_children (not so
in this very simple example).

The final step in putting this together is defining the transition
table, and then creating, configuring and running the parser:

.. literalinclude:: fsmparser-example.py
  :lines: 35-69

Writing complex parsers
-----------------------

* Constructors and recognizers are any callables that can be called
  with the parser as only argument (so no class- or instancemethods)
* Parser has .currentstate, .debug, ._debug() and .reader
* .reader is Peekable()
* The transition table is a mapping between (currentstate, successful
  recognizer) and (constructor-or-false,newstate-or-None)
* the value in the transition table can also be a callable, which is
  called with (currentstate,symbol,parser?) and returns
  (constructor-or-false,newstate-or-None)
* Specify the order of recognizers to try.
* Take a look at the 2-level ReStructuredText parser in
  tech/pep:makeparser(), the RFC parser in tech/rfc:makeparser() or
  the wikitext parser in general/wiki:makeparser()


Tips for debugging your parser
------------------------------

Two useful commands in the devel module (FIXME: how to specify parser?
any fully qualified callable that returns a initialized parser?)::

  $ # sets debug, prints serialize(parser.parse(...))
  $ ./ferenda-build.py devel fsmparse parser < chunks
  $ # sets debug, returns name of matching function
  $ ./ferenda-build.py devel fsmanalyze parser <currentstate> < chunk

