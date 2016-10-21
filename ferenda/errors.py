# -*- coding: utf-8 -*-
"""These are the exceptions thrown by Ferenda. Any of the python
built-in exceptions may be thrown as well, but exceptions in used
third-party libraries should be wrapped in one of these."""

from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
from builtins import *


class FerendaException(Exception):
    """Base class for anything that can go wrong in ferenda."""


class DownloadError(FerendaException):
    """Raised when a download fails in a non-recoverable way."""


class DownloadFileNotFoundError(DownloadError):
    """Raised when we had indication that a particular document should
    exist (we have a basefile for it) but on closer examination, it
    turns that it doesn't exist after all. This is used when we
    can't raise a requests.exceptions.HTTPError 404 error for some
    reason."""
    pass

class ParseError(FerendaException):

    """Raised when :py:meth:`~ferenda.DocumentRepository.parse` fails in
    any way.
    """


class FSMStateError(ParseError):
    """Raised whenever the current state and the current symbol in a
       :py:class:`~ferenda.FSMParser` configuration does not have a
       defined transition.
    """


class DocumentRemovedError(FerendaException):
    """Raised whenever a particular document has been found to be removed
    -- this can happen either during
    :py:meth:`~ferenda.DocumentRepository.download` or
    :py:meth:`~ferenda.DocumentRepository.parse` (which may be the
    case if there exists a physical document, but whose contents are
    essentially a placeholder saying that the document has been
    removed).

    You can set the attribute ``dummyfile`` on this exception when
    raising it, preferably to the parsed_path that would be created,
    if not this exception had occurred.. If present,
    ``ferenda-build.py`` (or rather :meth:`ferenda.manager.run`) will
    use this to create a dummy file at the indicated path. This
    prevents endless re-parsing of expired documents.

    """
    def __init__(self, value="", dummyfile=None):
        super(DocumentRemovedError, self).__init__(value)
        self.dummyfile = dummyfile


class DocumentSkippedError(DocumentRemovedError):
    """Raised if the document should not be processed (even though it may
    exist) since it's not interesting."""


class PatchError(ParseError):
    """Raised if a patch cannot be applied by
:py:meth:`~ferenda.DocumentRepository.patch_if_needed`."""


class NoDownloadedFileError(ParseError):
    """Raised on an attempt to parse a basefile for which there doesn't
exist a downloaded file."""


class InvalidTree(ParseError):
    """Raised when the parsed XHTML tree fails internal validation."""


class AttachmentNameError(ValueError):
    """Raised whenever an invalid attachment name is used with any method
    of :py:class:`~ferenda.DocumentStore`."""


class AttachmentPolicyError(ValueError):
    """Raised on any attempt to store an attachment using
    :py:class:`~ferenda.DocumentStore` when ``storage_policy`` is not
    set to ``dir``.
    """


class ArchivingError(FerendaException):
    """Raised whenever an attempt to archive a document version using
    :py:meth:`~ferenda.DocumentStore.archive` fails (for example,
    because the archive version already exists).

    """


class ValidationError(FerendaException):
    """Raised whenever a created document doesn't validate using the
    appropriate schema."""


class TransformError(FerendaException):
    """Raised whenever a XSLT transformation fails for any reason."""


class ExternalCommandError(FerendaException):
    """Raised whenever any invocation of an external commmand fails for
    any reason."""


class ExternalCommandNotFound(FerendaException):
    """Raised whenever any invocation of an external commmand fails """


class ConfigurationError(FerendaException):
    """Raised when a configuration file cannot be found in it's expected
location or when it cannot be used due to corruption, file permissions
or other reasons"""


class TriplestoreError(FerendaException):
    """Raised whenever communications with the triple store fails, for
    whatever reason."""


class SparqlError(TriplestoreError):
    """Raised whenever a SPARQL query fails. The Exception should contain
       whatever error message that the Triple store returned, so the
       exact formatting may be dependent on which store is used.

    """


class IndexingError(FerendaException):
    """Raised whenever an attempt to put text into the fulltext index fails."""


class SearchingError(FerendaException):
    """Raised whenever an attempt to do a full-text search fails."""


class SchemaConflictError(FerendaException):
    """Raised whenever a fulltext index is opened with repo arguments that
       result in a different schema than what's currently in
       use. Workaround this by removing the fulltext index and
       recreating.

    """


class SchemaMappingError(FerendaException):
    """Raised whenever a given field in a schema cannot be mapped to or
       from the underlying native field object in an actual
       fulltextindex store.

    """


class MaxDownloadsReached(FerendaException):
    """Raised whenever a recursive download operation has reached a
    globally set maximum number of requests.

    """
    pass


class ResourceNotFound(FerendaException):
    """Raised when :py:class:`~ferenda.ResourceLoader` method is called
    with the name of a non-existing resource. """
    pass

class PDFFileIsEmpty(FerendaException):
    """Raised when
    :py:class:`~ferenda.pdfreader.StreamingPDFReader.convert` tries to
    parse the textual content of a PDF, but finds that it has no text
    information (maybe because it only contains scanned images).

    """

class RequestHandlerError(FerendaException):
    """Raised when :py:class:`~ferenda.RequestHandler` attempts to handle
    an incoming request that it thinks it can support, but fails."""

