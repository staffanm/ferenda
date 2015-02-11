# -*- coding: utf-8 -*-
from __future__ import unicode_literals

# firststeps-api.py
import sys
import os
import shutil
sys.path.append(os.environ['FERENDA_HOME']+"/doc/examples") # to find w3cstandards.py

# begin download-status-1 # note: adjust line numbers in firststeps.rst
from w3cstandards import W3CStandards
repo = W3CStandards()
repo = W3CStandards(downloadmax=3) # not included in docs but needed for tests
repo.download()  
repo.status()
# or use repo.get_status() to get all status information in a nested dict
# end download-status-2

# make sure the basefile we use for examples is available
repo.download("rdfa-core")

# begin parse-force
from w3cstandards import W3CStandards
repo = W3CStandards(force=True)
repo.parse("rdfa-core")
# end parse-force

# begin parse-all
import logging
from w3cstandards import W3CStandards
# client code is responsible for setting the effective log level -- ferenda 
# just emits log messages, and depends on the caller to setup the logging 
# subsystem in an appropriate way
logging.getLogger().setLevel(logging.INFO)
repo = W3CStandards()
for basefile in repo.store.list_basefiles_for("parse"):
    # You you might want to try/catch the exception
    # ferenda.errors.ParseError or any of it's children here
    repo.parse(basefile)
# end parse-all

# begin final-commands
from ferenda import manager
from w3cstandards import W3CStandards
repo = W3CStandards()
for basefile in repo.store.list_basefiles_for("relate"):
    repo.relate(basefile)
manager.makeresources([repo], sitename="Standards", sitedescription="W3C standards, in a new form")
for basefile in repo.store.list_basefiles_for("generate"):
    repo.generate(basefile)
repo.toc()
repo.news()
manager.frontpage([repo])
# end final-commands
shutil.rmtree(repo.config.datadir)
return_value = True
