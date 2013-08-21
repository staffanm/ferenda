#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""These are the exceptions thrown by Ferenda. Any of the python built-in exceptions may be thrown as well, but exceptions in used third-party libraries should be wrapped in one of these."""

from __future__ import unicode_literals
class ParseError(Exception):
    """Raised when :py:meth:`~ferenda.DocumentRepository.parse` fails in
    any way.
    """
    pass

class FSMStateError(ParseError):
    """Raised whenever the current state and the current symbol in a
       :py:class:`~ferenda.FSMParser` configuration does not have a
       defined transition.
    """
    pass

class DocumentRemovedError(Exception):
    """Raised whenever a particular document has been found to be removed
    -- this can happen either during
    :py:meth:`~ferenda.DocumentRepository.download` or
    :py:meth:`~ferenda.DocumentRepository.parse` (which may be the
    case if there exists a physical document, but whose contents are
    essentially a placeholder saying that the document has been
    removed).
    """
    pass

class PatchError(ParseError):
    """Raised if a patch cannot be applied by :py:meth:`~ferenda.DocumentRepository.patch_if_needed`."""
    pass

class AttachmentNameError(ValueError):
    """Raised whenever an invalid attachment name is used with any method
    of :py:class:`~ferenda.DocumentStore`."""
    pass

class AttachmentPolicyError(ValueError): 
    """Raised on any attempt to store an attachment using
    :py:class:`~ferenda.DocumentStore` when ``storage_policy`` is not
    set to ``dir``.
    """
    pass

class ArchivingError(Exception):
    """Raised whenever an attempt to archive a document version using :py:meth:`~ferenda.DocumentStore.archive` fails (for example, because the archive version 
already exists)."""
    pass

class ValidationError(Exception):
    """Raised whenever a created document doesn't validate using the
    appropriate schema."""
    pass
    
class TransformError(Exception):
    """Raised whenever a XSLT transformation fails for any reason."""
    pass

class ExternalCommandError(Exception):
    """Raised whenever any invocation of an external commmand fails for
    any reason (including if the command line program doesn't exist)."""
    pass

class ConfigurationError(Exception):
    """Raised when a configuration file cannot be found in it's expected
location or when it cannot be used due to corruption, file permissions
or other reasons"""
    pass


class TriplestoreError(Exception):
    """Raised whenever communications with the triple store fails, for whatever reason."""
    pass
    
class SparqlError(TriplestoreError):
    """Raised whenever a SPARQL query fails. The Exception should contain whatever error message that the Triple store returned, so the exact formatting may be dependent on which store is used."""
    pass

class IndexingError(Exception):
    """Raised whenever an attempt to put text into the fulltext index fails."""
    pass
class SearchingError(Exception):
    """Raised whenever an attempt to do a full-text search fails."""
