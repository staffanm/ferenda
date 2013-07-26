
Creating your own document repositories
=======================================

Let's try some more substantial adjustments to the
download/parse/generate cycle. An precursor to the standards
publication of W3C are the RFCs. These are mainly available in plain
text format (formatted for printing on a line printer), and could
stand a little explicit structuring.


.. _implementing-download:

Writing your own ``download`` implementation
--------------------------------------------

The purpose of the
:py:meth:`~ferenda.sources.DocumentRepository.download` method is to
fetch source documents from a remote source and store them locally,
under possibly different filenames but otherwise bit-for-bit identical
with how they were stored at the remote source.

Each document must have a short internal identifier (the ``basefile``
of the document). To find out the location of the downloaded source
file for any document, use the
:py:meth:`~ferenda.sources.DocumentRepository.downloaded_path`
method. The default implementation (which you can override) is to use
a name like
``<datadir>/<alias>/downloaded/<basefile>.<downloaded_suffix>``, eg
for RFC 6725 in our example ``data/rfc/downloaded/6725.txt``

.. note::

   Reasons for overriding this naming scheme can be that you have a
   large (over 1000) number of documents, which makes it impractical
   to put them all in the same root directory, or that you need to
   download additional files for each document (such as images or
   stylesheets) and which to put each documents' collections of files
   in a separate directory.

When writing your own
:py:meth:`~ferenda.sources.DocumentRepository.download`
implementation, the main flow of the method is usually to perform some
sort of search or listing, then iterating through the results, finding
the basefile of each document together with the URL of the
document. The helper method
:py:meth:`~ferenda.sources.DocumentRepository.download_single`
performs the actual downloading and places the retrieved file in the
correct location.

In this approach, it's neccessary that the result data contains enough
information to find out the basefile of any document before it's
downloaded. Fortunately, that is almost often the case.

.. note::

   The ``basefile`` is a purely internal identifier; by default it
   shows up in URI paths, but if you want you can hide it entirely
   from public view.

We'll start out by creating a class similar to our W3C class in
:doc:`firststeps`:. All RFC documents are listed in the index file at
http://www.ietf.org/download/rfc-index.txt, while a individual
document (such as RFC 6725) are available at
http://tools.ietf.org/rfc/rfc6725.txt. Our first attempt will look like
this (save as rfcs.py)

   
.. literalinclude:: rfcs.py
  :lines: 1-5
  
And we'll enable it and try to run it like before:

.. code-block:: sh

  $ ./ferenda-build.py rfcs.RDFs enable
  $ ./ferenda-build.py rfc download
  
This doesn't work! This is because start page contains no actual HTML
links -- it's a plaintext file. However, we CAN do the following::

  $ ./ferenda-build.py rfc download 6725

This is because the default implementation of download delegates the
downloading of individual documents to download_single, which in turn
uses the
:py:data:`~ferenda.sources.DocumentRepository.document_url_regex` as a
*template* to find out that basefile 6725 can be found at
http://tools.ietf.org/rfc/rfc6725.txt

But in order to download everything we need to parse the index text
file to find out all available basefiles. In order to do that, we must
override download. 

.. literalinclude:: rfcs.py
  :lines: 7-17

Since the RFC index is a plain text file, we use the
:py:class:`~ferenda.TextReader` class, which contains a bunch of
functionality to make it easier to work with plain text files. In this
case, we'll iterate through the file one paragraph at a time, and if
the paragraph starts with a four-digit number (and the number hasn't
been marked "Not Issued.") we'll download it by callling
:py:meth:`~ferenda.sources.DocumentRepository.download_single`.

The default implementation has a added nicety: It records the time
when the last full download was finished in the ferenda.ini file
(which is useful when creating the website, to tell visitors how
recent the content is). You get that functionality for free trough the
:py:func:`~ferenda.sources.documentrepository.recordlastdownload` decorator.

Like the default implementation, we offload the main work to
:py:meth:`~ferenda.sources.DocumentRepository.download_single`, which will look if the file exists on disk and only
if not, attempt to download it. If the ``--refresh`` parameter is
provided, a `conditional get
<http://www.w3.org/Protocols/rfc2616/rfc2616-sec9.html#sec9.3>`_ is
performed and only if the server says the document has changed, it is
re-downloaded.

.. note::

   In many cases, the URL for the downloaded document is not easily
   constructed from a basefile
   identifier. :py:meth:`~ferenda.sources.DocumentRepository.download_single`
   therefore takes a optional url argument. The above could be written
   more verbosely like::

     url = "http://tools.ietf.org/rfc/rfc%s.txt" % basefile
     self.download_single(basefile, url) 

In other cases, a document to be downloaded could consists of several
resources (eg. a HTML document with images, or a PDF document with the
actual content combined with a HTML document with document
metadata). For these cases, you need to override
:py:meth:`~ferenda.sources.DocumentRepository.download_single`.

Writing your own ``parse`` implementation
-----------------------------------------

The purpose of the
:py:meth:`~ferenda.sources.DocumentRepository.parse` method is to take
the downloaded file(s) for a particular document and parse it into a
structured document with proper metadata, both for the document as a
whole, but also for individual sections of the document.

The parse method does not return anything, but should create a
structured XHTML+RDFa file in the location returned by
:py:meth:`~ferenda.sources.DocumentRepository.parsed_path`

.. code-block:: py

  @managedparsing
  def parse(self, doc):
      # v. simple heuristics
  
      def isheader(p):
          if len(p.split("\n") == 1 and not p.endwith(.):
              return True
  
      def is_pageheader(p):
          return False
  
      def is_pagefooter(p):
          return False
   
      # create body of document
      reader = TextReader(self.downloaded_path(doc.basefile))
      header = reader.readpara()
      title = reader.readpara()
      for para in reader.getiterator(reader.readpara()):
          if is_header(p)
              doc.append(Header(p))
          elif is_pageheader(p) or is_pagefooter(p):
              # just drop these line-printer remnants
              pass
          else:
              doc.append(Preformatted(p)) 
  
      # create metadata for document
      doc.meta.append(dct:title, title)
      pubdate = # find pub date in header
      authors = # find list of authors in header
      doc.meta.append(dct:published, pubdate)
      for author in authors:
          doc.meta.append(dct:author, author)

This implementation builds a very simple object model of a RFC
document, which through the magic of
:py:func:`~ferenda.sources.documentrepository.managedparsing` is
serialized to a XHTML1.1+RDFa doc.

How does ``./ferenda-build.py rfc parse --all`` work? It calls
:py:func:`list_basefiles_for` ("parse") which lists all downloaded
files, and extracts the basefile for each of them, then calls parse
for each in turn.

The parse step is probably the part of your application which you'll
spend the most time developing. You can start simple (like above) and
then incrementally improve the end result by processing more metadata,
model the semantic document structure, and handle in-line references
in text. See also :doc:`elementclasses`, :doc:`fsmparser` and
:doc:`citationparsing`.


(Not) writing your own ``relate`` implementation
------------------------------------------------

The purpose of the
:py:meth:`~ferenda.sources.DocumentRepository.relate` method is to
make sure that all document data and metadata is properly stored and
indexed, so that it can be easily retrieved in later steps. This
consists of two steps: Loading all RDF metadata into a triplestore,
and loading all document content into a full text index.

Since the output of parse is well structured XHTML+RDFa documents
that, on the surface level, do not differ much from docrepo to
docrepo, the default implementation is sufficiently generic that it
probably do not require modification.

.. note::

   If you really think you need to override this method, you can. Just
   take a look at the default implementation and do what you need.


Calling :py:func:`~ferenda.manager.makeresources`
-----------------------------------------------------

This method needs to run at some point before generate and the rest of
the methods. Unlike the other methods described above and below, which
are run for one docrepo at a time, this method is run for the project
as a whole (that is why it is a function in ferenda.manager instead of
a :py:class:`~ferenda.DocumentRepository` method). It constructs a set of site-wide
resources such as minified js and css files, and configuration for the
site-wide XSLT template. It is easy to run using the command-line tool::

  $ ./ferenda-build.py makeresources

If you use the API, you need to specify classes/docrepos that you're
using, by class name (not the actual classes themselves), and where
generated resources should be stored::

  from ferenda.manager import makeresources
  makeresources(['rfcs.RFC','w3cstandards.W3C'],'data/myresources')
  

Writing your own ``generate`` implementation
--------------------------------------------

The purpose of the
:py:meth:`~ferenda.sources.DocumentRepository.generate` method is to
create new browser-ready HTML files from the structured XHTML+RDFa
files created by
:py:meth:`~ferenda.sources.DocumentRepository.parse`. Unlike the files
created by :py:meth:`~ferenda.sources.DocumentRepository.parse`, these
files will contain site-branded headers, footers, navigation menus and
such. They will also contain related content not directly found in the
parsed files themselves: Sectioned documents will have a
automatically-generated table of contents, and other documents that
refer to a particular document will be listed in a sidebar in that
document. If the references are made to individual sections, there
will be sidebars for all such referenced sections.

The default implementation does this in two steps. In the first,
:py:meth:`~ferenda.sources.DocumentRepository.prep_annotation_file`
fetches metadata about other documents that relates to the document to
be generated into an *annotation file*. In the second,
:py:meth:`~ferenda.sources.DocumentRepository.transform_html` runs an
XSLT transformation on the source file (which sources the annotation
file and a configuration file created by
:py:func:`~ferenda.manager.makeresources`) in order to create the
browser-ready HTML file.

Getting annotations
^^^^^^^^^^^^^^^^^^^

The
:py:meth:`~ferenda.sources.DocumentRepository.prep_annotation_file`
step is driven by a `SPARQL construct query
<http://www.w3.org/TR/rdf-sparql-query/#construct>`_. The default query
fetches metadata about every other document that refers to the
document (or sections thereof) you're generating. Create
``annotations.rq`` in your project dir, or set the class variable
``sparql_query`` to the file name of SPARQL query file of your choice,
if you want to override the default.

Transforming to HTML
^^^^^^^^^^^^^^^^^^^^^

The :py:meth:`~ferenda.sources.DocumentRepository.transform_html` step
is driven by a XSLT stylesheet. The default stylesheet uses a
site-wide configuration file (created by
:py:func:`~ferenda.manager.makeresources`) for things like site name
and top-level navigation, and lists the document content, section by
section, alongside of other documents that contains references for
each section. Create default.xsl in your project dir, or set
xslt_template to the file name of a xslt of your choice, if you want
to override the default.


Writing your own ``toc`` implementation
---------------------------------------

The purpose of the :py:meth:`~ferenda.sources.DocumentRepository.toc`
method is to create a set of pages that acts as tables of contents for
all documents in your docrepo. For large document collections there
are often several different ways of creating such tables, eg. sorted
by title, publication date, document status, author and similar. The
pages uses the same site-branding,headers, footers, navigation menus
etc used by :py:meth:`~ferenda.sources.DocumentRepository.generate`.

The default implementation is generic enough to handle most cases, but
you'll have to override other methods which it calls, primarily
:py:meth:`~ferenda.sources.DocumentRepository.toc_query`,
:py:meth:`~ferenda.sources.DocumentRepository.toc_criteria` and
:py:meth:`~ferenda.sources.DocumentRepository.toc_predicates`. These
methods all depend on the metadata you've created by your parse
implementation, but in the simplest cases it's enough to specify that
you want one set of pages organized by the ``dct:title`` of each
document (alphabetically sorted) and another by ``dct:issued``
(numerically/calendarically sorted). The default implementation does
exactly this.

Se also :doc:`toc`.


Writing your own ``news`` implementation
----------------------------------------
The purpose of :py:meth:`~ferenda.sources.DocumentRepository.news`,
the final step, is to provide a set of news feeds for your document
repository.

The default implementation gives you one single news feed for all
documents in your docrepo, and creates both browser-ready HTML (using
the same headers, footers, navigation menus etc used by
:py:meth:`~ferenda.sources.DocumentRepository.generate`) and `Atom
syndication format <http://www.ietf.org/rfc/rfc4287.txt>`_ files.

You can specify some basic criteria similar to the way you specified
the organization of your TOC pages, since you might want to split up
the documents in different feeds, for example one feed for each RFC
track.

By now, you should have a basic idea about the key concepts of
ferenda. In the next section, :doc:`keyconcepts`, we'll explore them
further.
