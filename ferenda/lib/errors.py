"""Shared pipeline control signals."""


class SkipDocument(Exception):
    """The document should not be parsed (expired, removed, or empty).
    Raised by a source's extractor; caught by the build driver."""


class RebuildRequired(ValueError):
    """A history-as-git export's current corpus would change history rather
    than extending it (sfs.asgit, eurlex.asgit) -- the caller must pass
    --rebuild-history rather than silently rewriting or losing a commit."""


class UpstreamChanged(ValueError):
    """A source's page, feed, archive or file no longer has the shape its
    downloader reads -- a listing that parses to no rows, a heading block that
    is gone, an advertised count the harvest did not reach, a body whose magic
    bytes are not what the link promised.

    A raise, never an `assert` (rule:errors-drive-retry-use-raise): the check
    is about *upstream* content, so it has to survive `python -O`. An assert
    that vanishes there turns "the site changed" into a wrong record written
    to the corpus -- an HTML error page stored as a `.pdf`, an incomplete case
    list recorded as complete.

    `assert` stays for invariants whose failure means this program is wrong:
    a subprocess this module started, a type its own caller passes.
    """
