# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import os
import codecs
import copy

import six


class TextReader(object):

    """Fancy file-like-class for reading (not writing) text files by line,
    paragraph, page or any other user-defined unit of text, with
    support for peeking ahead and looking backwards. It can read
    files with byte streams using different encodings, but
    converts/handles everything to real strings (unicode in python
    2). Alternatively, it can be initialized from an existing
    string.

    :param filename: The file to read
    :type filename: str
    :param encoding: The encoding used by the file (default ``ascii``)
    :type encoding: str
    :param string: Alternatively, a string used for initialization
    :type string: str
    :param linesep: The line separators used in the file/string
    :type linesep: str
    """

    def __init__(self, filename=None, encoding=None, string=None, linesep=None):
        if not filename and not string:
            raise TypeError("Must specify either filename or string")

        # implementation of file attributes
        self.closed = False
        self.mode = "r+"
        self.name = filename
        self.newlines = None
        self.softspace = 0

        if encoding:
            self.encoding = encoding
        else:
            self.encoding = 'ascii'

        # Other initialization
        if linesep:
            self.linesep = linesep
        else:
            self.linesep = os.linesep

        # can be changed through getiterator, if we want to iterate over anything else but lines
        self.iterfunc = self.readline
        self.iterargs = []
        self.iterkwargs = {}
        self.autostrip = False
        self.autodewrap = False
        self.autodehyphenate = False
        self.expandtabs = True
        if filename:
            self.f = codecs.open(self.name, "r", self.encoding)
            self.data = self.f.read()
            self.f.close()
        else:
            assert(isinstance(string, six.text_type))
            self.data = string

        self.currpos = 0
        self.maxpos = len(self.data)
        self.lastread = ''

    UNIX = '\n'
    """Unix line endings, for use with the ``linesep`` parameter."""
    DOS = '\r\n'
    """Dos/Windows line endings, for use with the ``linesep`` parameter."""
    MAC = '\r'
    """Old-style Mac line endings, for use with the ``linesep`` parameter."""

    def __iter__(self):
        # self.iterfunc = self.readline
        return self

    def __find(self, delimiter, startpos):
        idx = self.data.find(delimiter, startpos)
        if idx == -1:  # not found, read until eof
            res = self.data[startpos:]
            newpos = startpos + len(res)
        else:
            res = self.data[startpos:idx]
            newpos = idx + len(delimiter)
        return (res, newpos)

    def __rfind(self, delimiter, startpos):
        idx = self.data.rfind(delimiter, 0, startpos)
        if idx == -1:  # not found, read until bof
            res = self.data[:startpos]
            newpos = 0
        else:
            res = self.data[idx + len(delimiter):startpos]
            newpos = idx
        return (res, newpos)

    def __process(self, s):
        if self.autostrip:
            s = self.__strip(s)
        if self.autodewrap:
            s = self.__dewrap(s)
        if self.autodehyphenate:
            s = self.__dehyphenate(s)
        if self.expandtabs:
            s = self.__expandtabs(s)
        return s

    def __strip(self, s):
        return s.strip()

    def __dewrap(self, s):
        return s.replace(self.linesep, " ")

    def __dehyphenate(self, s):
        return s  # FIXME: implement

    def __expandtabs(self, s):
        return s.expandtabs(8)

    #----------------------------------------------------------------
    # Added convenience methods

    def eof(self):
        """Returns True iff current seek position is at end of file."""
        return (self.currpos == self.maxpos)

    def bof(self):
        """Returns True iff current seek position is at begining of file."""
        return (self.currpos == 0)

    def cue(self, string):
        """Set seek position at the beginning of *string*, starting at current seek position. Raises IOError if *string* not found."""
        idx = self.data.find(string, self.currpos)
        if idx == -1:
            raise IOError("Could not find %r in file" % string)
        self.currpos = idx

    def cuepast(self, string):
        """Set seek position at the beginning of *string*, starting at current seek position. Raises IOError if *string* not found."""
        self.cue(string)
        self.currpos += len(string)

    def readto(self, string):
        """Read and return all text between current seek potition and *string*. Sets new seek position at the start of *string*.  Raises IOError if *string* not found."""
        idx = self.data.find(string, self.currpos)
        if idx == -1:
            raise IOError("Could not find %r in file" % string)
        res = self.data[self.currpos:idx]
        self.currpos = idx
        return self.__process(res)

    def readparagraph(self):
        """Reads and returns the next paragraph (all text up to
two or more consecutive line separators)."""
        # consume any leading newlines
        while self.peek(len(self.linesep)) == self.linesep:
            self.currpos += len(self.linesep)

        # read actual paragrapgh
        res = self.readchunk(self.linesep * 2)
        # consume any trailing lines
        while self.peek(len(self.linesep)) == self.linesep:
            self.currpos += len(self.linesep)

        # print("readparagraph: %r" % res[:40])
        return res

    def readpage(self):
        """Reads and returns the next page (all text up to next form feed, ``"\\f"``)"""

        return self.readchunk('\f')  # form feed - pdftotext generates
                                    # these to indicate page breaks
                                    # (other ascii oriented formats,
                                    # like the GPL, RFCs and even some
                                    # python source code, uses it as
                                    # well)

    def readchunk(self, delimiter):
        """Reads and returns the next chunk of text up to *delimiter*"""
        (self.lastread, self.currpos) = self.__find(delimiter, self.currpos)
        return self.__process(self.lastread)

    def lastread(self):
        """Returns the last chunk of data that was actually read (i.e. the ``peek*`` and ``prev*`` methods do not affect this)"""
        return self.__process(self.lastread)

    def peek(self, size=0):
        """Works like :meth:`~ferenda.TextReader.read`, but does not affect current seek position."""
        res = self.data[self.currpos:self.currpos + size]
        return self.__process(res)

    def peekline(self, times=1):
        """Works like :meth:`~ferenda.TextReader.readline`, but does not affect current seek position. If *times* is specified, peeks that many lines ahead."""
        return self.peekchunk(self.linesep, times)

    def peekparagraph(self, times=1):
        """Works like :meth:`~ferenda.TextReader.readparagraph`, but does not affect current seek position. If *times* is specified, peeks that many paragraphs ahead."""
        startpos = self.currpos
        # consume any leading newlines
        while self.peek(len(self.linesep)) == self.linesep:
            self.currpos += len(self.linesep)

        # read actual paragrapgh
        res = self.peekchunk(self.linesep * 2, times)

        # print("peekparagraph: %r" % res[:40])
        self.currpos = startpos
        return res

    def peekchunk(self, delimiter, times=1):
        """Works like :meth:`~ferenda.TextReader.readchunk`, but does not affect current seek position. If *times* is specified, peeks that many chunks ahead."""
        oldpos = self.currpos
        for i in range(times):
            (res, newpos) = self.__find(delimiter, oldpos)
            # print "peekchunk: newpos: %s, oldpos: %s" % (newpos,oldpos)
            if newpos == oldpos:
                raise IOError("Peek past end of file")
            else:
                oldpos = newpos
        return self.__process(res)

    def prev(self, size=0):
        """Works like :meth:`~ferenda.TextReader.read`, but reads backwards from current seek position, and does not affect it."""
        res = self.data[self.currpos - size:self.currpos]
        return self.__process(res)

    def prevline(self, times=1):
        """Works like :meth:`~ferenda.TextReader.readline`, but reads backwards from current seek position, and does not affect it. If *times* is specified, reads the line that many times back."""
        return self.prevchunk(self.linesep, times)

    def prevparagraph(self, times=1):
        """Works like :meth:`~ferenda.TextReader.readparagraph`, but reads backwards from current seek position, and does not affect it. If *times* is specified, reads the paragraph that many times back."""
        return self.prevchunk(self.linesep * 2, times)

    def prevchunk(self, delimiter, times=1):
        """Works like :meth:`~ferenda.TextReader.readchunk`, but reads backwards from current seek position, and does not affect it. If *times* is specified, reads the chunk that many times back."""
        oldpos = self.currpos
        for i in range(times):
            (res, newpos) = self.__rfind(delimiter, oldpos)
            if newpos == oldpos:
                raise IOError("Prev (backwards peek) past end of file")
            else:
                oldpos = newpos
        return self.__process(res)

    def getreader(self, callableObj, *args, **kwargs):
        """Enables you to treat the result of any single ``read*``, ``peek*``
        or ``prev*`` methods as a new TextReader. Particularly useful to
        process individual pages in page-oriented documents::

            filereader = TextReader("rfc822.txt")
            firstpagereader = filereader.getreader(filereader.readpage)
            # firstpagereader is now a standalone TextReader that only
            # contains the first page of text from rfc822.txt
            filereader.seek(0) # reset current seek position
            page5reader = filereader.getreader(filereader.peekpage, times=5)
            # page5reader now contains the 5th page of text from rfc822.txt

        """
        res = callableObj(*args, **kwargs)
        clone = copy.copy(self)
        clone.data = res
        clone.currpos = 0
        clone.maxpos = len(clone.data)
        return clone

    def getiterator(self, callableObj, *args, **kwargs):
        """Returns an
        iterator::

            filereader = TextReader("dashed.txt")
            # dashed.txt contains paragraphs separated by "----"
            for para in filereader.getiterator(filereader.readchunk, "----"):
                print(para)
        """
        self.iterfunc = callableObj
        self.iterargs = args
        self.iterkwargs = kwargs
        return self

    #----------------------------------------------------------------
    # Implementation of a file-like interface
    def flush(self):
        """See :py:meth:`io.IOBase.flush`. This is a no-op."""

    def read(self, size=0):
        """See :py:meth:`io.TextIOBase.read`."""
        self.lastread = self.data[self.currpos:self.currpos + size]
        self.currpos += len(self.lastread)
        return self.__process(self.lastread)

    def readline(self, size=None):
        """See :py:meth:`io.TextIOBase.readline`.

        .. note::

           The ``size`` parameter is not supported."""
        # FIXME: the size arg is required for file-like interfaces,
        # but we don't support it
        return self.readchunk(self.linesep)

    def seek(self, offset, whence=0):
        """See :py:meth:`io.TextIOBase.seek`.

        .. note::

           The ``whence`` parameter is not supported."""
        self.currpos = offset

    def tell(self):
        """See :py:meth:`io.TextIOBase.tell`."""
        return self.currpos

    def write(str):
        """See :py:meth:`io.TextIOBase.write`.

        .. note::

           Always raises IOError, as TextReader is a read-only object."""
        return IOError("TextReaders are read-only")

    def writelines(sequence):
        """See :py:meth:`io.IOBase.writelines`.

        .. note::

           Always raises IOError, as TextReader is a read-only object."""
        return IOError("TextReaders are read-only")

    def __next__(self):
        oldpos = self.currpos
        # res = self.__process(self.readline())
        # print "self.iterfunc is %r" % self.iterfunc
        res = self.__process(self.iterfunc(*self.iterargs, **self.iterkwargs))
        if self.currpos == oldpos:
            raise StopIteration
        else:
            return res

    # alias for py2 compat
    next = __next__
    """Backwards-compatibility alias for iterating over a file in python
2. Use :meth:`~ferenda.TextReader.getiterator` to make iteration work over anything other
than lines (eg paragraphs, pages, etc).

    """
