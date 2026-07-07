"""SFS vertical — consolidated statute text (acts) from rkrattsbaser.

Owns its full chain: body extraction (``extract``) → text reader (``reader``)
→ tokenizer/assembler over the ported recognition heuristics → typed model
(``model``) → golden normal form (``nf``) → register/amendments/metadata
(``register``). The recognition heuristics encode two decades of real-world
SFS formatting quirks; the architecture is new.
"""

import json
from pathlib import Path

from ..lib.errors import SkipDocument
from .assembler import assemble
from .extract import extract_body
from .reader import TextReader
from .register import (
    parse_register,
    parse_sfst_header,
    register_from_source,
    sfst_header_from_source,
)
from .tokenizer import Tokenizer


def _assemble(text, basefile):
    reader = TextReader(text)
    reader.autostrip = True
    return assemble(Tokenizer(reader, basefile))


def parse_sfs(path, basefile):
    """Parse a downloaded SFS HTML file into a Forfattning tree."""
    return _assemble(extract_body(path), basefile)


def parse_sfs_source(source, basefile):
    """Parse a downloaded JSON ``_source`` (the new beta API) into a
    Forfattning tree. ``fulltext.forfattningstext`` is already the plain body
    text that extract_body recovers from the legacy HTML."""
    text = source["fulltext"]["forfattningstext"]
    if text is None:
        # the act is in the register but carries no body text: repealed long
        # ago, or published then withdrawn before entering force. Nothing to
        # parse -- a deliberately empty document, not a failure.
        raise SkipDocument("%s: no forfattningstext" % basefile)
    return _assemble(text.replace("\r", ""), basefile)


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
    if Path(json_path).exists():
        source = json.loads(Path(json_path).read_text())
        return (parse_sfs_source(source, basefile),
                register_from_source(source),
                sfst_header_from_source(source))
    doc = parse_sfs(html_path, basefile)
    if not Path(register_path).exists():
        return doc, None, None
    try:
        register = parse_register(register_path)
    except SkipDocument:
        return doc, None, None
    return doc, register, parse_sfst_header(html_path)
