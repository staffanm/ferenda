#!/usr/bin/env python

from setuptools import setup, find_packages
import os,sys

# This is a RST version of the markdown in README.md. Try to keep
# these in sync!  When bored, try some of the tips in
# https://coderwall.com/p/qawuyq to automate the conversion

longdesc = """
Ferenda
=======

*Converts document collections to structured Linked Data*

Ferenda is a python library and framework to scrape and convert
unstructured content into semantically-marked-up, Linked Data-enabled
content. It is focused on documents, not individual pieces of data,
but is useful for any kind of information that can be described in
terms of resources (documents, people, places, events, etc...). It
uses RDF for all metadata.

Quick start
-----------

This uses ferenda's project framework to download the 50 latest RFCs 
and W3C standards, parse documents into structured, RDF-enabled XHTML 
documents, loads all RDF metadata into a triplestore and generates a 
web site of static HTML5 files that are usable offline::

    export STATIC_DEPS=true     # if using python 3.3 on Mac OS
    pip install --extra-index-url https://testpypi.python.org/pypi ferenda
    ferenda-setup myproject
    cd myproject
    ./ferenda-build.py ferenda.sources.tech.RFC enable
    ./ferenda-build.py ferenda.sources.tech.W3Standards enable
    ./ferenda-build.py all all --downloadmax=50 --staticsite --fulltextindex=False
    open data/index.html

More information
----------------

See http://ferenda.readthedocs.org/ for information about prerequisites, 
installing and writing code to handle your document collections.

"""
# FIXME: We'd like to install rdflib-sqlalchemy but this isn't
# available from pypi, only from git repos (see
# requirements.py3.txt). Is this possible?

install_requires = ['beautifulsoup4 >= 4.2.0',
                    'jsmin >= 2.0.2',
                    'lxml >= 3.2.0',
                    'rdflib >= 4.0.1',
                    'html5lib >= 1.0b1',
                    'rdfextras >= 0.4',
                    'requests >= 1.2.0',
                    'Whoosh >= 2.4.1',
                    'six >= 1.2.0']

if sys.version_info < (3,0,0):
    install_requires.append('pyparsing==1.5.7')
    # not py3 compatible, but not essential either
    install_requires.append('SimpleParse >= 2.1.1') 
else:
    # lastest version 2.0.0 is not py2 compatible
    install_requires.append('pyparsing') 

if sys.version_info < (2,7,0):
    install_requires.append('ordereddict >= 1.1')


tests_require = []

if sys.version_info < (3,3,0):
    tests_require.append('mock >= 1.0.0')
if sys.version_info < (2,7,0):
    tests_require.append('unittest2 >= 0.5.1')
    
# we can't just import ferenda to get at ferenda.__version since it
# might have unmet dependencies at this point. Exctract it directly
# from the file (code from rdflib:s setup.py)
def find_version(filename):
    import re
    _version_re = re.compile(r'__version__ = "(.*)"')
    for line in open(filename):
        version_match = _version_re.match(line)
        if version_match:
            return version_match.group(1)

setup(name='ferenda',
      version=find_version('ferenda/__init__.py'),
      description='Transform unstructured document collections to structured Linked Data',
      long_description=longdesc,
      author='Staffan Malmgren',
      author_email='staffan.malmgren@gmail.com',
      url='http://lagen.nu/ferenda/',
      license='BSD',
      install_requires=install_requires,
      tests_require=tests_require,
      entry_points = {
        'console_scripts':['ferenda-setup = ferenda.manager:setup']
        },
      packages=find_packages(exclude=('test', 'docs')),
      # package_dir = {'ferenda':'ferenda'},
      # package_data = {'ferenda':['res/css/*.css', 'res/js/*.js', 'res/xsl/*.xsl']},
      include_package_data = True,
      zip_safe = False,
      classifiers=[ 
          'Development Status :: 4 - Beta',
          'Environment :: Console',
          'Environment :: Web Environment',
          'Intended Audience :: Developers',
          'License :: OSI Approved :: BSD License',
          'Operating System :: OS Independent',
          'Programming Language :: Python',
          'Programming Language :: Python :: 2',
          'Programming Language :: Python :: 2.6',
          'Programming Language :: Python :: 2.7',
          'Programming Language :: Python :: 3',
          'Programming Language :: Python :: 3.2',
          'Programming Language :: Python :: 3.3',
          'Topic :: Software Development :: Libraries :: Python Modules',
          'Topic :: Internet :: WWW/HTTP :: WSGI :: Application',
          'Topic :: Text Processing',
          'Topic :: Text Processing :: Markup :: XML'
          ]
      )
