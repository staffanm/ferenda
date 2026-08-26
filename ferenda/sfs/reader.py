"""Line/paragraph-oriented text reader with lookahead.

Minimal reimplementation of the subset of ferenda.TextReader that the SFS
tokenizer needs. Paragraphs are chunks separated by blank lines; peeks past
end of data raise IOError (callers use that to detect "no more lookahead").
"""


class TextReader:
    def __init__(self, data, linesep="\n"):
        self.data = data
        self.linesep = linesep
        self.currpos = 0
        self.maxpos = len(data)
        self.autostrip = False
        self.expandtabs = True

    def _process(self, s):
        if self.autostrip:
            s = s.strip()
        if self.expandtabs:
            s = s.expandtabs(8)
        return s

    def _find(self, delimiter, startpos):
        idx = self.data.find(delimiter, startpos)
        if idx == -1:
            return self.data[startpos:], self.maxpos
        return self.data[startpos:idx], idx + len(delimiter)

    def eof(self):
        return self.currpos == self.maxpos

    def read(self, size):
        res = self.data[self.currpos:self.currpos + size]
        self.currpos += len(res)
        return self._process(res)

    def readline(self):
        res, self.currpos = self._find(self.linesep, self.currpos)
        return self._process(res)

    def peek(self, size):
        return self._process(self.data[self.currpos:self.currpos + size])

    def readparagraph(self):
        # NB: the blank-line skipping is processed through autostrip, so it
        # only happens when autostrip is OFF (inside tables): with
        # autostrip on, a paragraph read at a blank-line position returns
        # "" -- old-reader behavior that the golden corpus reflects (e.g.
        # an empty first stycke when an old-style "§ 6." marker is a line
        # of its own)
        while self.peek(len(self.linesep)) == self.linesep:
            self.currpos += len(self.linesep)
        res, self.currpos = self._find(self.linesep * 2, self.currpos)
        while self.peek(len(self.linesep)) == self.linesep:
            self.currpos += len(self.linesep)
        return self._process(res)

    def peekline(self, times=1):
        return self._peekchunk(self.linesep, self.currpos, times)

    def peekparagraph(self, times=1):
        pos = self.currpos
        while self._process(self.data[pos:pos + len(self.linesep)]) == \
                self.linesep:
            pos += len(self.linesep)
        return self._peekchunk(self.linesep * 2, pos, times)

    def _peekchunk(self, delimiter, pos, times):
        for _ in range(times):
            res, newpos = self._find(delimiter, pos)
            if newpos == pos:
                raise IOError("Peek past end of data")
            pos = newpos
        return self._process(res)
