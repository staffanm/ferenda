"""POI-backed extraction of legacy Word files (.doc/.docx) into a flat paragraph
stream. Shared machinery: the DV vertical reads court-decision Word bodies and
the förarbete vertical reads proptrips/regeringen-era Word bodies through it.

The binary Word 97-2003 format (.doc) and the OOXML format (.docx) are read
through Apache POI (HWPF / XWPF) over jpype into a *single* flat paragraph
stream -- (text, bold, in_table) -- recovering the label/value table structure
and the bold run markers that antiword's DocBook conversion flattened. Callers
map that stream onto their own model (DV's referat head/body, förarbete's Para
classify).

The JVM never runs inside the calling process. This module is a thin client:
``read()`` lazily spawns one persistent ``poi_worker`` subprocess (which owns
jpype and the JVM) and speaks line-delimited JSON with it over its pipes, so a
build worker's address space stays free of the _jpype C extension and the JVM's
threads/signal handlers. One worker per process, amortizing JVM startup over a
whole legacy corpus; it exits on stdin EOF when its parent goes away.

The jars are not committed (`vendor/poi/*.jar`, gitignored); fetch once with
`tools/fetch_poi.sh`, which populates the repo-root `vendor/poi/` the worker
globs. A JVM (Java 9+, jpype's floor; the README pins openjdk-21-jdk-headless)
must be discoverable -- jpype auto-finds `libjvm.so`.
"""

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# Word's field-result placeholder, emitted for empty form fields (Avdelning,
# Domsnummer …). It carries no value, so we strip it to empty.
_FORMTEXT = "FORMTEXT"

# A .doc field: \x13 field-begin, then the field *instruction* (" FORMTEXT ",
# " REF foo "), \x14 separator, then the displayed *result*, \x15 field-end.
# Only the result is document text; the instruction (and a resultless field)
# is dropped. Applied repeatedly so nested fields unwrap inside-out.
_FIELD_INSTRUCTION = re.compile("\x13[^\x13\x14\x15]*[\x14\x15]")

_OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_ZIP_MAGIC = b"PK\x03\x04"


@dataclass
class Para:
    text: str
    bold: bool
    in_table: bool


def _clean(text):
    # \x07 is the HWPF cell/row terminator bell; drop it, the form-field
    # placeholder, and CRs, then collapse remaining whitespace.
    text = text.replace("\x07", "").replace("\r", "").replace(_FORMTEXT, "")
    while "\x13" in text:
        text, n = _FIELD_INSTRUCTION.subn("", text)
        if not n:
            break   # unbalanced field-begin: strip the stray marker below
    # \x01/\x02/\x05 are embedded-object / annotation anchors; stray field
    # markers survive an unbalanced field.
    text = re.sub("[\x01\x02\x05\x13\x14\x15]", "", text)
    return " ".join(text.split())


_WORKER: subprocess.Popen[str] | None = None


def _worker():
    global _WORKER
    if _WORKER is None or _WORKER.poll() is not None:
        # stderr inherited: JVM/POI diagnostics surface on the build's stderr
        # instead of vanishing (the worker also re-points the JVM's stdout at
        # stderr so stray Java prints can never corrupt the JSON protocol)
        _WORKER = subprocess.Popen(
            [sys.executable, "-m", "accommodanda.lib.poi_worker"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    return _WORKER


def read(path):
    """A legacy Word file -> ordered list[Para], extracted in the persistent
    ``poi_worker`` subprocess. Dispatches on file magic here so junk input
    fails fast without ever spawning a JVM."""
    path = Path(path)
    magic = path.read_bytes()[:8]
    if not magic.startswith((_ZIP_MAGIC, _OLE2_MAGIC)):
        raise ValueError("%s: neither OLE2 (.doc) nor ZIP (.docx): %r" % (path, magic))
    proc = _worker()
    assert proc.stdin is not None and proc.stdout is not None
    proc.stdin.write(json.dumps(str(path)) + "\n")
    proc.stdin.flush()
    line = proc.stdout.readline()
    if not line:
        # the worker segfaulted or was killed mid-request; its stderr already
        # went to ours. Raise (not assert): this must fail identically under
        # `python -O` (rule:errors-drive-retry-use-raise), and the next read()
        # respawns a fresh worker via the poll() check.
        raise RuntimeError("poi worker died while reading %s" % path)
    reply = json.loads(line)
    if "error" in reply:
        raise RuntimeError("poi worker failed on %s: %s" % (path, reply["error"]))
    return [Para(text, bold, in_table) for text, bold, in_table in reply["paras"]]
