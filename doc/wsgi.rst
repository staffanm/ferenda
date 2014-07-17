The WSGI app
============

All ferenda projects contains a built-in web application. This app
provides navigation, document display and search.

Running the web application
---------------------------

During development, you can just ``ferenda-build.py runserver``. This
starts up a single-threaded web server in the foreground with the web
application, by default accessible as ``http://localhost:8000/``

You can also run the web application under any `WSGI
<http://wsgi.readthedocs.org/en/latest/>`_ server, such as `mod_wsgi
<http://code.google.com/p/modwsgi/>`_, `uWSGI
<https://uwsgi-docs.readthedocs.org/en/latest/index.html>`_ or
`Gunicorn <http://gunicorn.org/>`_.  ``ferenda-setup`` creates a file
called ``wsgi.py`` alongside ``ferenda-build.py`` which is used to
serve the ferenda web app using WSGI. This is the contents of that
file::

  from ferenda.manager import make_wsgi_app
  inifile = os.path.join(os.path.dirname(__file__), "ferenda.ini")
  application = make_wsgi_app(inifile=inifile)

Apache and mod_wsgi
^^^^^^^^^^^^^^^^^^^
In your httpd.conf::

  WSGIScriptAlias / /path/to/project/wsgi.py
  WSGIPythonPath /path/to/project
  <Directory /path/to/project>
    <Files wsgi.py>
      Order deny,allow
      Allow from all
    </Files>
  </Directory>

The ferenda web app consists mainly of static files. Only search and
API requests are dynamically handled. By default though, all static
files are served by the ferenda web app. This is simple to set up, but
isn't optimal performance-wise.

..
  You can create a .htaccess file to
  allow apache to serve static files without changing any public
  URLs. Simply pass the --htaccess parameter to the makeresources
  command::
  
    $ ./ferenda-build.py makeresources --htaccess
  
  .. note::
  
     The ``--htaccess`` parameter doesn't actually work yet.
    
  Then, change the path where the dynamic web app is mounted in the URL
  space in your httpd.conf::
  
    WSGIScriptAlias /api /path/to/project/wsgi.py
    WSGIScriptAlias /search /path/to/project/wsgi.py
  
  (Both of these should be present. If you'd like to mount these apps in
  a different place, you should also add or change the 'apiendpoint' and
  'searchendpoint' parameters in ferenda.ini, eg::
  
    [__root__]
    apiendpoint=/dynamic/service/ferenda-api
    searchendpoint=/dynamic/service/ferenda-search
  
Gunicorn
^^^^^^^^
Just run ``gunicorn wsgi:application``

.. _urls_used:

URLs for retrieving resources
-----------------------------

In keeping with `Linked Data principles
<http://www.w3.org/DesignIssues/LinkedData.html>`_, all URIs for your
documents should be retrievable. By default, all URIs for your
documents start with ``http://localhost:8000/res``
(e.g. ``http://localhost:8000/res/rfc/4711`` -- this is controlled by
the ``url`` parameter in ``ferenda.ini``). These URIs are retrievable
when you run the built-in web server during development, as described
above.


Document resources
^^^^^^^^^^^^^^^^^^

For each resource, use the ``Accept`` header to retrieve different
versions of it:

* ``curl -H "Accept: text/html" http://localhost:8000/res/rfc/4711``
  returns ``rfc/generated/4711.html``
* ``curl -H "Accept: application/xhtml+xml"
  http://localhost:8000/res/rfc/4711`` returns
  ``rfc/parsed/4711.xhtml``
* ``curl -H "Accept: application/rdf+xml"
  http://localhost:8000/res/rfc/4711`` returns
  ``rfc/distilled/4711.rdf``
* ``curl -H "Accept: text/turtle" http://localhost:8000/res/rfc/4711``
  returns ``rfc/distilled/4711.rdf``, but in Turtle format
* ``curl -H "Accept: text/plain" http://localhost:8000/res/rfc/4711``
  returns ``rfc/distilled/4711.rdf``, but in NTriples format

..
  * ``curl -H "Accept: application/json"
    http://localhost:8000/res/rfc/4711`` returns
    ``rfc/distilled/4711.rdf``, but in JSON-LD format

You can also get *extended information* about a single document in
various RDF flavours. This extended information includes everything
that :meth:`~ferenda.DocumentRepository.construct_annotations`
returns, i.e. information about documents that refer to this document.

* ``curl -H "Accept: application/rdf+xml"
  http://localhost:8000/res/rfc/4711/data`` returns a RDF/XML
  combination of ``rfc/distilled/4711.rdf`` and
  ``rfc/annotation/4711.grit.xml``
* ``curl -H "Accept: text/turtle"
  http://localhost:8000/res/rfc/4711/data`` returns the same in Turtle
  format
* ``curl -H "Accept: text/plain"
  http://localhost:8000/res/rfc/4711/data`` returns the same in
  NTriples format
* ``curl -H "Accept: application/json"
  http://localhost:8000/res/rfc/4711/data`` returns the same in
  JSON-LD format.

  
Dataset resources
^^^^^^^^^^^^^^^^^

Each docrepo exposes information about the data it contains through
it's dataset URI. This is a single URI (controlled by
:meth:`~ferenda.DocumentRepository.dataset_uri`) which can be queried
in a similar way as the document resources above:

* ``curl -H "Accept: application/html" http://localhost/dataset/rfc``
  returns a HTML view of a Table of Contents for all documents (see
  :doc:`toc`)
* ``curl -H "Accept: text/plain" http://localhost/dataset/rfc``
  returns ``rfc/distilled/dump.nt`` which contains all RDF statements
  for all documents in the repository.
* ``curl -H "Accept: application/rdf+xml"
  http://localhost/dataset/rfc`` returns the same, but in RDF/XML
  format.
* ``curl -H "Accept: text/turtle" http://localhost/dataset/rfc``
  returns the same, but in turtle format.


See also :doc:`restapi`.


.. 
  URIs for things other than documents
  ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  .. note::
     The functionality in this section is not yet implemented.
  It should be noted that the infamous httpRange-14
  (http://www.jenitennison.com/blog/node/159) issue is largely a
  non-issue for content served by ferenda, as it only uses URIs for
  things (documents) that are, in fact, available on the web. But
  occasionally you need (or want) to use references to things that are
  not available on the web, for example to specify the publisher of a
  specific document, eg::
    <http://localhost:8000/res/rfc/4711>
        dcterms:publisher <http://localhost:8080/things/org/IETF> .
  All n3 files present in the directory ``triples`` will be read and
  used. Eg. create ``triples/org.n3`` with the content::
    <http://localhost:8000/things/org/IETF>
        rdfs:label "Internet Engineering Task Force (IETF)"@en ,
        foaf:homepage <http://www.ietf.org> .
  Now when you go to http://localhost:8000/things/org/IETF with a web
  browser, it will redirect you to the IETF homepage, but if you perform
  a Accept: application/rdf+xml GET on the same URI, it'll reply with
  all statements about that URI in RDF/XML
  
.. 
  Using ``develurl`` during development
  ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  
  .. note::
  
     The functionality in this section is not yet implemented either.
  
  When deploying, you won't use http://localhost:8000/ in your
  public-facing URLs. Instead, come up with an external base url such as
  ``http://example.org/netstandards/``, and in ferenda.ini set::
  
    [__root__]
    url=http://example.org/netstandards/   
    develurl=http://localhost:8000/
  
  This will make all uris in parsed and generated documents on the form
  http://example.org/netstandards/res/rfc/4711, but during devel still
  support http://localhost:8000/res/rfc/4711.
  
  When you set url to a new value, you must re-run ``./ferenda-build.py
  all generate --all --force``, ``./ferenda-build.py all toc --force``,
  ``./ferenda-build.py all news --force`` and ``./ferenda-build.py all
  frontpage --force`` for it to take effect.

