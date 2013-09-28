Creating your own document repositories
=======================================

The next step is to do more substantial adjustments to the
download/parse/generate cycle. As the source for our next docrepo
we'll use the `collected RFCs <http://www.ietf.org/rfc.html>`_, 
as published by `IETF <http://www.ietf.org/>`_. These documents are mainly
available in plain text format (formatted for printing on a line
printer), as is the document index itself. This means that we cannot rely 
on the default implementation of download and parse. Furthermore, RFCs are
categorized and refer to each other using varying semantics. This metadata 
can be captured, queried and used in a number of ways to present the RFC
collection in a better way.

.. _implementing-download:

Writing your own ``download`` implementation
--------------------------------------------

The purpose of the
:meth:`~ferenda.DocumentRepository.download` method is to
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
document, use the :meth:`~ferenda.DocumentStore.downloaded_path`
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
:meth:`~ferenda.DocumentRepository.download`
implementation, the main flow of the method is usually to perform some
sort of search or listing, then iterating through the results, finding
the basefile of each document together with the URL of the
document. The helper method
:meth:`~ferenda.DocumentRepository.download_single`
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
this (save as ``rfcs.py``)

   
.. literalinclude:: examples/rfcs.py
  :start-after: # begin download1
  :end-before: # end download1
  
And we'll enable it and try to run it like before:

.. code-block:: sh

  $ ./ferenda-build.py rfcs.RFCs enable
  $ ./ferenda-build.py rfc download
  
This doesn't work! This is because start page contains no actual HTML
links -- it's a plaintext file. However, we CAN do the following::

  $ ./ferenda-build.py rfc download 6725

This is because the default implementation of download delegates the
downloading of individual documents to
:meth:`~ferenda.DocumentRepository.download_single`, which in turn
uses the :data:`~ferenda.DocumentRepository.document_url_template` as
a template to find out that basefile 6725 can be found at
http://tools.ietf.org/rfc/rfc6725.txt

But in order to download *everything* we need to parse the index text
file to find out all available basefiles. In order to do that, we must
override :meth:`~ferenda.DocumentRepository.download`. 

.. literalinclude:: examples/rfcs.py
   :start-after: # begin download2
   :end-before: # end download2

Since the RFC index is a plain text file, we use the
:class:`~ferenda.TextReader` class, which contains a bunch of
functionality to make it easier to work with plain text files. In this
case, we'll iterate through the file one paragraph at a time, and if
the paragraph starts with a four-digit number (and the number hasn't
been marked "Not Issued.") we'll download it by callling
:meth:`~ferenda.DocumentRepository.download_single`.

Like the default implementation, we offload the main work to
:meth:`~ferenda.DocumentRepository.download_single`, which will
look if the file exists on disk and only if not, attempt to download
it. If the ``--refresh`` parameter is provided, a `conditional get
<http://www.w3.org/Protocols/rfc2616/rfc2616-sec9.html#sec9.3>`_ is
performed and only if the server says the document has changed, it is
re-downloaded.

.. note::

   In many cases, the URL for the downloaded document is not easily
   constructed from a basefile
   identifier. :meth:`~ferenda.DocumentRepository.download_single`
   therefore takes a optional url argument. The above could be written
   more verbosely like::

     url = "http://tools.ietf.org/rfc/rfc%s.txt" % basefile
     self.download_single(basefile, url) 

In other cases, a document to be downloaded could consists of several
resources (eg. a HTML document with images, or a PDF document with the
actual content combined with a HTML document with document
metadata). For these cases, you need to override
:meth:`~ferenda.DocumentRepository.download_single`.

Writing your own ``parse`` implementation
-----------------------------------------

The purpose of the
:meth:`~ferenda.DocumentRepository.parse` method is to take
the downloaded file(s) for a particular document and parse it into a
structured document with proper metadata, both for the document as a
whole, but also for individual sections of the document.

.. literalinclude:: examples/rfcs.py
   :start-after: # begin parse1
   :end-before: # end parse1

This implementation builds a very simple object model of a RFC
document, which is serialized to a XHTML1.1+RDFa document by the
:func:`~ferenda.decorators.managedparsing` decorator. If you
run it (by calling ``ferenda-build.py rfc parse --all``) after having
downloaded the rfc documents, the result will be a set of documents in
``data/rfc/parsed``, and a set of RDF files in
``data/rfc/distilled``. Take a look at them! The above might appear to
be a lot of code, but it also accomplishes much. Furthermore, it
should be obvious how to extend it, for instance to create more
metadata from the fields in the header (such as capturing the RFC
category, the publishing party, the authors etc) and better semantic
representation of the body (such as marking up regular paragraphs,
line drawings, bulleted lists, definition lists, EBNF definitions and
so on).

Next up, we'll extend this implementation in two ways: First by
representing the nested nature of the sections and subsections in the
documents, secondly by finding and linking citations/references to
other parts of the text or other RFCs in full.

.. note::

   How does ``./ferenda-build.py rfc parse --all`` work? It calls
   :func:`~ferenda.DocumentStore.list_basefiles_for` with the
   argument ``parse``, which lists all downloaded files, and extracts
   the basefile for each of them, then calls parse for each in turn.


Handling document structure
^^^^^^^^^^^^^^^^^^^^^^^^^^^

The main text of a RFC is structured into sections, which may contain
subsections, which in turn can contain subsubsections. The start of
each section is easy to identify, which means we can build a model of
this structure by extending our parse method with relatively few lines:

.. literalinclude:: examples/rfcs.py
   :start-after: # begin parse2
   :end-before: # end parse2

This enhances parse so that instead of outputting a single long list of elements directly under ``body``:

.. code-block:: xml

    <h1>2.  Overview</h1>
    <h1>2.1.  Date, Location, and Participants</h1>
    <pre>
       The second ForCES interoperability test meeting was held by the IETF
       ForCES Working Group on February 24-25, 2011...
    </pre>
    <h1>2.2.  Testbed Configuration</h1>
    <h1>2.2.1.  Participants' Access</h1>
    <pre>
       NTT and ZJSU were physically present for the testing at the Internet
      Technology Lab (ITL) at Zhejiang Gongshang University in China.
    </pre>
  
...we have a properly nested element structure, as well as much more
metadata represented in RDFa form:

.. code-block:: xml

    <div class="section" property="dct:title" content=" Overview"
         typeof="bibo:DocumentPart" about="http://localhost:8000/res/rfc/6984#S2.">
      <span property="bibo:chapter" content="2."
            about="http://localhost:8000/res/rfc/6984#S2."/>
      <div class="subsection" property="dct:title" content=" Date, Location, and Participants"
           typeof="bibo:DocumentPart" about="http://localhost:8000/res/rfc/6984#S2.1.">
        <span property="bibo:chapter" content="2.1."
              about="http://localhost:8000/res/rfc/6984#S2.1."/>
        <pre>
          The second ForCES interoperability test meeting was held by the
          IETF ForCES Working Group on February 24-25, 2011...
        </pre>
        <div class="subsection" property="dct:title" content=" Testbed Configuration"
             typeof="bibo:DocumentPart" about="http://localhost:8000/res/rfc/6984#S2.2.">
          <span property="bibo:chapter" content="2.2."
                about="http://localhost:8000/res/rfc/6984#S2.2."/>
          <div class="subsubsection" property="dct:title" content=" Participants' Access"
               typeof="bibo:DocumentPart" about="http://localhost:8000/res/rfc/6984#S2.2.1.">
            <span content="2.2.1." about="http://localhost:8000/res/rfc/6984#S2.2.1."
                  property="bibo:chapter"/>
            <pre>
              NTT and ZJSU were physically present for the testing at the
              Internet Technology Lab (ITL) at Zhejiang Gongshang
              University in China...
            </pre>
          </div>
        </div>
      </div>
    </div>

Note in particular that every section and subsection now has a defined
URI (in the ``@about`` attribute). This will be useful later.
    
Handling citations in text
^^^^^^^^^^^^^^^^^^^^^^^^^^

References / citations in RFC text is often of the form ``"are to be
interpreted as described in [RFC2119]"`` (for citations to other RFCs
in whole), ``"as described in Section 7.1"`` (for citations to other
parts of the current document) or ``"Section 2.4 of [RFC2045] says"``
(for citations to a specific part in another document). We can define
a simple grammar for these citations using `pyparsing
<http://pyparsing.wikispaces.com/>`_:

.. literalinclude:: examples/rfcs.py
   :start-after: # begin citation1
   :end-before: # end citation1

The above productions have named results for different parts of the
citation, ie a citation of the form "Section 2.4 of [RFC2045] says"
will result in the named matches Sec = "2.4" and RFC = "2045". The
CitationParser class can be used to extract these matches into a dict,
which is then passed to a uri formatter function like:

.. literalinclude:: examples/rfcs.py
   :start-after: # begin citation2
   :end-before: # end citation2

And to initialize a citation parser and have it run over the entire
structured text, finding citations and formatting them into URIs as we
go along, just use:

.. literalinclude:: examples/rfcs.py
   :start-after: # begin citation3
   :end-before: # end citation3

The result of these lines is that the following block of plain text:

.. code-block:: xml

   <pre>
      The behavior recommended in Section 2.5 is in line with generic error
      treatment during the IKE_SA_INIT exchange, per Section 2.21.1 of
      [RFC5996].
   </pre>

...transform into this hyperlinked text:
   
.. code-block:: xml

   <pre>
      The behavior recommended in <a href="#S2.5"
      rel="dct:references">Section 2.5</a> is in line with generic
      error treatment during the IKE_SA_INIT exchange, per <a
      href="http://localhost:8000/res/rfc/5996#S2.21.1"
      rel="dct:references">Section 2.21.1 of [RFC5996]</a>.
   </pre>

.. note::

   The uri formatting function uses
   :meth:`~ferenda.DocumentRepository.canonical_uri` to create the
   base URI for each external reference. Proper design of the URIs
   you'll be using is a big topic, and you should think through what
   URIs you want to use for your documents and their parts. Ferenda
   provides a default implementation to create URIs from document
   properties, but you might want to override this.

The parse step is probably the part of your application which you'll
spend the most time developing. You can start simple (like above) and
then incrementally improve the end result by processing more metadata,
model the semantic document structure better, and handle in-line
references in text more correctly. See also :doc:`elementclasses`,
:doc:`fsmparser` and :doc:`citationparsing`.

Calling :meth:`~ferenda.DocumentRepository.relate`
-----------------------------------------------------

The purpose of the :meth:`~ferenda.DocumentRepository.relate`
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


Calling :func:`~ferenda.manager.makeresources`
-----------------------------------------------------

This method needs to run at some point before generate and the rest of
the methods. Unlike the other methods described above and below, which
are run for one docrepo at a time, this method is run for the project
as a whole (that is why it is a function in
:mod:`ferenda.manager` instead of a
:class:`~ferenda.DocumentRepository` method). It constructs a set of
site-wide resources such as minified js and css files, and
configuration for the site-wide XSLT template. It is easy to run using
the command-line tool::

  $ ./ferenda-build.py all makeresources

If you use the API, you need to provide a list of instances of the
docrepos that you're using, and the path to where generated resources
should be stored::

  from ferenda.manager import makeresources
  config = {'datadir':'mydata'}
  myrepos = [RFC(**config), W3C(**config]
  makeresources(myrepos,'mydata/myresources')
  

Customizing :meth:`~ferenda.DocumentRepository.generate`
-------------------------------------------------------------------------

The purpose of the
:meth:`~ferenda.DocumentRepository.generate` method is to
create new browser-ready HTML files from the structured XHTML+RDFa
files created by
:meth:`~ferenda.DocumentRepository.parse`. Unlike the files
created by :meth:`~ferenda.DocumentRepository.parse`, these
files will contain site-branded headers, footers, navigation menus and
such. They will also contain related content not directly found in the
parsed files themselves: Sectioned documents will have a
automatically-generated table of contents, and other documents that
refer to a particular document will be listed in a sidebar in that
document. If the references are made to individual sections, there
will be sidebars for all such referenced sections.

The default implementation does this in two steps. In the first,
:meth:`~ferenda.DocumentRepository.prep_annotation_file`
fetches metadata about other documents that relates to the document to
be generated into an *annotation file*. In the second,
:class:`~ferenda.Transformer` runs an
XSLT transformation on the source file (which sources the annotation
file and a configuration file created by
:func:`~ferenda.manager.makeresources`) in order to create the
browser-ready HTML file.

You should not need to override the general
:meth:`~ferenda.DocumentRepository.generate` method, but you might
want to control how the annotation file and the XSLT transformation is
done.

Getting annotations
^^^^^^^^^^^^^^^^^^^

The :meth:`~ferenda.DocumentRepository.prep_annotation_file` step is
driven by a `SPARQL construct query
<http://www.w3.org/TR/rdf-sparql-query/#construct>`_. The default
query fetches metadata about every other document that refers to the
document (or sections thereof) you're generating, using the
``dct:references`` predicate. By setting the class variable
:data:`~ferenda.DocumentRepository.sparql_annotations` to the file
name of SPARQL query file of your choice, you can override this query.

Since our metadata contains more specialized statements on how
document refer to each other, in the form of ``rfc:updates`` and
``rfc:obsoletes`` statements, we want a query that'll fetch this
metadata as well. When we query for metadata about a particular
document, we want to know if there is any other document that updates
or obsoletes this document. Using a CONSTRUCT query, we create
``rfc:isUpdatedBy`` and ``rfc:isObsoletedBy`` references to such
documents.

.. literalinclude:: examples/rfcs.py
   :start-after: # begin annotations
   :end-before: # end annotations

The contents of ``rfc-annotations.rq``, placed in the current
directory, should be:

.. literalinclude:: examples/rfc-annotations.rq

Note that ``%(uri)s`` will be replaced with the URI for the document
we're querying about.

Now, when querying the triplestore for metadata about RFC 6021, the
(abbreviated) result is:

.. code-block:: xml

    <graph xmlns:dct="http://purl.org/dc/terms/"
           xmlns:rfc="http://example.org/ontology/rfc/"
	   xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
      <resource uri="http://localhost:8000/res/rfc/6021">
        <rfc:isObsoletedBy ref="http://localhost:8000/res/rfc/6991"/>
        <dct:published fmt="datatype">
          <date xmlns="http://www.w3.org/2001/XMLSchema#">2010-10-01</date>
        </dct:published>
        <dct:title xml:lang="en">Common YANG Data Types</dct:title>
      </resource>
      <resource uri="http://localhost:8000/res/rfc/6991">
        <a><rfc:RFC/></a>
        <rfc:obsoletes ref="http://localhost:8000/res/rfc/6021"/>
        <dct:published fmt="datatype">
          <date xmlns="http://www.w3.org/2001/XMLSchema#">2013-07-01</date>
        </dct:published>
        <dct:title xml:lang="en">Common YANG Data Types</dct:title>
      </resource>
    </graph>    

.. note::

   You can find this file in ``data/rfc/annotations/6021.grit.xml``. It's
   in the `Grit <http://code.google.com/p/oort/wiki/Grit>`_ format for
   easy inclusion in XSLT processing.
		    
Even if you're not familiar with the format, or with RDF in general,
you can see that it contains information about two resources: first
the document we've queried about (RFC 6021), then the document that
obsoletes the same document (RFC 6991).

.. note::

   If you're coming from a relational database/SQL background, it can
   be a little difficult to come to grips with graph databases and
   SPARQL. The book "Learning SPARQL" by Bob DuCharme is highly
   recommended.

   
Transforming to HTML
^^^^^^^^^^^^^^^^^^^^^

The :class:`~ferenda.Transformer` step is driven by a XSLT
stylesheet. The default stylesheet uses a site-wide configuration file
(created by :func:`~ferenda.manager.makeresources`) for things like
site name and top-level navigation, and lists the document content,
section by section, alongside of other documents that contains
references (in the form of ``dct:references``) for each section. The
SPARQL query and the XSLT stylesheet often goes hand in hand -- if
your stylesheet needs a certain piece of data, the query must be
adjusted to fetch it. By setting he class variable
:data:`~ferenda.DocumentRepository.xslt_template` in the same way as
you did for the SPARQL query, you can override the default.

.. literalinclude:: examples/rfcs.py
   :start-after: # begin xslt
   :end-before: # end xslt

The contents of ``rfc.xsl``, placed in the current
directory, should be:

.. literalinclude:: examples/rfc.xsl
   :language: xml		    

This XSLT stylesheet depends on ``base.xsl`` (which resides in
``ferenda/res/xsl`` in the source distribution of ferenda -- take a
look if you want to know how everything fits together). The main
responsibility of this stylesheet is to format individual elements of
the document body.

``base.xsl`` takes care of the main chrome of the page, and it has a
default implementation (that basically transforms everything from
XHTML1.1 to HTML5, and removes some RDFa-only elements). It also loads
and provides the annotation file in the global variable
$annotations. The above XSLT stylesheet uses this to fetch information
about referencing documents. In particular, when processing an older
document, it lists if later documents have updated or obsoleted it
(see the named template ``aside-annotations``).

You might notice that this XSLT template flattens the nested structure
of sections which we spent so much effort to create in the parse
step. This is to make it easier to put up the aside boxes next to each
part of the document, independent of the nesting level.

.. note::

   While both the SPARQL query and the XSLT stylesheet might look
   complicated (and unless you're a RDF/XSL expert, they are...), most
   of the time you can get a good result using the default generic
   query and stylesheet.
   

Customizing :meth:`~ferenda.DocumentRepository.toc`
--------------------------------------------------------------------

The purpose of the :meth:`~ferenda.DocumentRepository.toc`
method is to create a set of pages that acts as tables of contents for
all documents in your docrepo. For large document collections there
are often several different ways of creating such tables, eg. sorted
by title, publication date, document status, author and similar. The
pages uses the same site-branding,headers, footers, navigation menus
etc used by :meth:`~ferenda.DocumentRepository.generate`.

The default implementation is generic enough to handle most cases, but
you'll have to override other methods which it calls, primarily
:meth:`~ferenda.DocumentRepository.toc_query`,
:meth:`~ferenda.DocumentRepository.toc_criteria` and
:meth:`~ferenda.DocumentRepository.toc_predicates`. These
methods all depend on the metadata you've created by your parse
implementation, but in the simplest cases it's enough to specify that
you want one set of pages organized by the ``dct:title`` of each
document (alphabetically sorted) and another by ``dct:issued``
(numerically/calendarically sorted). The default implementation does
exactly this.

In our case, we wish to create four kinds of sorting: By identifier
(RFC number), by date of issue, by title and by category. These map
directly to four kinds of metadata that we've stored about each and
every document. By overriding
:meth:`~ferenda.DocumentRepository.toc_predicates` we can specify
these four *predicates*.

.. literalinclude:: examples/rfcs.py
   :start-after: # begin toc_predicates
   :end-before: # end toc_predicates

After running toc with this change, you can see that four sets of
index pages are created. However, except for publication year, the
actual partitioning of documents are still done by title. In order to
correct this, we must create a set of :class:`~ferenda.TocCriteria`
objects that specify how documents should be ordered for any
particular criteria, and have
:meth:`~ferenda.DocumentRepository.toc_criteria` return these.

.. literalinclude:: examples/rfcs.py
   :start-after: # begin toc_criteria
   :end-before: # end toc_criteria

The above code gives some example of how :class:`~ferenda.TocCriteria`
objects can be configured. However, a :class:`~ferenda.TocCriteria`
object does not control how each individual document is listed on a
toc page. The default formatting just lists the title of the document,
linked to the document in question. For RFCs, who mainly is referenced
using their RFC number rather than their title, we'd like to add the
RFC number in this display. This is done by overriding
:meth:`~ferenda.DocumentRepository.toc_item`.
		
.. literalinclude:: examples/rfcs.py
   :start-after: # begin toc_item
   :end-before: # end toc_item

Se also :doc:`toc`.

Customizing :meth:`~ferenda.DocumentRepository.news`
---------------------------------------------------------------------
The purpose of :meth:`~ferenda.DocumentRepository.news`,
the next to final step, is to provide a set of news feeds for your document
repository.

The default implementation gives you one single news feed for all
documents in your docrepo, and creates both browser-ready HTML (using
the same headers, footers, navigation menus etc used by
:meth:`~ferenda.DocumentRepository.generate`) and `Atom
syndication format <http://www.ietf.org/rfc/rfc4287.txt>`_ files.

You can specify some basic criteria similar to the way you specified
the organization of your TOC pages, since you might want to split up
the documents in different feeds, for example one feed for each RFC
track.

.. literalinclude:: examples/rfcs.py
   :start-after: # begin news_criteria
   :end-before: # end news_criteria

When running ``news``, this will create five different atom feeds
(which are mirrored as HTML pages) under ``data/rfc/news``: One
containing all documents, and four others that contain documents in a
particular category.
		
.. note::

   As you can see, the resulting HTML pages are a little rough around
   the edges. Also, there isn't currently any way of discovering the
   Atom feeds or HTML pages from the main site -- you need to know the
   URLs. This will all be fixed in due time.
		
Se also :doc:`news`.

Customizing :func:`~ferenda.manager.frontpage`
----------------------------------------------

Finally, :func:`~ferenda.manager.frontpage` creates a front page for
your entire site with content from the different docrepos. Each
docrepos :func:`~ferenda.DocumentRepository.frontpage_content` method
will be called, and should return a XHTML fragment with information
about the repository and it's content. Below is a simple example that
uses functionality we've used in other contexts to create a list of
the five latest documents, as well as a total count of documents.
 
.. literalinclude:: examples/rfcs.py
   :start-after: # begin frontpage_content
   :end-before: # end frontpage_content

Next steps
----------

When you have written code and customized downloading, parsing and all
the other steps, you'll want to run all these steps for all your
docrepos in a single command by using the special value ``all`` for
docrepo, and again ``all`` for action::

    ./ferenda-build.py all all

By now, you should have a basic idea about the key concepts of
ferenda. In the next section, :doc:`keyconcepts`, we'll explore them
further.
    
