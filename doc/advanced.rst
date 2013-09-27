Advanced topics
===============


Composite docrepos
------------------

In some cases, a document collection may available from multiple
sources, with varying degrees of completeness and/or quality. For
example, in a collection of US patents, some patents may be available
in structured XML with good metadata through a easy-to-use API, some
in tag-soup style HTML with no metadata, requiring screenscraping, and
some in the form of TIFF files that you scanned yourself. The
implementation of both download() and parse() will differ wildly for
these sources. You'll have something like this:

.. literalinclude:: examples/patents.py
   :start-after: # begin subrepos
   :end-before: # end subrepos

But since the result of all three parse() implementations are
XHTML1.1+RDFa documents (possibly with varying degrees of data
fidelity), the implementation of generate() will be substantially the
same. Furthermore, you probably want to present a unified document
collection to the end user, presenting documents derived from
structured XML if they're available, documents derived from tagsoup
HTML if an XML version wasn't available, and finally documents derived
from your scanned documents if nothing else is available.

The class :class:`~ferenda.CompositeRepository` makes this
possible. You specify a number of subordinate docrepo classes using
the ``subrepos`` class property.

.. literalinclude:: examples/patents.py
   :start-after: # begin composite
   :end-before: # end composite
  
The CompositeRepository docrepo then acts as a proxy for all of your
specialized repositories::

.. literalinclude:: examples/composite-repository.sh

Note that ``patents.XMLPatents`` and the other subrepos are never
registered in ferenda.ini``. They're just called behind-the-scenes by
``patents.CompositePatents``.


Patch files
-----------

It is not uncommon that source documents in a document repository
contains formatting irregularities, sensitive information that must be
redacted, or just outright errors. In some cases, your parse
implementation can detect and correct these things, but in other
cases, the irregularities are so uncommon or unique that this is not
possible to do in a general way.

As an alternative, you can patch the source document (or it's
intermediate representation) before the main part of your parsing
logic.

The method :meth:`~ferenda.DocumentRepository.patch_if_needed`
automates most of this work for you. It expects a basefile and the
corresponding source document as a string, looks in a *patch
directory* for a corresponding patch file, and applies it if found.

By default, the patch directory is alongside the data directory. The
patch file for document foo in repository bar should be placed in
``patches/bar/foo.patch``. An optional description of the patch (as a
plaintext, UTF-8 encoded file) can be placed in
``patches/bar/foo.desc``.


:meth:`~ferenda.DocumentRepository.patch_if_needed` returns a tuple
(text, description). If there was no available patch, text is
identical to the text passed in and description is None. If there was
a patch available and it applied cleanly, text is the patched text and
description is a description of the patch (or "(No patch description
available)"). If there was a patch, but it didn't apply cleanly, a
PatchError is raised.

.. note:: 

   There is a ``mkpatch`` command in the Devel class which aims to
   automate the creation of patch files. It does not work at the
   moment.


External annotations
--------------------

Ferenda contains a general docrepo class that fetches data from a
separate MediaWiki server and stores this as annotations/descriptions
related to the documents in your main docrepos. This makes it possible
to present a source document and commentary on it (including
annotations about individual sections) side-by-side.

See :class:`ferenda.sources.general.MediaWiki`


Keyword hubs
------------

Ferenda also contains a general docrepo class that lists all keywords
used by documents in your main docrepos (by default, it looks for all
``dct:subject`` properties used in any document) and generate
documents for each of them. These documents have no content of their
own, but act as hub pages that list all documents that use a certain
keyword in one place.

When used together with the MediaWiki module above, this makes it
possible to write editorial descriptions about each keyword used, that
is presented alongside the list of documents that use that keyword.

See :class:`ferenda.sources.general.Keyword`
