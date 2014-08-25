# Copyright 2013 semantics GmbH
# Written by Marcus Brinkmann <m.brinkmann@semantics.de>

from __future__ import print_function, division
from __future__ import absolute_import, unicode_literals

class SemanticsState(dict):
    # Internalize frozen states to conserve memory.
    _intern = dict()

    @classmethod
    def _to_hashable(obj):
        # Converts lists and values in dicts.
        if isinstance(obj, list):
            return tuple(_convert(i) for i in obj)
        elif isinstance(obj, dict):
            return frozenset((i, _convert(j)) for i, j in obj.items())
        else:
            return obj

    def __init__(self, state):
        # Allowed is a frozenset with atomic or tuple values.
        def _convert(obj):
            if isinstance(obj, tuple):
                return list(obj)
            else:
                return obj
        if state is not None:
            things = [(key, _convert(value)) for key, value in state]
            super(SemanticsState, self).__init__(things)
        else:
            super(SemanticsState, self).__init__()

    def as_hashable(self):
        def _convert(obj):
            if isinstance(obj, list):
                if len(obj) == 0:
                    return None
                else:
                    return tuple(obj)
            elif isinstance(obj, int):
                if obj == 0:
                    return None
                else:
                    return obj
            else:
                return obj

        things = [(key, _convert(value)) for key, value in self.items()]
        things = [(key, value) for key, value in things if value is not None]
        if len(things) == 0:
            return None
        state = frozenset(things)
        cached_state = SemanticsState._intern.get(state, None)
        if cached_state is not None:
            return cached_state
        else:
            self._intern[state] = state
            return state

    def increment(self, name):
        cur = self.get(name, 0)
        self[name] = cur + 1

    def decrement(self, name):
        cur = self.get(name, 0)
        self[name] = cur - 1

    def push_to(self, name, item):
        cur = self.get(name, None)
        if cur is None:
            self[name] = [item]
        else:
            cur.append(item)

    def pop_from(self, name):
        cur = self.get(name, None)
        # assert(cur is not None)
        if cur is not None:
            return cur.pop()
        return None

    def peek_at(self, name):
        cur = self.get(name, None)
        # assert(cur is not None)
        if cur is not None:
            return cur[-1]
        return None

    def get_list(self, name):
        return self.get(name, [])


