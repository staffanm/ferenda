First steps
===========

Ferenda can be used in a project-like manner with a command-line tool
(similar to how projects based on `Django
<https://www.djangoproject.com/>`_, `Sphinx <http://sphinx-doc.org/>`_
and `Scrapy <http://scrapy.org>`_ are used), or it can be used
programatically through a simple API. In this guide, we'll primarily
be using the command-line tool, and then show how to achieve the same thing
using the API.

The first step is to create a project. Lets make a simple website that
contains published standards from W3C and IETF, called
"netstandards". Ferenda installs a system-wide command-line tool
called ``ferenda-setup`` whose sole purpose is to create projects::

  $ ferenda-setup netstandards
  $ cd netstandards
  $ ls
  ferenda-build.py
  ferenda.ini
  wsgi.py

The three files created by ``ferenda-setup`` is another command line
tool (``ferenda-build.py``) used for management of the newly created
project, a WSGI application (``wsgi.py``, see :doc:`wsgi`) and a
configuration file (``ferenda.ini``). By default, this configuration
file specifies the default logging level, the directory where document
files will be stored and which triple store your project will be
using.

.. note::

   When using the API, you don't create a project or deal with configuration 
   files in the same way. Instead, your client code is responsible for
   keeping track of which docrepos to use, and providing configuration
   when calling their methods.

Creating a Document repository class
------------------------------------

Any document collection is handled by a
:ref:`keyconcept-documentrepository` class (or *docrepo* for
short), so our first task is to create a docrepo for W3C standards.

A docrepo class is responsible for downloading documents in a specific
document collection. These classes can inherit from
:class:`~ferenda.DocumentRepository`, which amongst others provides
the method :meth:`~ferenda.DocumentRepository.download` for
this. Since the details of how documents are made available on the web
differ greatly from collection to collection, you'll often have to
override the default implementation, but in this particular case, it
suffices. The default implementation assumes that all documents are
available from a single index page, and that the URLs of the documents
follow a set pattern.


The W3C standards are set up just like that:  All standards are available at
``http://www.w3.org/TR/tr-status-all``. There are a lot of links to
documents on that page, and not all of them are links to recommended
standards. A simple way to find only the recommended standards is to
see if the link follows the pattern
``http://www.w3.org/TR/<year>/REC-<standardid>-<date>``.

Creating a docrepo that is able to download all web
standards is then as simple as creating a subclass and setting three
class properties. Create this class in the current directory (or
anywhere else on your python path) and save it as ``w3cstandards.py``

.. literalinclude:: w3cstandards.py
  :lines: 1-6
  
The first property, :data:`~ferenda.DocumentRepository.alias`, is
required for all docrepos and controls the alias used by the command
line tool for that docrepo, as well as the path where files are
stored, amongst other things. If your project has a large collection
of docrepos, it's important that they all have unique aliases.

The other two properties are parameters which the default
implementation of :meth:`~ferenda.DocumentRepository.download` uses in
order to find out which documents to
download. :data:`~ferenda.DocumentRepository.start_url` is just a
simple regular URL, while
:data:`~ferenda.DocumentRepository.document_url_regex` is a standard
:py:mod:`re` regex with named groups. The group named ``basefile`` has
special meaning, and will be used as a base for stored files and
elsewhere as a short identifier for the document. For example, the web
standard found at URL
http://www.w3.org/TR/2012/REC-rdf-plain-literal-20121211/ will have
the basefile ``rdf-plain-literal``.
      
Using ferenda-build.py and enabling docrepo classes
---------------------------------------------------

Next step is to enable our class. Like most tasks, this is done using
the command line tool present in your project directory. To register
the class (together with a short alias) in your ``ferenda.ini``
configuration file, run the following::

  $ ./ferenda-build.py w3cstandards.W3CStandards enable
  Enabled class w3cstandards.W3CStandards (alias 'w3c')

This creates a new section in ferenda.ini that just looks like the
following::

  [w3c]
  class = w3cstandards.W3CStandards
  
From this point on, you can use the class name or the alias "w3c"
interchangably::

  $ ./ferenda-build.py w3cstandards.W3CStandards status # verbose
  $ ./ferenda-build.py w3c status # terse, exactly the same result


.. note:: 

   When using the API, there is no need (or possibility) to register
   docrepo classes. Your client code directly instantiates the class(es)
   it uses and calls methods on them.

Downloading
-----------

To test the downloading capabilities of our class, you can run the
download method directly from the command line using the command line
tool::

  $ ./ferenda-build.py w3c download 
  16:31:51 w3c INFO WOFF: downloaded from http://www.w3.org/TR/2012/REC-WOFF-20121213/
  16:31:51 w3c INFO rdf-plain-literal: downloaded from http://www.w3.org/TR/2012/REC-rdf-plain-literal-20121211/
  16:31:52 w3c INFO owl2-xml-serialization: downloaded from http://www.w3.org/TR/2012/REC-owl2-xml-serialization-20121211/
  ...

After a few minutes of downloading, the result is a bunch of files in ``data/w3c/downloaded``::

  $ ls -1 data/w3c/downloaded
  WOFF.html
  WOFF.html.etag
  owl2-rdf-based-semantics.html
  owl2-rdf-based-semantics.html.etag
  owl2-xml-serialization.html
  owl2-xml-serialization.html.etag


.. note::

   The ``.etag`` files are created in order to support Conditional
   GET, so that we don't waste our time or remote server bandwith by
   re-downloading documents that hasn't changed. They can be ignored
   and might go away in future versions of Ferenda.

We can get a overview of the status of our docrepo using the
``status`` command::

  $ ./ferenda-build.py w3c status
  Status for document repository 'w3c' (w3cstandards.W3CStandards)
  download: rdf-plain-literal, owl2-xml-serialization, owl2-syntax... (2 more)
  parse: None.
  generated: None.

.. note::

   To do the same using the API:

   .. literalinclude:: firststeps-api.py
      :lines: 4-8

Finally, if the logging information scrolls by too quickly and you
want to read it again, take a look in the ``logs`` directory in the
current directory. Each invocation of ``ferenda-build.py`` creates a
new log file containing the same information that is written to
stdout.


Parsing
-------

Let's try the next step in the workflow, to parse one of the documents
we've downloaded::

  $ ./ferenda-build.py w3c parse rdb-direct-mapping
  2012-10-09 10:06:15 DEBUG: Parse rdf-direct-mapping start
  2012-10-09 10:06:15 DEBUG: 3 triples extracted
  2012-10-09 10:06:15 INFO: Parse rdf-direct-mapping OK (3.423 sec)


By now, you might have realized that our command line tool generally
is called in the following manner::

  $ ./ferenda-build.py <docrepo> <command> [argument(s)]

The parse command resulted in one new file being created in ``data/w3c/parsed``::

  $ ls -1 data/w3c/downloaded
  rdb-direct-mapping.xhtml

And we can again use the ``status`` command to get a comprehensive
overview of our document repository::
  
  $ ./ferenda-build.py w3c status
  Status for document repository 'w3c' (w3cstandards.W3CStandards)
  download: widgets, rdf-plain-literal, rdb-direct-mapping... (13 more)
  parse: rdb-direct-mapping. Todo: rdf-plain-literal, owl2-xml-serialization, owl2-syntax... (12 more)
  generated: None. Todo: rdb-direct-mapping

Note that by default, subsequent invocations of parse won't actually
parse documents that don't need parsing::

  $ ./ferenda-build.py w3c parse rdb-direct-mapping
  2012-10-09 10:06:15 DEBUG: Parse rdf-direct-mapping skipped (data/parsed/rdf-direct-mapping/index.xhtml up-to-date)
  
But during development, when you change the parsing code frequently,
you'll need to override this through the ``--force`` flag (or set the
``force`` parameter in ``ferenda.ini``)::

  $ ./ferenda-build.py w3c parse rdb-direct-mapping --force
  2012-10-09 10:06:15 DEBUG: Parse rdf-direct-mapping start
  ...


.. note::

   To do the same using the API:

   .. literalinclude:: firststeps-api.py
      :lines: 12-14

Note also that you can parse all downloaded documents through the
``--all`` flag, and control logging verbosity by the ``--loglevel`` flag::

  $ ./ferenda-build.py w3c parse --all --loglevel=INFO
  2012-10-09 10:06:15 INFO: Parse r2rml OK (3.423 sec)
  2012-10-09 10:06:15 INFO: Parse foo OK (3.423 sec)
  2012-10-09 10:06:15 INFO: Parse bar OK (3.423 sec)
  ...


.. note::

   To do the same using the API:

   .. literalinclude:: firststeps-api.py
      :lines: 18-28

   Note that the API makes you explicitly list and iterate over any available files. This is 
   so that client code has the opportunity to parallelize this work in an appropriate way.

If we take a look at the files created in ``data/w3c/distilled``, we
see some metadata for each document. This metadata has been
automatically extracted from RDFa statements in the XHTML documents,
but is so far very spartan.

Now take a look at the files created in ``data/w3c/parsed``. The default
implementation of ``parse()`` processes the DOM of the main body of the 
document, but some tags and attribute that are used only for
formatting are stripped, such as ``<style>`` and ``<script>``.

These documents have
quite a lot of "boilerplate" text such as table of contents and links
to latest and previous versions which we'd like to remove so that just
the actual text is left (problem 1). And we'd like to explicitly extract some
parts of the document and represent these as metadata for the document
-- for example the title, the publication date, the authors/editors of
the document and it's abstract, if available (problem 2).

Just like the default implementation of
:meth:`~ferenda.DocumentRepository.download` allowed for some
customization using class variables, we can solve problem 1 by setting
two additional class variables:

.. literalinclude:: w3cstandards.py
  :lines: 8-10

The :py:data:`~ferenda.DocumentRepository.parse_content_selector`
member specifies, using `CSS selector syntax
<http://www.w3.org/TR/CSS2/selector.html>`_, the part of the document
which contains our main text. It defaults to ``"body"``, and can often
be set to ``".content"`` (the first element that has a class="content"
attribute"), ``"#main-text"`` (any element with the id
``"main-text"``), ``"article"`` (the first ``<article>`` element) or
similar.  The
:py:data:`~ferenda.DocumentRepository.parse_filter_selectors` is a list
of similar selectors, with the difference that all matching elements
are removed from the tree. In this case, we use it to remove some
boilerplate sections that often within the content specified by
:py:data:`~ferenda.DocumentRepository.parse_content_selector`, but
which we don't want to appear in the final result.

In order to solve problem 2, we can override one of the methods that
the default implementation of parse() calls:

.. literalinclude:: w3cstandards.py
   :language: python
   :lines: 11-21

FIXME: code example should show from ferenda import Describer

:py:meth:`~ferenda.DocumentRepository.parse_metadata_from_soup` is
called with a document object and the parsed HTML document in the form
of a BeautifulSoup object. It is the responsibility of
:py:meth:`~ferenda.DocumentRepository.parse_metadata_from_soup` to add
document-level metadata for this document, such as it's title,
publication date, and similar. Note that
:py:meth:`~ferenda.DocumentRepository.parse_metadata_from_soup` is run
before the
:py:data:`~ferenda.DocumentRepository.parse_content_selector` and
:py:data:`~ferenda.DocumentRepository.parse_filter_selectors` are
applied, so the BeautifulSoup object passed into it contains the
entire document.

.. note::

   The selectors are passed to `BeautifulSoup.select() <http://www.crummy.com/software/BeautifulSoup/bs4/doc/#css-selectors>`_,
   which supports a subset of the CSS selector syntax. If you stick
   with simple tag, id and class-based selectors you should be fine.

Now, if you run ``parse --force`` again, both documents and metadata are
in better shape. Further down the line the value of properly extracted
metadata will become more obvious.

Republishing the parsed content
-------------------------------

The XHTML contains metadata in RDFa format. As such, you can extract
all that metadata and put it into a triple store. The relate command
does this, as well as creating a full text index of all textual
content::

  $ ./ferenda-build.py w3c relate --all
  2012-10-09 10:06:15 INFO: 467 triples in total (data/w3c/distilled/rdf.nt)

The next step is to create a number of *resource files*  (placed under
``data/rsrc``). These resource files include css and javascript files
for the new website we're creating, as well as a xml configuration
file used by the XSLT transformation done by ``generate`` below::

  $ ./ferenda-build.py w3c makeresources
  $ ls -lR data/rsrc
  resources.xml
  css/normalize.css
  css/base.css
  css/ferenda.css
  js/jquery-1.2.3.js
  js/modernizr-123.js
  js/ferenda.js
   

.. note::

   It is possible to combine and minify both javascript and css files using
   the ``combineresources`` option in the configuration file.

Running ``makeresources`` is needed for the final few steps::

  $ ./ferenda-build.py w3c generate --all

The ``generate`` command creates browser-ready HTML5
documents from our structured XHTML documents, using our site's
navigation::

  $ ./ferenda-build.py w3c toc
  $ ./ferenda-build.py w3c news
  $ ./ferenda-build.py w3c frontpage

The ``toc`` and ``feeds`` commands creates static files for general indexes/tables of contents
of all documents in our docrepo as well as Atom feeds, and the ``frontpage`` command creates a suitable frontpage for the site as a whole.

.. note:: 


   To do all of the above using the API:

   .. literalinclude:: firststeps-api.py
      :lines: 32-42

Finally, to start a development web server and check out the finished
result::

  $ ./ferenda-build.py w3c runserver
  $ open http://localhost:8080/

Now you've created your own web site with structured documents. It
contains listings of all documents, feeds with updated documents (in
both HTML and Atom flavors) and full text search. In order to deploy
your site, you can run it under Apache+mod_wsgi, ngnix+uWSGI, Gunicorn
or just about any WSGI capable web server, see :doc:`wsgi`.

.. note::

   Using :py:func:`~ferenda.manager.runserver` from the API does not really make any sense.
   If your environment supports running WSGI applications, see the above link for information
   about how to get the ferenda WSGI application. Otherwise, the app can be run by any standard 
   WSGI host.

To keep it up-to-date whenever the W3C issues new standards, use the
following command::

  $ ./ferenda-build.py w3c all

The "all" command is an alias that runs ``download``, ``parse --all``, ``relate
--all``, ``generate --all``, ``toc`` and ``feeds`` in sequence. 

.. note::

   The API doesn't have any corresponding method. Just run all of the
   above code again. As long as you don't pass the ``force=True``
   parameter when creating the docrepo instance, ferendas dependency
   management should make sure that documents aren't needlessly
   re-parsed etc.

This 20-line example of a docrepo took a lot of shortcuts by depending on the
default implementation of the :meth:`~ferenda.DocumentRepository.download` and :meth:`~ferenda.DocumentRepository.parse` methods. Ferenda
tries to make it really to get *something* up and running quickly, and
then improving each step incrementally.

In the next section :doc:`createdocrepos` we will take a closer look at
each of the six main steps (``download``, ``parse``, ``relate``, ``generate``, ``toc`` and
``news``), including how to completely replace the built-in methods. 
You can also take a look at the source code for
:py:class:`ferenda.sources.tech.W3Standards`, which contains a more complete 
(and substantially longer) implementation of :meth:`~ferenda.DocumentRepository.download`, :meth:`~ferenda.DocumentRepository.parse`
and the others.
