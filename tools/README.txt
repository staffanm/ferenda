This directory contains a number of useful scripts when developing ferenda.

* test.sh / test.cmd: This runs the main unittest suite (use test.sh
  on unix and test.cmd on windows). Individual test modules, suits or
  tests can be run by passing the name as a parameter, eg:

  $ tools/test.sh testManager
  $ tools/test.sh testManager.Setup
  $ tools/test.sh testManager.Setup.test_runsetup

  The unittest suite is contained in the test/test*py files. Apart
  from those, there is also a integration test suite and a functional
  test suite, run by...

* integration.sh: This requires that ElasticSearch, Fuseki and Sesame
  is up and running on localhost, but doesn't access any resources on
  the internet

* functional.sh: This runs all end-to-end tests, including examples
  used in the documentation. It excercises small parts of the RFC and
  W3C docrepo implementations, and so downloads stuff from the
  internet.

* setenv.cmd: This utility sets environment variables that disable
  some of the tests that are hard to run on Windows. Read comments for
  more info.

* setup-virtenvs.cmd: This helps with setting up virtualenvs on
  windows where the standard `pip install -r requirements.py3.txt`
  fails since LXML and SimpleParse is hard to compile. Read comments
  for more info.
  
* win32/: This directory contains prebuilt versions of lxml for
  windows (taken from http://www.lfd.uci.edu/~gohlke/pythonlibs/#lxml,
  but reproduced here since the download urls there arent't stable),
  and also a Tidy HTML binary. These are primarily used for CI builds
  on appveyor.
