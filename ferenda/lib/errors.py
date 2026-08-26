"""Shared pipeline control signals."""


class SkipDocument(Exception):
    """The document should not be parsed (expired, removed, or empty).
    Raised by a source's extractor; caught by the build driver."""
