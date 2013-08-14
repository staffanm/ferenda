# firststeps-api.py

# --- INCLUDE BELOW ---
from w3cstandards import W3CStandards
repo = W3CStandards()
repo.download()
repo.status()
# or use repo.get_status() to get all status information in a nested dict
# --- INCLUDE ABOVE ---

# --- INCLUDE BELOW ---
from w3cstandards import W3CStandards
repo = W3CStandards(force=True)
repo.parse("rdb-direct-mapping")
# --- INCLUDE ABOVE ---

# --- INCLUDE BELOW ---
import logging
from w3cstandards import W3CStandards
# client code is responsible for setting the effective log level -- ferenda 
# just emits log messages, and depends on the caller to setup the logging 
# subsystem in an appropriate way
logging.getLogger().setLevel(logging.INFO)
repo = W3CStandards()
for basefile in repo.list_basefiles_for("parse"):
    # You you might want to try/catch the exception
    # ferenda.errors.ParseError or any of it's children here
    repo.parse(basefile)
# --- INCLUDE ABOVE ---

# --- INCLUDE BELOW ---
from ferenda import manager
from w3cstandards import W3CStandards
repo = W3CStandards()
for basefile in repo.list_basefiles_for("relate"):
    repo.relate(basefile)
manager.makeresources([repo], sitename="Standards", sitedescription="W3C standards, in a new form")
for basefile in repo.list_basefiles_for("generate"):
    repo.generate(basefile)
repo.toc()
repo.news()
manager.frontpage([repo])
# --- INCLUDE ABOVE ---
