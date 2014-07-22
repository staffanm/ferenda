REM
REM  This batch file sets up five virtual environments for the different
REM  python versions supported under windows. It requires that the installed
REM  pythons have SimpleParse (for python 2.*) and LXML installed in the
REM  system-wide site-packages (since these are not easily installed by pip on
REM  windows).
REM
REM  For python 3.3 and 3.4, unofficial lxml binary packages are available at
REM  http://www.lfd.uci.edu/~gohlke/pythonlibs/#lxml
REM  Note that there are no binary packages for
REM  SimpleParse and any Python 3 version.
REM

cd %USERPROFILE%
md virtualenvs
cd virtualenvs

virtualenv -p C:\Python26\python.exe --system-site-packages frnd-py26
frnd-py26\Scripts\pip install beautifulsoup4 rdflib html5lib rdfextras rdflib-jsonld requests six jsmin cssmin whoosh pyparsing unittest2 ordereddict mock coverage

virtualenv -p C:\Python27\python.exe --system-site-packages frnd-py27
frnd-py27\Scripts\pip install beautifulsoup4 rdflib html5lib rdfextras rdflib-jsonld requests six jsmin cssmin whoosh pyparsing mock coverage

virtualenv -p C:\Python32\python.exe --system-site-packages frnd-py32
frnd-py32\Scripts\pip install beautifulsoup4 rdflib html5lib rdfextras rdflib-jsonld requests six jsmin cssmin whoosh pyparsing mock coverage

virtualenv -p C:\Python33\python.exe --system-site-packages frnd-py33
frnd-py33\Scripts\pip install beautifulsoup4 rdflib html5lib rdfextras rdflib-jsonld requests six jsmin cssmin whoosh pyparsing coverage

virtualenv -p C:\Python34\python.exe --system-site-packages frnd-py34
frnd-py34\Scripts\pip install beautifulsoup4 rdflib html5lib rdfextras rdflib-jsonld requests six jsmin cssmin whoosh pyparsing coverage tox

