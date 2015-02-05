.. Ferenda documentation master file, created by
   sphinx-quickstart on Tue Oct  9 20:25:33 2012.
   You can adapt this file completely to your liking, but it should at least
   contain the root `toctree` directive.

Ferenda
===================================

Ferenda is a python library and framework for transforming
unstructured document collections into structured
`Linked Data <http://en.wikipedia.org/wiki/Linked_data>`_. It helps
with downloading documents, parsing them to add explicit semantic
structure and RDF-based metadata, finding relationships between
documents, and republishing the results.


.. toctree::
   :maxdepth: 2

   intro
   firststeps
   createdocrepos
   keyconcepts
   docmetadata
   elementclasses
   fsmparser
   citationparsing
   readers
   facets
   toc
   news
   wsgi
   restapi
   external-dbs
   advanced

   
API reference
=============

Classes
-------

.. toctree::
   :maxdepth: 1

   api/documentrepository
   api/document
   api/documententry
   api/documentstore
   api/facet
   api/tocpage
   api/tocpageset
   api/feed
   api/feedset
   api/elements
   api/elements-html
   api/describer
   api/transformer
   api/fsmparser
   api/citationparser
   api/uriformatter
   api/triplestore
   api/fulltextindex
   api/textreader
   api/pdfreader
   api/pdfanalyzer
   api/wordreader
..  
   api/wsgiapp
   api/resources -- these are not yet public api
   
Modules
-------

.. toctree::
   :maxdepth: 2

   api/util
   api/citationpatterns
   api/uriformats
   api/manager
   api/testutil

Decorators
----------

.. toctree::
   :maxdepth: 2

   api/decorators
   
Errors
------

.. toctree::
   :maxdepth: 2

   api/errors

Document repositories
---------------------

.. toctree::
   :maxdepth: 2

   docrepo/static
   docrepo/keyword
   docrepo/mediawiki
   docrepo/skeleton
   docrepo/tech
   docrepo/legal-eu
   docrepo/legal-se
   api/devel
   

Indices and tables
==================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`

