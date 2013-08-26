Setting up external databases
=============================

Ferenda stores data in three substantially different ways:

* Documents are stored in the file system
* RDF Metadata is stored in in a `triple store <http://en.wikipedia.org/wiki/Triplestore>`_
* Document text is stored in a fulltext search engine.

There are many capable and performant triple stores and fulltext
search engines available, and ferenda supports a few of them. The
default choice for both are embedded solutions (using RDFLib + SQLite
for a triple store and Whoosh for a fulltext search engine) so that
you can get a small system going without installing and configuring
additional server processess. However, these choices do not work well
with medium to large datasets, so when you start feeling that indexing
and searching is getting slow, you should run an external triplestore
and an external fulltext search engine.

If you're using the project framework, you set the configuration
values ``storetype`` and ``indextype`` to new values. You'll find that
the ``ferenda-setup`` tool creates a ``ferenda.ini`` that specifies
``storetype``, but not ``indextype`` -- you'll have to add that
yourself.

.. note::

   If you had a Sesame or Fuseki server running using their standard
   configuration when you ran ferenda-setup, you'll notice that
   ``ferenda-setup`` attempted to configure ``ferenda.ini`` to use one of
   them. You still might have to do extra configuration, both in the
   config file as well as the Sesame or Fuseki server.

At the same time, you'll need to change ``storelocation`` and
``indexlocation`` (and possibly ``storerepository``) to new values as
well. See examples below.


.. _external-triplestore:

Triple stores
-------------

There are four choices. 

RDFLib + SQLite
^^^^^^^^^^^^^^^

In ``ferenda.ini``::

    [__root__]
    storetype = SQLITE
    storelocation = data/ferenda.sqlite # single file
    storerepository = <projectname>

This is the simplest way to get up and running, requiring no configuration or installs on any platform.

RDFLib + Sleepycat (aka ``bsddb``)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

In ``ferenda.ini``::

    [__root__]
    storetype = SLEEPYCAT
    storelocation = data/ferenda.db # directory
    storerepository = <projectname>

This requires that ``bsddb`` (part of the standard library for python 2) or ``bsddb3`` (separate package) is available and working (which can be a bit of pain on many platforms). Furthermore it's less stable and slower than RDFLib + SQLite, so it can't really be recommended. But since it's the only persistant storage directly supported by RDFLib, it's supported by Ferenda as well.

Sesame
^^^^^^

In ``ferenda.ini``::

    [__root__]
    storetype = SESAME
    storelocation = http://localhost:8080/openrdf-sesame
    storerepository = <projectname>

`Sesame <http://www.openrdf.org/index.jsp>`_ is a framework and a set of java web applications that normally runs within a Tomcat application server. If you're comfortable with Tomcat and servlet containers you can get started with this quickly, see their `installation instructions <http://www.openrdf.org/doc/sesame2/users/ch06.html>`_. You'll need to install both the actual Sesame Server and the OpenRDF workbench.

After installing it and configuring ``ferenda.ini`` to use it, you'll need to use the OpenRDF workbench app (at ``http://localhost:8080/openrdf-workbench`` by default) to create a new repository. The recommended settings are::

    Type: Native Java store    
    ID: <projectname> # eg same as storerepository in ferenda.ini    
    Title: Ferenda repository for <projectname>    
    Triple indexes: spoc,posc,cspo,opsc,psoc

It's much faster than the RDFLib-based stores and is fairly stable (although Ferenda's usage patterns seem to sometimes make simple operations take a disproportionate amount of time). 


Fuseki
^^^^^^

In ``ferenda.ini``::

    [__root__]
    storetype = SESAME
    storelocation = http://localhost:3030
    storerepository = ds

`Fuseki <http://jena.apache.org/documentation/serving_data/>`_ is a simple java server that implements most SPARQL standards and can be run `without any complicated setup <http://jena.apache.org/documentation/serving_data/#getting-started-with-fuseki>`_. It can keep data purely in memory or store it on disk. The above configuration works with the default configuration of Fuseki - just download it and run ``fuseki-server``

Fuseki seems to be the fastest triple store that Ferenda supports, at least with Ferendas usage patterns. Since it's also the easiest to set up, it's the recommended triple store once RDFLib + SQLite isn't enough.

.. _external-fulltext:

Fulltext search engines
-----------------------

There are two choices. 

Whoosh
^^^^^^

In ``ferenda.ini``::

    [__root__]
    indextype = WHOOSH
    indexlocation = data/whooshindex

Whoosh is an embedded python fulltext search engine, which requires no setup (it's automatically installed when installing ferenda with ``pip`` or ``easy_install``), works reasonably well with small to medium amounts of data, and performs quick searches. However, once the index grows beyond a few hundred MB, indexing of new material begins to slow down. 


Elasticsearch
^^^^^^^^^^^^^


In ``ferenda.ini``::

    [__root__]
    indextype = ELASTICSEARCH
    indexlocation = http://localhost:9200/ferenda/

Elasticsearch is a distributed fulltext search engine in java which can run in a distributed fashion and which is accessed through a simple JSON/REST API. It's easy to setup -- just download it and run ``bin/elasticsearch`` as per the `instructions <http://www.elasticsearch.org/guide/reference/setup/installation/>`_. Ferenda's support for Elasticsearch is new and not yet stable, but it should be able to handle much larger amounts of data.
