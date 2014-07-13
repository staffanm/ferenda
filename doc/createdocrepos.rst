=========================================
 Creating your own document repositories
=========================================

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
============================================

The purpose of the :meth:`~ferenda.DocumentRepository.download` method
is to fetch source documents from a remote source and store them
locally, possibly under different filenames but otherwise bit-for-bit
identical with how they were stored at the remote source (see
:ref:`file-storage` for more information about how and where files are
stored locally).

The default implementation of
:meth:`~ferenda.DocumentRepository.download` uses a small number of
methods and class variables to do the actual work. By selectively
overriding these, you can often avoid rewriting a complete
implementation of :meth:`~ferenda.DocumentRepository.download`.

A simple example
----------------

We'll start out by creating a class similar to our W3C class in
:doc:`firststeps`. All RFC documents are listed in the index file at
http://www.ietf.org/download/rfc-index.txt, while a individual
document (such as RFC 6725) are available at
http://tools.ietf.org/rfc/rfc6725.txt. Our first attempt will look
like this (save as ``rfcs.py``)

   
.. literalinclude:: examples/rfcs.py
  :start-after: # begin download1
  :end-before: # end download1
  
And we'll enable it and try to run it like before:

.. code-block:: sh

  $ ./ferenda-build.py rfcs.RFCs enable
  $ ./ferenda-build.py rfc download
  
This doesn't work! This is because start page contains no actual HTML
links -- it's a plaintext file. We need to parse the index text file
to find out all available basefiles. In order to do that, we must
override :meth:`~ferenda.DocumentRepository.download`.

.. literalinclude:: examples/rfcs.py
   :start-after: # begin download2
   :end-before: # end download2

Since the RFC index is a plain text file, we use the
:class:`~ferenda.TextReader` class, which contains a bunch of
functionality to make it easier to work with plain text files. In this
case, we'll iterate through the file one paragraph at a time, and if
the paragraph starts with a four-digit number (and the number hasn't
been marked "Not Issued.") we'll download it by calling
:meth:`~ferenda.DocumentRepository.download_single`.

Like the default implementation, we offload the main work to
:meth:`~ferenda.DocumentRepository.download_single`, which will look
if the file exists on disk and only if not, attempt to download it. If
the ``--refresh`` parameter is provided, a `conditional get
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


The main flow of the download process
-------------------------------------

The main flow is that the :meth:`~ferenda.DocumentRepository.download`
method itself does some source-specific setup, which often include
downloading some sort of index or search results page. The location of
that index resource is given by the class variable
:data:`~ferenda.DocumentRepository.start_url`.
:meth:`~ferenda.DocumentRepository.download` then calls
:meth:`~ferenda.DocumentRepository.download_get_basefiles` which
returns an iterator of basefiles.


For each basefile, :meth:`~ferenda.DocumentRepository.download_single`
is called. This method is responsible for downloading everything
related to a single document. Most of the time, this is just a single
file, but can occasionally be a set of files (like a HTML document
with accompanying images, or a set of PDF files that conceptually is a
single document).

The default implementation of
:meth:`~ferenda.DocumentRepository.download_single` assumes that a
document is just a single file, and calculates the URL of that
document by calling the :meth:`~ferenda.DocumentRepository.remote_url`
method.

The default :meth:`~ferenda.DocumentRepository.remote_url` method uses
the class variable
:data:`~ferenda.DocumentRepository.document_url_template`. This string
template should be using string formatting and expect a variable
called ``basefile``. The default implementation of
:meth:`~ferenda.DocumentRepository.remote_url` can in other words only
be used if the URLs of the remote source are predictable and directly
based on the ``basefile``.

.. note::

   In many cases, the URL for the remote version of a document can be
   impossible to calculate from the basefile only, but be readily
   available from the main index page or search result page. For those
   cases, :meth:`~ferenda.DocumentRepository.download_get_basefiles`
   should return a iterator that yields ``(basefile, url)``
   tuples. The default implementation of
   :meth:`~ferenda.DocumentRepository.download` handles this and uses
   ``url`` as the second, optional argument to download_single.

Finally, the actual downloading of individual files is done by the
:meth:`~ferenda.DocumentRepository.download_if_needed` method. As the
name implies, this method tries to avoid downloading anything from the
network if it's not strictly needed.  If there is a file in-place
already, a conditional GET is done (using the timestamp of the file
for a ``If-modified-since`` header, and an associated .etag file for a
``If-none-match`` header). This avoids re-downloading the (potentially
large) file if it hasn't changed.

To summarize: The main chain of calls looks something like this::

    download
      start_url (class variable)
      download_get_basefiles (instancemethod) - iterator 
      download_single (instancemethod)
         remote_url (instancemethod)
             document_url_template (class variable)
         download_if_needed (instancemethod)


These are the methods that you may override, and when you might want to do so:


====================== ==================================  ==================================
method                 Default behaviour                   Override when
====================== ==================================  ==================================
download               Download the contents of            All your documents are not linked 
                       ``start_url`` and extracts all      from a single index page (i.e. paged
                       links by ``lxml.html.iterlinks``,   search results). In these cases, you 
                       which are passed to                 should override  
                       ``download_get_basefiles``.         ``download_get_basefiles`` as well
                       For each item that is returned,     and make that method responsible for
                       call download_single.               fetching all pages of search results.
---------------------- ----------------------------------  ----------------------------------
download_get_basefiles Iterate through the (element,       The basefile/url extraction is more
                       attribute, link, url) tuples from   complicated than what can be achieved
                       the source and examine if link      through the ``basefile_regex`` /
                       matches ``basefile_regex`` or if    ``document_url_regex`` mechanism, or 
                       url match ``document_url_regex``.   when you've overridden download to 
                       If so, yield a                      pass a different argument than a 
                       (text, url) tuple.                  link iterator. Note that you must
		                                           return an iterator by using the 
		                                           ``yield`` statement for each basefile
							   found.
---------------------- ----------------------------------  ----------------------------------
download_single        Calculates the url of the document  The complete contents of your 
                       to download (or, if a URL is        document is contained in several
                       provided, uses that), and calls     different files. In these cases, you
                       ``download_if_needed`` with that.   should start with the main one and
                       Afterwards, updates the             call ``download_if_needed`` for that,
                       ``DocumentEntry`` of the document   then calculate urls and file paths
                       to reflect source url and download  (using the ``attachment`` parameter to
                       timestamps.                         ``store.downloaded_path``) for each
                                                           additional file, then call
                                                           ``download_if_needed`` for each. Finally,
							   you must update the ``DocumentEntry``
							   object.
---------------------- ----------------------------------  ----------------------------------
remote_url             Calculates a URL from a basename    The rules for producing a URL from a
                       using ``document_url_template``     basefile is more complicated than 
                                                           what string formatting can achieve.
---------------------- ----------------------------------  ----------------------------------
download_if_needed     Downloads an individual URL to a    You really shouldn't. 
                       local file. Makes sure the local 
                       file has the same timestamp as the
                       Last-modified header from the
                       server. If an older version of the
                       file is present, this can either 
                       be archived (the default) or
                       overwritten.             
====================== ==================================  ==================================


The optional basefile argument
------------------------------

During early stages of development, it's often useful to just download
a single document, both in order to check out that download_single
works as it should, and to have sample documents for parse. When using
the ferenda-build.py tool, the download command can take a single
optional parameter, ie.::

    ./ferenda-build.py rfc download 6725

If provided, this parameter is passed to the download method as the
optional basefile parameter.  The default implementation of download
checks if this parameter is provided, and if so, simply calls
download_single with that parameter, skipping the full download
procedure. If you're overriding download, you should support this
usage, by starting your implementation with something like this::

    def download(self, basefile=None):
        if basefile:
            return self.download_single(basefile)

        # the rest of your code


The :func:`~ferenda.decorators.downloadmax` decorator
-----------------------------------------------------

As we saw in :doc:`intro`, the built-in docrepos support a
``downloadmax`` configuration parameter. The effect of this parameter
is simply to interrupt the downloading process after a certain amount
of documents have been downloaded. This can be useful when doing
integration-type testing, or if you just want to make it easy for
someone else to try out your docrepo class. The separation between the
main :meth:`~ferenda.DocumentRepository.download` method anbd the
:meth:`~ferenda.DocumentRepository.download_get_basefiles` helper
method makes this easy -- just add the
``@``:func:`~ferenda.decorators.downloadmax` to the latter. This
decorator reads the ``downloadmax`` configuration parameter (it also
looks for a ``FERENDA_DOWNLOADMAX`` environment variable) and if set,
limits the number of basefiles returned by
:meth:`~ferenda.DocumentRepository.download_get_basefiles`.
	

Writing your own ``parse`` implementation
=========================================

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
---------------------------

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

    <div class="section" property="dcterms:title" content=" Overview"
         typeof="bibo:DocumentPart" about="http://localhost:8000/res/rfc/6984#S2.">
      <span property="bibo:chapter" content="2."
            about="http://localhost:8000/res/rfc/6984#S2."/>
      <div class="subsection" property="dcterms:title" content=" Date, Location, and Participants"
           typeof="bibo:DocumentPart" about="http://localhost:8000/res/rfc/6984#S2.1.">
        <span property="bibo:chapter" content="2.1."
              about="http://localhost:8000/res/rfc/6984#S2.1."/>
        <pre>
          The second ForCES interoperability test meeting was held by the
          IETF ForCES Working Group on February 24-25, 2011...
        </pre>
        <div class="subsection" property="dcterms:title" content=" Testbed Configuration"
             typeof="bibo:DocumentPart" about="http://localhost:8000/res/rfc/6984#S2.2.">
          <span property="bibo:chapter" content="2.2."
                about="http://localhost:8000/res/rfc/6984#S2.2."/>
          <div class="subsubsection" property="dcterms:title" content=" Participants' Access"
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
--------------------------

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
      rel="dcterms:references">Section 2.5</a> is in line with generic
      error treatment during the IKE_SA_INIT exchange, per <a
      href="http://localhost:8000/res/rfc/5996#S2.21.1"
      rel="dcterms:references">Section 2.21.1 of [RFC5996]</a>.
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
==================================================

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
==============================================

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
========================================================

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
-------------------

The :meth:`~ferenda.DocumentRepository.prep_annotation_file` step is
driven by a `SPARQL construct query
<http://www.w3.org/TR/rdf-sparql-query/#construct>`_. The default
query fetches metadata about every other document that refers to the
document (or sections thereof) you're generating, using the
``dcterms:references`` predicate. By setting the class variable
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

    <graph xmlns:dcterms="http://purl.org/dc/terms/"
           xmlns:rfc="http://example.org/ontology/rfc/"
	   xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
      <resource uri="http://localhost:8000/res/rfc/6021">
        <rfc:isObsoletedBy ref="http://localhost:8000/res/rfc/6991"/>
        <dcterms:published fmt="datatype">
          <date xmlns="http://www.w3.org/2001/XMLSchema#">2010-10-01</date>
        </dcterms:published>
        <dcterms:title xml:lang="en">Common YANG Data Types</dcterms:title>
      </resource>
      <resource uri="http://localhost:8000/res/rfc/6991">
        <a><rfc:RFC/></a>
        <rfc:obsoletes ref="http://localhost:8000/res/rfc/6021"/>
        <dcterms:published fmt="datatype">
          <date xmlns="http://www.w3.org/2001/XMLSchema#">2013-07-01</date>
        </dcterms:published>
        <dcterms:title xml:lang="en">Common YANG Data Types</dcterms:title>
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
--------------------

The :class:`~ferenda.Transformer` step is driven by a XSLT
stylesheet. The default stylesheet uses a site-wide configuration file
(created by :func:`~ferenda.manager.makeresources`) for things like
site name and top-level navigation, and lists the document content,
section by section, alongside of other documents that contains
references (in the form of ``dcterms:references``) for each section. The
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
===================================================

The purpose of the :meth:`~ferenda.DocumentRepository.toc`
method is to create a set of pages that acts as tables of contents for
all documents in your docrepo. For large document collections there
are often several different ways of creating such tables, eg. sorted
by title, publication date, document status, author and similar. The
pages uses the same site-branding,headers, footers, navigation menus
etc used by :meth:`~ferenda.DocumentRepository.generate`.

The default implementation is generic enough to handle most cases, but
you'll have to override other methods which it calls, primarily
:meth:`~ferenda.DocumentRepository.facets` and
:meth:`~ferenda.DocumentRepository.toc_item`. These methods
depend on the metadata you've created by your parse implementation,
but in the simplest cases it's enough to specify that you want one set
of pages organized by the ``dcterms:title`` of each document
(alphabetically sorted) and another by ``dcterms:issued``
(numerically/calendarically sorted). The default implementation does
exactly this.

In our case, we wish to create four kinds of sorting: By identifier
(RFC number), by date of issue, by title and by category. These map
directly to four kinds of metadata that we've stored about each and
every document. By overriding
:meth:`~ferenda.DocumentRepository.facets` we can specify these four
*facets*, aspects of documents used for grouping and sorting.

.. literalinclude:: examples/rfcs.py
   :start-after: # begin facets
   :end-before: # end facets

After running toc with this change, you can see that three sets of
index pages are created. By default, the ``dcterms:identifier``
predicate isn't used for the TOC pages, as it's often derived from the
document title. Furthermore, you'll get some error messages along the
lines of "Best Current Practice does not look like a valid URI", which
is because the ``dcterms:subject`` predicate normally should have URIs
as values, and we are using plain string literals.

We can fix both these problems by customizing our facet objects a
little. We specify that we wish to use ``dcterms:identifier`` as a TOC
facet, and provide a simple method to group RFCs by their identifier
in groups of 100, ie one page for RFC 1-99, another for RFC 100-199,
and so on. We also specify that we expect our ``dcterms:subject``
values to be plain strings.

.. literalinclude:: examples/rfcs.py
   :start-after: # begin facets2
   :end-before: # end facets2

The above code gives some example of how :class:`~ferenda.Facet`
objects can be configured. However, a :class:`~ferenda.Facet`
object does not control how each individual document is listed on a
toc page. The default formatting just lists the title of the document,
linked to the document in question. For RFCs, who mainly is referenced
using their RFC number rather than their title, we'd like to add the
RFC number in this display. This is done by overriding
:meth:`~ferenda.DocumentRepository.toc_item`.
		
.. literalinclude:: examples/rfcs.py
   :start-after: # begin toc_item
   :end-before: # end toc_item

Se also :doc:`toc` and :doc:`facets`.

Customizing :meth:`~ferenda.DocumentRepository.news`
====================================================
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

   The news generation does not make use the Facet objects that we've
   defined. This will be fixed in later releases of Ferenda.
		
Se also :doc:`news`.

Customizing :func:`~ferenda.manager.frontpage`
==============================================

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
==========

When you have written code and customized downloading, parsing and all
the other steps, you'll want to run all these steps for all your
docrepos in a single command by using the special value ``all`` for
docrepo, and again ``all`` for action::

    ./ferenda-build.py all all

By now, you should have a basic idea about the key concepts of
ferenda. In the next section, :doc:`keyconcepts`, we'll explore them
further.
    
