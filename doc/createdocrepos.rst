
Creating your own document repositories
=======================================

The next step is to do more substantial adjustments to the
download/parse/generate cycle. As the source for our next docrepo
we'll use the collected RFCs, as published by IETF. These are mainly
available in plain text format (formatted for printing on a line
printer), and could stand a little explicit structuring. We'll 

.. _implementing-download:

Writing your own ``download`` implementation
--------------------------------------------

The purpose of the
:py:meth:`~ferenda.DocumentRepository.download` method is to
fetch source documents from a remote source and store them locally,
possibly under different filenames but otherwise bit-for-bit identical
with how they were stored at the remote source.

Each document must have a short internal identifier (the ``basefile``
of the document). This basefile is used when naming of all files
related to that document. When creating a
:class:`~ferenda.DocumentRepository` object, a
:class:`~ferenda.DocumentStore` object is also created and is
available as the ``store`` property. This object is used when storing
or reading any file related to the document.

.. note::

   Right now, ferenda stores all content as normal files on
   disk. Because of this, it's often easiest to retrieve a filename
   using any of the methods in :class:`~ferenda.DocumentStore` and
   then use normal file operations using that filename. However, there
   exists a number of :class:`~ferenda.DocumentStore` methods that
   returns open filehandles (for instance
   :meth:`~ferenda.DocumentStore.open_downloaded`; if your code can be
   written to use these, it will be easier to transition to other
   storage backends such as MongoDB, Amazon S3 or Git, if supported in
   the future.


To find out the location of the downloaded source file for any
document, use the :py:meth:`~ferenda.DocumentStore.downloaded_path`
method. The default implementation (which you can override) is to use
a name like
``<datadir>/<alias>/downloaded/<basefile>.<downloaded_suffix>``, eg
for RFC 6725 in our example ``data/rfc/downloaded/6725.txt``.

.. note::

   Reasons for overriding this naming scheme can be that you have a
   large (over 1000) number of documents, which makes it impractical
   to put them all in the same root directory, or that you wish to use
   basefiles that have characters not allowed in file names (such as
   ``:``)

When writing your own
:py:meth:`~ferenda.DocumentRepository.download`
implementation, the main flow of the method is usually to perform some
sort of search or listing, then iterating through the results, finding
the basefile of each document together with the URL of the
document. The helper method
:py:meth:`~ferenda.DocumentRepository.download_single`
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
:doc:`firststeps`. All RFC documents are listed in the index file at
http://www.ietf.org/download/rfc-index.txt, while a individual
document (such as RFC 6725) are available at
http://tools.ietf.org/rfc/rfc6725.txt. Our first attempt will look like
this (save as rfcs.py)

   
.. literalinclude:: rfcs.py
  :lines: 1-7
  
And we'll enable it and try to run it like before:

.. code-block:: sh

  $ ./ferenda-build.py rfcs.RDFs enable
  $ ./ferenda-build.py rfc download
  
This doesn't work! This is because start page contains no actual HTML
links -- it's a plaintext file. However, we CAN do the following::

  $ ./ferenda-build.py rfc download 6725

This is because the default implementation of download delegates the
downloading of individual documents to
:py:meth:`~ferenda.DocumentRepository.download_single`, which in turn
uses the :py:data:`~ferenda.DocumentRepository.document_url_template` as
a template to find out that basefile 6725 can be found at
http://tools.ietf.org/rfc/rfc6725.txt

But in order to download everything we need to parse the index text
file to find out all available basefiles. In order to do that, we must
override download. 

.. literalinclude:: rfcs.py
  :lines: 9-17

Since the RFC index is a plain text file, we use the
:py:class:`~ferenda.TextReader` class, which contains a bunch of
functionality to make it easier to work with plain text files. In this
case, we'll iterate through the file one paragraph at a time, and if
the paragraph starts with a four-digit number (and the number hasn't
been marked "Not Issued.") we'll download it by callling
:py:meth:`~ferenda.DocumentRepository.download_single`.

.. The default implementation has a added nicety: It records the time
   when the last full download was finished in the ferenda.ini file
   (which is useful when creating the website, to tell visitors how
   recent the content is). You get that functionality for free through
   the :py:func:`~ferenda.DocumentRepository.recordlastdownload`
   decorator.    <-- DOESN'T WORK YET

Like the default implementation, we offload the main work to
:py:meth:`~ferenda.DocumentRepository.download_single`, which will
look if the file exists on disk and only if not, attempt to download
it. If the ``--refresh`` parameter is provided, a `conditional get
<http://www.w3.org/Protocols/rfc2616/rfc2616-sec9.html#sec9.3>`_ is
performed and only if the server says the document has changed, it is
re-downloaded.

.. note::

   In many cases, the URL for the downloaded document is not easily
   constructed from a basefile
   identifier. :py:meth:`~ferenda.DocumentRepository.download_single`
   therefore takes a optional url argument. The above could be written
   more verbosely like::

     url = "http://tools.ietf.org/rfc/rfc%s.txt" % basefile
     self.download_single(basefile, url) 

In other cases, a document to be downloaded could consists of several
resources (eg. a HTML document with images, or a PDF document with the
actual content combined with a HTML document with document
metadata). For these cases, you need to override
:py:meth:`~ferenda.DocumentRepository.download_single`.

Writing your own ``parse`` implementation
-----------------------------------------

The purpose of the
:py:meth:`~ferenda.DocumentRepository.parse` method is to take
the downloaded file(s) for a particular document and parse it into a
structured document with proper metadata, both for the document as a
whole, but also for individual sections of the document.

The parse method does not return anything, but should create a
structured XHTML+RDFa file in the location returned by
:py:meth:`~ferenda.DocumentRepository.parsed_path`

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
:py:func:`~ferenda.DocumentRepository.managedparsing` is
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


Calling :py:meth:`~ferenda.DocumentRepository.relate`
-----------------------------------------------------

The purpose of the :py:meth:`~ferenda.DocumentRepository.relate`
method is to make sure that all document data and metadata is properly
stored and indexed, so that it can be easily retrieved in later
steps. This consists of three steps: Loading all RDF metadata into a
triplestore, loading all document content into a full text index, and
making note of how documents refer to each other.

Since the output of parse is well structured XHTML+RDFa documents
that, on the surface level, do not differ much from docrepo to
docrepo, you should not have to change anything about this step.

.. note::

   You might want to configure whether to load everything into a
   fulltext index -- this operation takes a lot of time, and this
   index is not even used if createing a static site. You do this by
   setting ``fulltextindex`` to ``False``, either in ferenda.ini or on
   the command line::

     ./ferenda-build.py rfc relate --all --fulltextindex=False


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

If you use the API, you need to provide a list of instances of the
docrepos that you're using, and the path to where generated resources
should be stored::

  from ferenda.manager import makeresources
  config = {'datadir':'mydata'}
  myrepos = [RFC(**config), W3C(**config]
  makeresources(myrepos,'mydata/myresources')
  

Calling (and customizing) :py:meth:`~ferenda.DocumentRepository.generate`
-------------------------------------------------------------------------

The purpose of the
:py:meth:`~ferenda.DocumentRepository.generate` method is to
create new browser-ready HTML files from the structured XHTML+RDFa
files created by
:py:meth:`~ferenda.DocumentRepository.parse`. Unlike the files
created by :py:meth:`~ferenda.DocumentRepository.parse`, these
files will contain site-branded headers, footers, navigation menus and
such. They will also contain related content not directly found in the
parsed files themselves: Sectioned documents will have a
automatically-generated table of contents, and other documents that
refer to a particular document will be listed in a sidebar in that
document. If the references are made to individual sections, there
will be sidebars for all such referenced sections.

The default implementation does this in two steps. In the first,
:py:meth:`~ferenda.DocumentRepository.prep_annotation_file`
fetches metadata about other documents that relates to the document to
be generated into an *annotation file*. In the second,
:py:meth:`~ferenda.DocumentRepository.transform_html` runs an
XSLT transformation on the source file (which sources the annotation
file and a configuration file created by
:py:func:`~ferenda.manager.makeresources`) in order to create the
browser-ready HTML file.

You should not need to override the general
:py:meth:`~ferenda.DocumentRepository.generate` method, but you might
want to control how the annotation file and the XSLT transformation is
done.

Getting annotations
^^^^^^^^^^^^^^^^^^^

The :py:meth:`~ferenda.DocumentRepository.prep_annotation_file` step
is driven by a `SPARQL construct query
<http://www.w3.org/TR/rdf-sparql-query/#construct>`_. The default
query fetches metadata about every other document that refers to the
document (or sections thereof) you're generating. Set the class
variable :py:data:`~ferenda.DocumentRepository.sparql_annotations`` to
the file name of SPARQL query file of your choice, if you want to
override the default.

Transforming to HTML
^^^^^^^^^^^^^^^^^^^^^

The :py:meth:`~ferenda.DocumentRepository.transform_html` step is
driven by a XSLT stylesheet. The default stylesheet uses a site-wide
configuration file (created by
:py:func:`~ferenda.manager.makeresources`) for things like site name
and top-level navigation, and lists the document content, section by
section, alongside of other documents that contains references for
each section. set
:py:data:`~ferenda.DocumentRepository.xslt_template`` to the file name
of a XSLT stylesheet of your choice, if you want to override the default.


Calling (and customizing) :py:meth:`~ferenda.DocumentRepository.toc`
--------------------------------------------------------------------

The purpose of the :py:meth:`~ferenda.DocumentRepository.toc`
method is to create a set of pages that acts as tables of contents for
all documents in your docrepo. For large document collections there
are often several different ways of creating such tables, eg. sorted
by title, publication date, document status, author and similar. The
pages uses the same site-branding,headers, footers, navigation menus
etc used by :py:meth:`~ferenda.DocumentRepository.generate`.

The default implementation is generic enough to handle most cases, but
you'll have to override other methods which it calls, primarily
:py:meth:`~ferenda.DocumentRepository.toc_query`,
:py:meth:`~ferenda.DocumentRepository.toc_criteria` and
:py:meth:`~ferenda.DocumentRepository.toc_predicates`. These
methods all depend on the metadata you've created by your parse
implementation, but in the simplest cases it's enough to specify that
you want one set of pages organized by the ``dct:title`` of each
document (alphabetically sorted) and another by ``dct:issued``
(numerically/calendarically sorted). The default implementation does
exactly this.

Se also :doc:`toc`.


Calling (and customizing) :py:meth:`~ferenda.DocumentRepository.news`
---------------------------------------------------------------------
The purpose of :py:meth:`~ferenda.DocumentRepository.news`,
the final step, is to provide a set of news feeds for your document
repository.

The default implementation gives you one single news feed for all
documents in your docrepo, and creates both browser-ready HTML (using
the same headers, footers, navigation menus etc used by
:py:meth:`~ferenda.DocumentRepository.generate`) and `Atom
syndication format <http://www.ietf.org/rfc/rfc4287.txt>`_ files.

You can specify some basic criteria similar to the way you specified
the organization of your TOC pages, since you might want to split up
the documents in different feeds, for example one feed for each RFC
track.

Se also :doc:`news`.

Next steps
----------

When you have written code and customized downloading, parsing and all
the other steps, you'll want to run all these steps for all your
docrepos in a single command by using the special value ``all`` for
docrepo, and again ``all`` for action.

    ./ferenda-build.py all all

By now, you should have a basic idea about the key concepts of
ferenda. In the next section, :doc:`keyconcepts`, we'll explore them
further.
    
