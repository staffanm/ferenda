REM
REM  This batch file sets up three virtual environments for the different
REM  python versions supported under windows. It requires that the installed
REM  pythons have SimpleParse and LXML installed in the system-wide
REM  site-packages (since these are not easily installed by pip on windows).
REM  Since there is no binary package for lxml and python 3.3, this version
REM  is not currently supported. Note that there are no binary packages for
REM  SimpleParse and any Python 3 version.
REM

cd %USERPROFILE%
md virtualenvs
cd virtualenvs
virtualenv -p C:\Python26\python.exe --system-site-packages frnd-py26
frnd-py26\Scripts\activate.bat
pip install beautifulsoup4 rdflib html5lib rdfextras requests six jsmin whoosh pyparsing unittest2 ordereddict mock coverage
deactivate
virtualenv -p C:\Python27\python.exe --system-site-packages frnd-py27
frnd-py27\Scripts\activate.bat
pip install beautifulsoup4 rdflib html5lib rdfextras requests six jsmin whoosh pyparsing mock coverage
deactivate
virtualenv -p C:\Python32\python.exe --system-site-packages frnd-py32
frnd-py32\Scripts\activate.bat
pip install beautifulsoup4 rdflib html5lib rdfextras requests six jsmin whoosh pyparsing mock coverage
deactivate

