"""lib/poi's subprocess boundary: Java runs only in the poi_worker child,
never in the calling process.

The extraction content itself is covered by test_dv_legacy (Para/_clean) and
the golden corpus; these tests lock the *isolation* -- the calling process
must stay free of the _jpype C extension (a JVM in-process was a prime
suspect in the 2026-07 GC-segfault hunt) -- and the client's error paths.
"""

import subprocess
import sys
from pathlib import Path

import pytest

from accommodanda.lib import poi

JARS = sorted((Path(__file__).parents[1] / "vendor" / "poi").glob("*.jar"))


def test_importing_build_loads_no_native_bridges():
    # a fresh interpreter, because this test process may legitimately have
    # imported anything; the claim is about what build.py *itself* drags in
    code = ("import sys; import accommodanda.build; "
            "bad = [m for m in ('_jpype', 'greenlet') if m in sys.modules]; "
            "sys.exit(repr(bad) if bad else 0)")
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert proc.returncode == 0, \
        "build.py imported native bridge extensions: %s" % proc.stderr


def test_read_rejects_junk_without_spawning_worker(tmp_path):
    junk = tmp_path / "junk.doc"
    junk.write_bytes(b"dummy data, not a Word file")
    before = poi._WORKER
    with pytest.raises(ValueError, match="neither OLE2"):
        poi.read(junk)
    assert poi._WORKER is before, "junk input must not cost a JVM subprocess"


@pytest.mark.skipif(not JARS, reason="POI jars not fetched (tools/fetch_poi.sh)")
def test_read_extracts_via_subprocess_only():
    paras = poi.read(Path(__file__).parent / "files" / "wordreader" / "sample.doc")
    assert paras[0] == poi.Para("Document title", True, False)
    assert "_jpype" not in sys.modules, "the JVM bridge leaked into the client"
    assert poi._WORKER is not None and poi._WORKER.poll() is None


@pytest.mark.skipif(not JARS, reason="POI jars not fetched (tools/fetch_poi.sh)")
def test_worker_survives_a_failed_document(tmp_path):
    # a Java-side failure (Word 95 is too old for HWPF) surfaces as a
    # RuntimeError naming the file, and the persistent worker keeps serving
    word95 = Path(__file__).parent / "files" / "forarbete-legacy" / "proptrips_word95.doc"
    with pytest.raises(RuntimeError, match="proptrips_word95"):
        poi.read(word95)
    paras = poi.read(Path(__file__).parent / "files" / "wordreader" / "sample.docx")
    assert paras[0].text == "Document title"
