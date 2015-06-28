# -*- coding: utf-8 -*-
import bisect
import re
from collections import namedtuple


hunk_head_re = re.compile((r'^@@ -(?P<a1>\d+)(?:,(?P<a2>\d+))? \+(?P<b1>\d+)'
                           r'(?:,(?P<b2>\d+))? @@'
                           r'(?:(?:\s+(?P<comment>\S.*))|)'))

patch_metadata_re = re.compile(r'^(?P<name>diff|index) (?P<data>.*)$')

patch_head_re = re.compile((r'^(?P<direction>---|\+\+\+) (?P<filename>[^\t]+?)'
                            r'(?:(?:\t+|\s{2,})(?P<comment>.*)|)$'))

statement_re = re.compile(r'^(?P<symbol> |-|\+)(?P<text>.*?)$')


class PatchSyntaxError(Exception):
    """Thrown whenever the parser chokes on a malformed unified diff."""
    pass


class PatchConflictError(Exception):
    """Thrown by the hunk merge logic whenever the merge failed."""
    pass


class LineEnumerator(object):

    def __init__(self, lines):
        self.lines_enum = enumerate(lines, start=1)
        self.line = None
        self.line_no = 0

    def __iter__(self):
        return self

    def __next__(self):
        self.line_no, self.line = next(self.lines_enum)
        return self.line
    next = __next__

class Hunk(object):
    """Collection of operations concerning an isolated file chunk."""

    (OP_DELETE, OP_EQUAL, OP_INSERT, ) = range(-1, 2)

    operation_symbol_map = {'+': OP_INSERT,
                            '-': OP_DELETE,
                            ' ': OP_EQUAL, }

    Operation = namedtuple('Operation', ('symbol', 'text', ))

    def __init__(self, source_range, target_range, comment=None):
        self.source_range = source_range
        self.target_range = target_range
        self.comment = comment
        self.operations = []

    def __lt__(self, r_side):
        return self.source_range[0] < r_side.source_range[1]

    def add_operation(self, symbol, text):
        if symbol not in (self.OP_DELETE, self.OP_EQUAL, self.OP_INSERT, ):
            symbol = self.operation_symbol_map[symbol]
        self.operations.append(self.Operation(symbol, text))

    def merge(self, lines):
        """Merge Hunk into `lines`.

        :param lines: isolated collection of lines on which the
                      hunk should be applied to.
        :type lines: generator
        :raises: :class:`PatchConflictError`
        """
        if not hasattr(lines, 'next'):
            lines = iter(lines)

        for symbol, text in self.operations:
            if symbol in (Hunk.OP_EQUAL, Hunk.OP_DELETE, ):
                try:
                    line = lines.next()
                except StopIteration:
                    raise PatchConflictError('Unexpected end of stream')

                if line != text:
                    raise PatchConflictError('patch conflict')
                if symbol == Hunk.OP_EQUAL:
                    yield line

            elif symbol == Hunk.OP_INSERT:
                yield text


class Patch(object):
    """Collection of Hunks concerning a single file."""

    def __init__(self, source_filename, target_filename,
                 source_comment=None, target_comment=None, metadata=None):
        self.source_filename = source_filename
        self.target_filename = target_filename
        self.source_comment = source_comment
        self.target_comment = target_comment
        self.metadata = metadata
        self.hunks = []

    def merge(self, lines):
        """Merges entire hunk collection into `lines`.

        :param lines: collection of lines on which the patch should be applied.
        :type lines: generator
        :raises: :class:`PatchConflictError`
        """
        lines_enumerator = LineEnumerator(lines)
        for hunk in self.hunks:
            while lines_enumerator.line_no < hunk.source_range[0] - 1:
                yield lines_enumerator.next()

            for line in hunk.merge(lines_enumerator):
                yield line

        for line in lines_enumerator:
            yield line


class PatchSet(object):
    """Collection of Patches."""

    def __init__(self):
        self.patches = []

    def __len__(self):
        return len(self.patches)

    def __getitem__(self, item):
        return self.patches[item]

    @classmethod
    def from_stream(cls, in_stream):
        """Reads from `in_stream` and return the parsed patch set.

        :param in_stream: stream containing a patch set.
        :type in_stream: file
        """
        reader = PatchSetReader()
        reader.feed(iter(in_stream.readline, ''))
        return reader.patch_set


class PatchSetReader(object):
    """Stateful reader parsing arbitrary patches and hunks."""

    def __init__(self):
        self._patch_source_buffer = None
        self._metadata_buffer = []
        self._active_patch_set = None
        self._active_patch = None
        self._active_hunk = None

    def __parse_to_dict(self, re_obj, line):
        match_o = re_obj.match(line)
        if not match_o:
            return {}

        return match_o.groupdict()

    def __handle_patch(self, patch_dict):
        self._active_hunk = None

        direction = patch_dict.get('direction')
        if direction == '---':
            self._active_patch = None
            if self._patch_source_buffer is not None:
                raise PatchSyntaxError('duplicate source information')
            self._patch_source_buffer = patch_dict
            return

        if self._patch_source_buffer is None:
            raise PatchSyntaxError('missing source information')

        self._active_patch = Patch(self._patch_source_buffer['filename'],
                                   patch_dict['filename'],
                                   self._patch_source_buffer['comment'],
                                   patch_dict['comment'],
                                   self._metadata_buffer)
        self.patch_set.patches.append(self._active_patch)

        self._patch_source_buffer = None
        self._metadata_buffer = []

    def __handle_hunk(self, hunk_dict):
        if not self._active_patch:
            raise PatchSyntaxError('Missing current patch')

        a_range = (int(hunk_dict['a1']), int(hunk_dict['a2']), )
        b_range = (int(hunk_dict['b1']), int(hunk_dict['b2']), )
        self._active_hunk = Hunk(a_range, b_range, hunk_dict.get('comment'))
        bisect.insort_right(self._active_patch.hunks, self._active_hunk)

    def __handle_statement(self, statement_dict):
        if not self._active_hunk:
            raise PatchSyntaxError('Missing current hunk')
        self._active_hunk.add_operation(statement_dict['symbol'],
                                        statement_dict['text'])

    @property
    def patch_set(self):
        """:class:`PatchSet` containing parsed content.

        :rtype: :class:`PatchSet`
        """
        if not self._active_patch_set:
            self._active_patch_set = PatchSet()
        return self._active_patch_set

    def feed(self, lines):
        """Parses set of by newline separated lines describing a patch set.

        :param lines: collection of newline terminated lines
        :type lines: generator
        :raises: :class:`PatchSyntaxError`
        """
        for line in lines:
            if not line.strip('\n'):
                continue

            metadata_dict = self.__parse_to_dict(patch_metadata_re, line)
            if metadata_dict:
                self._metadata_buffer.append((metadata_dict['name'],
                                              metadata_dict['data'], ))
                continue

            patch_dict = self.__parse_to_dict(patch_head_re, line)
            if patch_dict:
                self.__handle_patch(patch_dict)
                continue

            hunk_dict = self.__parse_to_dict(hunk_head_re, line)
            if hunk_dict:
                self.__handle_hunk(hunk_dict)
                continue

            statement_dict = self.__parse_to_dict(statement_re, line)
            if statement_dict:
                self.__handle_statement(statement_dict)
                continue

            raise PatchSyntaxError('Unreadable content')
