REM 
REM  These environment variables skips tests that are hard to run under
REM  windows, and makes sure python3 can use LegalRef through a python2
REM  fallback
REM  

set SKIP_FUSEKI_TESTS=1
set SKIP_SESAME_TESTS=1
set SKIP_SLEEPYCAT_TESTS=1
set FERENDA_PYTHON2_FALLBACK=C:\Python27\python.exe
