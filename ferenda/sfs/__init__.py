"""SFS vertical — consolidated statute text (acts) from rkrattsbaser.

Owns its full chain: body extraction (``extract``) → text reader (``reader``)
→ tokenizer/assembler over the ported recognition heuristics → typed model
(``model``) → golden normal form (``nf``) → register/amendments/metadata
(``register``). The recognition heuristics encode two decades of real-world
SFS formatting quirks; the architecture is new.
"""

import re
from pathlib import Path

from ..lib import compress, patch
from ..lib.errors import SkipDocument
from .assembler import assemble
from .extract import extract_body
from .model import Bilaga
from .parallelappendix import AppendixMisaligned
from .parallelappendix import parse as parse_parallel_appendix
from .reader import TextReader
from .register import (
    parse_register,
    parse_sfst_header,
    register_from_source,
    sfst_header_from_source,
)
from .tokenizer import Tokenizer

# A paragraf marker that the source glued to the end of the paragraf above it:
# "… tillämpas. Lag (2004:197).\n13 e § Kliniska läkemedelsprövningar får …",
# with one newline where the rest of the text has two. The reader splits on the
# blank line, so 13 e § of Lag (1992:859) om läkemedel became the last words of
# 13 d § and stopped existing as a paragraf of its own. The repair recovers 24
# paragrafer in 23 statutes. The provenance marker is what makes it safe: it
# always ends a paragraf, so a paragraf marker on the very next line always
# opens one.
# the separator inside the marker is a plain space or a nbsp, the same two the
# tokenizer's own matchers accept (`tokenizer.flat`) -- the publisher writes
# both, and a repair that knew only one would leave "13 e\xa0§" glued
RE_GLUED_PARAGRAF = re.compile(
    r"(\((?:19|20)\d\d:\d+\)\.)\n(\d+[ \xa0]?[a-z]?[ \xa0]\xa7[ .])")


def _assemble(text, basefile, historical=False):
    # the plain statute text is SFS's intermediate format: apply any curated
    # patch (a correction, or an obfuscated redaction of personal data) here, before
    # the reader tokenises it, so the fix flows into every downstream artifact.
    # `historical` is an archived consolidation (the versions stage), where the
    # statute's own patch is offered to a wording it may predate or postdate --
    # see `patch.apply_if_fits`.
    text = (patch.apply_if_fits("sfs", basefile, text) if historical
            else patch.apply("sfs", basefile, text))
    # after the curated patch, so a patch is still written against the text as
    # the source publishes it
    text = RE_GLUED_PARAGRAF.sub(r"\1\n\n\2", text)
    # A statute that incorporates a convention as a bi-/trilingual parallel-text
    # appendix is recognised by its structure, not its SFS number:
    # parse_parallel_appendix() returns (statute_text, Konventionsbilaga) or None
    # for an ordinary statute. If it looks parallel but doesn't line up across
    # languages it raises AppendixMisaligned; we then flat-parse instead.
    try:
        parsed = parse_parallel_appendix(text)
    except AppendixMisaligned:
        parsed = None
    if parsed is not None:
        statute, bilaga = parsed
        reader = TextReader(statute)
        reader.autostrip = True
        doc = assemble(Tokenizer(reader, basefile))
        doc.children.append(Bilaga("Bilaga", children=[bilaga]))
        return doc
    reader = TextReader(text)
    reader.autostrip = True
    return assemble(Tokenizer(reader, basefile))


def parse_sfs(path, basefile, historical=False):
    """Parse a downloaded SFS HTML file into a Forfattning tree."""
    return _assemble(extract_body(path), basefile, historical)


def parse_sfs_source(source, basefile, historical=False):
    """Parse a downloaded JSON ``_source`` (the new beta API) into a
    Forfattning tree. ``fulltext.forfattningstext`` is already the plain body
    text that extract_body recovers from the legacy HTML."""
    text = source["fulltext"]["forfattningstext"]
    if text is None:
        # the act is in the register but carries no body text: repealed long
        # ago, or published then withdrawn before entering force. Nothing to
        # parse -- a deliberately empty document, not a failure.
        raise SkipDocument("%s: no forfattningstext" % basefile)
    return _assemble(text.replace("\r", ""), basefile, historical)


def input_paths(path):
    """Dispatch a downloaded-document path to ``load_inputs``' three path
    arguments: the new JSON ``_source`` when ``path`` already is one, else the
    legacy SFST HTML with its SFSR register sibling found alongside (by the
    ``/downloaded/`` -> ``/register/`` substitution the old tree layout
    used)."""
    json_path = path if path.suffix == ".json" else None
    html_path = path if path.suffix != ".json" else None
    register_path = (Path(str(path).replace("/downloaded/", "/register/"))
                     if html_path else None)
    return json_path, html_path, register_path


def load_inputs(json_path, html_path, register_path, basefile):
    """Return ``(doc, register, sfst_header)`` for a basefile, preferring the
    new JSON ``_source`` over the legacy SFST+SFSR HTML pages — the DV
    single-best-source-per-document pattern. ``register``/``sfst_header`` are
    None when the legacy register page is absent or empty."""
    if json_path and compress.exists(Path(json_path)):
        source = compress.read_json(Path(json_path))
        return (parse_sfs_source(source, basefile),
                register_from_source(source),
                sfst_header_from_source(source))
    # the JSON source is the input throughout (the legacy HTML fallback is gone);
    # fail loud at the boundary if it is missing rather than passing a None path
    # down to parse_sfs -> extract_body, where it surfaces as an opaque TypeError
    if html_path is None:
        raise FileNotFoundError(
            "no input for %s: JSON source %s absent and no legacy HTML page"
            % (basefile, json_path))
    doc = parse_sfs(html_path, basefile)
    if not compress.exists(Path(register_path)):
        return doc, None, None
    try:
        register = parse_register(register_path)
    except SkipDocument:
        return doc, None, None
    return doc, register, parse_sfst_header(html_path)
