import builtins
import functools
import logging
import os
import tempfile
from contextlib import contextmanager

from ferenda.compat import patch

class TempFileHandler(logging.FileHandler): pass

def quiet():
    """A decorator that ensures that anything called by the decorated
    method won't output anything to the console, either by logging
    statements or calls to print().

    """
    # extra ceremony to work with unittest
    def outer(f, *args, **kwargs):
        @functools.wraps(f)
        def test_wrapper(self):
            state = _setup()
            with patch('builtins.print') as printmock:
                ret = f(self, *args, **kwargs)
            _restore(state)
            return ret
        return test_wrapper
    return outer

@contextmanager
def silence():
    """The same functionality as quiet(), but as a context manager so that
    one can use "with silence:" constructs."""
    state = _setup()
    try:
        with patch('builtins.print') as printmock:
            yield
    finally:
        _restore(state)

def _setup():
    l = logging.getLogger()
    tmp = None
    for h in l.handlers:
        if isinstance(h, logging.StreamHandler):
            break
    else:
        fileno, tmp = tempfile.mkstemp()
        l.addHandler(TempFileHandler(tmp))
        h = logging.StreamHandler()
        h.setLevel(logging.CRITICAL)
        h.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s",
                                         datefmt="%H:%M:%S"))
        l.addHandler(h)
    prevlevel = l.level
    l.setLevel(logging.CRITICAL)
    return prevlevel, tmp, h

def _restore(state):
    prevlevel, tmp, h = state
    l = logging.getLogger()
    # assert l.level == logging.CRITICAL, "Someone messed with the root logger level"
    l.setLevel(prevlevel)
    h.setLevel(prevlevel) # otherwise it'll be the default logging.WARNING i think
    for handler in list(l.handlers):
        if isinstance(handler, TempFileHandler):
            handler.close()
            l.handlers.remove(handler)
    if tmp:
        os.unlink(tmp)
    
