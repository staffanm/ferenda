# -*- coding: utf-8 -*-
"""These are the exceptions thrown by Ferenda. Any of the python built-in exceptions may be thrown as well, but exceptions in used third-party libraries should be wrapped in one of these."""
from __future__ import unicode_literals


class ParseError(Exception):

    """Raised when :py:meth:`~ferenda.DocumentRepository.parse` fails in
    any way.
    """


class FSMStateError(ParseError):

    """Raised whenever the current state and the current symbol in a
       :py:class:`~ferenda.FSMParser` configuration does not have a
       defined transition.
    """


class DocumentRemovedError(Exception):

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


class PatchError(ParseError):

    """Raised if a patch cannot be applied by :py:meth:`~ferenda.DocumentRepository.patch_if_needed`."""


class AttachmentNameError(ValueError):

    """Raised whenever an invalid attachment name is used with any method
    of :py:class:`~ferenda.DocumentStore`."""


class AttachmentPolicyError(ValueError):

    """Raised on any attempt to store an attachment using
    :py:class:`~ferenda.DocumentStore` when ``storage_policy`` is not
    set to ``dir``.
    """


class ArchivingError(Exception):

    """Raised whenever an attempt to archive a document version using :py:meth:`~ferenda.DocumentStore.archive` fails (for example, because the archive version 
already exists)."""


class ValidationError(Exception):

    """Raised whenever a created document doesn't validate using the
    appropriate schema."""


class TransformError(Exception):

    """Raised whenever a XSLT transformation fails for any reason."""


class ExternalCommandError(Exception):

    """Raised whenever any invocation of an external commmand fails for
    any reason (including if the command line program doesn't exist)."""


class ConfigurationError(Exception):

    """Raised when a configuration file cannot be found in it's expected
location or when it cannot be used due to corruption, file permissions
or other reasons"""


class TriplestoreError(Exception):

    """Raised whenever communications with the triple store fails, for whatever reason."""


class SparqlError(TriplestoreError):

    """Raised whenever a SPARQL query fails. The Exception should contain whatever error message that the Triple store returned, so the exact formatting may be dependent on which store is used."""


class IndexingError(Exception):

    """Raised whenever an attempt to put text into the fulltext index fails."""


class SearchingError(Exception):

    """Raised whenever an attempt to do a full-text search fails."""
