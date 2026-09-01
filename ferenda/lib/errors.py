"""Shared pipeline control signals."""


class SkipDocument(Exception):
    """The document should not be parsed (expired, removed, or empty).
    Raised by a source's extractor; caught by the build driver."""


class RebuildRequired(ValueError):
    """A history-as-git export's current corpus would change history rather
    than extending it (sfs.asgit, eurlex.asgit) -- the caller must pass
    --rebuild-history rather than silently rewriting or losing a commit."""
