# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import shutil, os
if os.path.exists("netstandards"):
    shutil.rmtree("netstandards")

# begin example
from ferenda.sources.tech import RFC, W3Standards
from ferenda.manager import makeresources, frontpage, runserver, setup_logger
from ferenda.errors import DocumentRemovedError, ParseError, FSMStateError

config = {'datadir':'netstandards/exampledata', 
          'loglevel':'DEBUG',
          'force':False,
          'storetype':'SQLITE',
          'storelocation':'netstandards/exampledata/netstandards.sqlite',
          'storerepository':'netstandards',
          'downloadmax': 50 # remove this to download everything
}

setup_logger(level='DEBUG')

# Set up two document repositories
docrepos = (RFC(**config), W3Standards(**config))

for docrepo in docrepos:
    # Download a bunch of documents
    docrepo.download()
    
    # Parse all downloaded documents
    for basefile in docrepo.store.list_basefiles_for("parse"):
        try:
            docrepo.parse(basefile)
        except ParseError as e:
            pass  # or handle this in an appropriate way

    # Index the text content and metadata of all parsed documents
    for basefile in docrepo.store.list_basefiles_for("relate"):
        docrepo.relate(basefile, docrepos)

# Prepare various assets for web site navigation
makeresources(docrepos,
              resourcedir="netstandards/exampledata/rsrc",
              sitename="Netstandards",
              sitedescription="A repository of internet standard documents")

# Relate for all repos must run before generate for any repo
for docrepo in docrepos:
    # Generate static HTML files from the parsed documents, 
    # with back- and forward links between them, etc.
    for basefile in docrepo.store.list_basefiles_for("generate"):
        docrepo.generate(basefile)
        
    # Generate a table of contents of all available documents
    docrepo.toc()
    # Generate feeds of new and updated documents, in HTML and Atom flavors
    docrepo.news()

# Create a frontpage for the entire site
frontpage(docrepos,path="netstandards/exampledata/index.html")

# Start WSGI app at http://localhost:8000/ with navigation,
# document viewing, search and API
# runserver(docrepos, port=8000, documentroot="netstandards/exampledata")

# end example
shutil.rmtree("netstandards")
return_value = True
