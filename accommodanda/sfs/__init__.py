"""SFS vertical — consolidated statute text (acts) from rkrattsbaser.

Owns its full chain: body extraction (``extract``) → text reader (``reader``)
→ tokenizer/assembler over the ported recognition heuristics → typed model
(``model``) → golden normal form (``nf``) → register/amendments/metadata
(``register``). The recognition heuristics encode two decades of real-world
SFS formatting quirks; the architecture is new.
"""

import json
from pathlib import Path

from ..lib import compress, patch
from ..lib.errors import SkipDocument
from .assembler import assemble
from .atmf import parse_appendix as parse_atmf_appendix
from .atmf import split_appendix as split_atmf_appendix
from .crc import parse_appendix as parse_crc_appendix
from .crc import split_appendix as split_crc_appendix
from .echr import parse_appendix, split_appendix
from .extract import extract_body
from .model import Bilaga
from .montreal import parse_appendix as parse_montreal_appendix
from .montreal import split_appendix as split_montreal_appendix
from .reader import TextReader
from .register import (
    parse_register,
    parse_sfst_header,
    register_from_source,
    sfst_header_from_source,
)
from .tokenizer import Tokenizer


def _assemble(text, basefile):
    # the plain statute text is SFS's intermediate format: apply any curated
    # patch (a correction, or a rot13 redaction of personal data) here, before
    # the reader tokenises it, so the fix flows into every downstream artifact.
    text = patch.apply("sfs", basefile, text)
    # Convention incorporation statutes route by basefile plus an asserted
    # printed-format marker. Older ECHR SFST generations contain overlapping
    # temporal variants of the whole appendix (and, in the oldest, no Bilaga
    # marker at all), so only its modern explicit-1-§ shape takes this path.
    # This is format dispatch, not an error fallback: after selection, the
    # language/provision/paragraph invariants must all hold.
    convention_parser = None
    if basefile == "1994:1219" and text.lstrip().startswith("1 §"):
        convention_parser = (split_appendix, parse_appendix)
    elif basefile == "2018:1197":
        convention_parser = (split_crc_appendix, parse_crc_appendix)
    elif basefile == "2010:510" and "\nCONVENTION FOR THE UNIFICATION" in text:
        convention_parser = (split_montreal_appendix, parse_montreal_appendix)
    elif basefile == "2022:366" and "\nRègles uniformes concernant" in text:
        convention_parser = (split_atmf_appendix, parse_atmf_appendix)
    if convention_parser:
        split, parse = convention_parser
        statute, appendix = split(text)
        reader = TextReader(statute)
        reader.autostrip = True
        doc = assemble(Tokenizer(reader, basefile))
        doc.children.append(Bilaga("Bilaga", children=[parse(appendix)]))
        return doc
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
    if json_path and compress.exists(Path(json_path)):
        source = json.loads(compress.read_text(Path(json_path)))
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
