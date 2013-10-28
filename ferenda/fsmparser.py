# -*- coding: utf-8 -*-
from __future__ import unicode_literals, print_function
import collections
import inspect

import six
from six import text_type as str

from ferenda.errors import FSMStateError


class FSMParser():

    """A configurable finite state machine for parsing documents with
    nested structure. You provide a set of *recognizers*, a set of
    *constructors*, a *transition table* and a *stream* of document text
    chunks, and it returns a hierarchical document object structure.

    See :doc:`../fsmparser`.

    """

    def __init__(self):
        self.debug = False
        self.transitions = None  # set by set_transitions
        self.recognizers = None  # set by set_recognizers() or set_transitions()
        self.reader = None  # set by parse()
        # somewhat magic
        self.initial_state = None
        self.initial_constructor = None
        # pseudo-internal
        self._state_stack = []

    def _debug(self, msg):
        """Prints a debug message, indented to show how far down in the nested structure we are"""
        if self.debug:
            stack = inspect.stack()
            calling_frame = [x[3] for x in stack][1]
            relative_depth = len(self._state_stack)
            print("%s[%s(%r)] %s" % (". " * relative_depth, calling_frame, self._state_stack, msg))

    def set_recognizers(self, *args):
        """Set the list of functions (or other callables) used in
        order to recognize symbols from the stream of text
        chunks. Recognizers are tried in the order specified here."""
        self.recognizers = args

    def set_transitions(self, transitions):
        """Set the transition table for the state matchine.

        :param transitions: The transition table, in the form of a mapping between two tuples. The first tuple should be the current state (or a list of possible current states) and a callable function that determines if a particular symbol is recognized ``(currentstate, recognizer)``. The second tuple should be a constructor function (or `False```) and the new state to transition into.
        
        """
        self.transitions = {}
        for (before, after) in transitions.items():
            (before_states, recognizer) = before
            if not callable(after):
                (constructor, after_state) = after
                assert (constructor == False) or callable(
                    constructor), "Specified constructor %r not callable" % constructor
            assert callable(recognizer), "Specified recognizer %r not callable" % recognizer
            if (not isinstance(before_states, (list, tuple))):
                before_states = [before_states]
            for before_state in before_states:
                if callable(after):
                    self._debug("%r,%s() -> %s()" %
                                (before_state, recognizer.__name__, after.__name__))
                elif callable(after[0]):
                    self._debug("%r,%s() -> %s(), %r" %
                                (before_state, recognizer.__name__, after[0].__name__, after[1]))
                else:
                    self._debug("%r,%s() -> %r, %r" %
                                (before_state, recognizer.__name__, after[0], after[1]))
                self.transitions[(before_state, recognizer)] = after

    def parse(self, chunks):
        """Parse a document in the form of an iterable of suitable
        chunks -- often lines or elements.  each chunk should be a
        string or a string-like obje ct.  Some examples::
        
          p = FSMParser()
          reader = TextReader("foo.txt")
          body = p.parse(reader.getiterator(reader.readparagraph),"body", make_body)
          body = p.parse(BeautifulSoup("foo.html").find_all("#main p"), "body", make_body)
          body = p.parse(ElementTree.parse("foo.xml").find(".//paragraph"), "body", make_body)

        :param chunks: The document to be parsed, as a list or any other
                       iterable of text-like objects.
        :param initialstate: The initial state for the machine. The
                             state must be present in the transition
                             table. This could be any object, but strings are
                             preferrable as they make error messages
                             easier to understand.
        :param initialconstructor: A function that creates a document
                                   root object, and then fills it with
                                   child objects using
                                   .make_children()
        :type initialconstructor: callable
        :returns: A document object tree.
        """
        self._debug("Starting parse")
        self.reader = Peekable(chunks)
        self._state_stack = [self.initial_state]
        return self.initial_constructor(self)

    def analyze_symbol(self):
        """Internal function used by make_children()"""
        try:
            rawchunk = self.reader.peek()
            chunk = str(rawchunk)
            if len(chunk) > 40:
                chunk = chunk[:25] + "[...]" + chunk[-10:]
            else:
                chunk = chunk

        except StopIteration:
            self._debug("We're done!")
            return None

        applicable_tmp = [x[1] for x in self.transitions.keys() if x[0] == self._state_stack[-1]]
        # Create correct sorting of applicable_recognizers
        applicable_recognizers = []
        for recognizer in self.recognizers:
            if recognizer in applicable_tmp:
                applicable_recognizers.append(recognizer)

        self._debug("Testing %r against %s (state %r) " %
                    (chunk, [x.__name__ for x in applicable_recognizers],
                     self._state_stack[-1]))
        for recognizer in applicable_recognizers:
            if recognizer(self):
                self._debug("%r -> %s" % (chunk, recognizer.__name__))
                return recognizer
        raise FSMStateError("No recognizer match for %r" % chunk)

    def transition(self, currentstate, symbol):
        """Internal function used by make_children()"""
        assert (currentstate, symbol) in self.transitions, "(%r, %r) should be in self.transitions" % (currentstate, symbol)

        t = self.transitions[(currentstate, symbol)]
        if callable(t):
            return t(symbol, self._state_stack)
        else:
            return t

    def make_child(self, constructor, childstate):
        """Internal function used by make_children(), which calls one
        of the constructors defined in the transition table."""

        if not childstate:
            childstate = self._state_stack[-1]
            self._debug("calling child constructor %s" % constructor.__name__)
        else:
            self._debug("calling child constructor %s in state %r" %
                        (constructor.__name__, childstate))
        self._state_stack.append(childstate)
        ret = constructor(self)
        self._state_stack.pop()  # do something with this?
        return ret

    def make_children(self, parent):
        """Creates child nodes for the current (parent) document node.

        :param parent: The parent document node, as any list-like object
                       (preferrably a subclass of
                       :py:class:`ferenda.elements.CompoundElement`)
        :returns: The same ``parent`` object.
        
        """
        self._debug("Making children for %s" % parent.__class__.__name__)
        while True:  # we'll break out of this when transition()
                    # returns a constructor that is False
            symbol = self.analyze_symbol()
            if symbol is None:  # no more symbols
                self._debug("We're done!")
                return parent

            (constructor, newstate) = self.transition(self._state_stack[-1],
                                                      symbol)

            if constructor is False:
                self._debug("transition(%r,%s()) -> (False,%r)" %
                            (self._state_stack[-1], symbol.__name__, newstate))
            else:
                self._debug("transition(%r,%s()) -> (%s(),%r)" %
                            (self._state_stack[-1], symbol.__name__,
                             constructor.__name__, newstate))

            # if transition() indicated that we should change state,
            # first find out whether the constructor will call
            # make_child, creating a new stack frame. This is
            # indicated by the callable having the 'newstate'
            # attribute (now set manually, should be through a
            # decorator)
            if newstate and not hasattr(constructor, 'newstate'):
                self._debug("Changing top of state stack (%r->%r)" %
                            (self._state_stack[-1], newstate))
                self._state_stack[-1] = newstate

            if constructor:
                element = self.make_child(constructor, newstate)
                if element is not None:
                    parent.append(element)
            else:
                # special weird hack - set the state we'll be
                # returning to by manipulating self._state_stack
                # FIXME: we have no regular test case for this path,
                # but integrationRFC excercises it
                if newstate:
                    self._debug("Changing the state we'll return to (self._state_stack[-2])")
                    self._debug("  (from %r to %r)" % (self._state_stack[-2], newstate))
                    self._state_stack[-2] = newstate
                return parent


# inspired by recipe 19.18 in the python cookbook. A implementation detail helper for FSMParser.
class Peekable(six.Iterator):

    def __init__(self, iterable):
        self._iterable = iter(iterable)
        self._cache = collections.deque()

    def __iter__(self):
        return self

    def _fillcache(self):
        while len(self._cache) < 1:
            self._cache.append(six.advance_iterator(self._iterable))

    def __next__(self):
        self._fillcache()
        result = self._cache.popleft()
        return result

    # useful alias
    next = __next__

    def peek(self):
        self._fillcache()
        result = self._cache[0]
        return result
