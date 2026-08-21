"""Point the corpus roots at an empty directory for the whole test session.

`lib/layout` derives every stage root (`DOWNLOADED`, `ARTIFACT`, `GENERATED`,
…) from `config.DATA` at *import* time, and `config.DATA` reads the `DATA_ROOT`
environment variable. Setting it here -- before any test module imports
`accommodanda` -- is what keeps a test run off the developer's live corpus.
pytest imports this file first, so the assignment lands in time.

Two reasons this matters, and the speed one is the lesser:

  * **A test must not depend on what is sitting in `site/data/`.** Five tests
    were passing only because the real tree happens to hold the ärende they
    name (`sou/2026-14`): with an empty root they failed, because the code
    under test decides "is this an ärende or one answer?" by asking the
    filesystem. A test that reads the live corpus proves nothing about a
    machine that has not run the pipeline -- CI, or a fresh checkout.
  * **Speed.** `page.Site.from_catalog` walks the whole remisser artifact tree
    (`_remiss_indexes`), which is 80 200 files and 3.8 seconds. Every one of
    the ~90 test_site.py tests that builds a Site paid it, for a corpus none of
    them asserts anything about: 416 s for the suite, against 42 s once the
    root is empty.

A test that needs a corpus builds its own under `tmp_path` and monkeypatches
the roots it needs (`test_remisser_ai_analyze.arende` is the pattern). The
session root stays empty; anything written into it is a test reaching for data
it did not create.
"""

import atexit
import os
import shutil
import tempfile

# a fresh root per session, so nothing a test writes can make the *next* run
# pass for the wrong reason -- the failure mode this file exists to remove
_ROOT = tempfile.mkdtemp(prefix="ferenda-test-data-")
os.environ["DATA_ROOT"] = _ROOT
os.environ["CATALOG_ROOT"] = _ROOT
# the wiki content repo is a sibling checkout, not part of the data root, and a
# developer who has one would otherwise have its begrepp/kommentar markdown read
# into tests that never wrote it
os.environ["WIKI_ROOT"] = os.path.join(_ROOT, "wiki")
# ...and its patches, which live in the same repo (`layout.PATCHES`). The tree
# has to *exist* though: `layout.patch` asserts it does, because an absent tree
# is indistinguishable from "no document has a patch" and would silently drop
# every redaction. An empty one gives each test the same answer -- no patch --
# on a fresh checkout and on a machine that has the real corpus.
os.makedirs(os.path.join(_ROOT, "wiki", "patches"), exist_ok=True)

atexit.register(shutil.rmtree, _ROOT, True)
