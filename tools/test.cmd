@ECHO OFF
SET SKIP_FUSEKI_TESTS=1
SET SKIP_SESAME_TESTS=1
SET SKIP_SLEEPYCAT_TESTS=1
SET SKIP_ELASTICSEARCH_TESTS=1
SET FERENDA_PYTHON2_FALLBACK="C:\Python27\python.exe"
IF [%1] == [] (
   python -Wi -m unittest discover -v  test
) ELSE (
  SET PYTHONPATH=test
  python -Wi -m unittest -v %1
)