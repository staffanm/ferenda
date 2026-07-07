"""Direct unit tests for accommodanda.sfs.tokenizer.Tokenizer, targeting
tokenizer-internal edge cases that the fixture-driven test_sfs_parse.py
oracle doesn't exercise well (end-of-data lookahead)."""

from accommodanda.sfs.reader import TextReader
from accommodanda.sfs.tokenizer import OpenAvdelning, Tokenizer

BASEFILE = "9999:998"


def test_trailing_avdelning_heading_is_not_dropped():
    """A avdelning heading with nothing after it must still be emitted: the
    underrubrik lookahead runs past the end of data and used to raise an
    uncaught IOError, which the (former) blanket `except IOError` in
    next_event swallowed as "no more events" -- silently dropping the
    heading."""
    reader = TextReader("FÖRSTA AVDELNINGEN")
    reader.autostrip = True
    events = list(Tokenizer(reader, BASEFILE))
    assert events == [OpenAvdelning(ordinal="1", rubrik="FÖRSTA AVDELNINGEN",
                                    underrubrik=None)]
