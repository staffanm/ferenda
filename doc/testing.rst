Testing your docrepo
====================

The module :py:mod:`ferenda.testutil` contains an assortment of
classes and functions that can be useful when testing code written
against the Ferenda API.

Extra assert methods
--------------------

The :py:class:`~ferenda.testutil.FerendaTestCase` is intended to be
used by your :py:class:`unittest.TestCase` based testcases. Your
testcase inherits from both ``TestCase`` and ``FerendaTestCase``, and
thus gains new assert methods:

=====================================  ======================
Method                                 Description
=====================================  ======================
:py:meth:`.assertEqualGraphs`          Compares two
                                       :py:class:`~rdflib.graph.Graph` objects
:py:meth:`.assertEqualXML`             Compares two XML documents (in string or
                                       :py:mod:`lxml.etree` form)
:py:meth:`.assertEqualDirs`            Compares the files and contents of those
                                       files in two directories
:py:meth:`.assertAlmostEqualDatetime`  Compares two datetime objects to a
                                       specified precision
=====================================  ======================

Creating parametric tests
-------------------------

A parametric test case is a single unit of test code that, during test
execution, is run several times with different arguments
(parameters). The function :py:func:`~ferenda.testutil.parametrize`
creates a single new testcase, based upon a template method, and binds
the specified parameters to the template method. Each testcase is
uniquely named based on the given parameters. Since each invocation
creates a new test case method, specific parameters can be tested in
isolation, and the normal unittest test runner reports exactly which
parameters the test succeeds or fails with.

Often, the parameters to the test is best stored in files. The
function :py:func:`~ferenda.testutil.file_parametrize` creates one
testcase, based upon a template method, for each file found in a
specified directory.

RepoTester
----------

Functional tests are written to test a specific functionality of a
software system as a whole. This means that functional tests excercize
a larger portion of the code and is focused on what the behaviour
(output) of the code should be, given a particular input. A typical
repository has at least three large units of code that benefits from a
functional-level testing: Code that performs downloading of documents,
code that extracts metadata from downloaded documents, and code that
generates structured XHTML documents from the downloaded documents.

The :py:class:`~ferenda.testutil.RepoTester` contains generic,
parametric test for all three of these. In order to use them, you
create test data in some directory of your choice, create a subclass
of ``RepoTester`` specifying the location of your test data and the
docrepo class you want to test: and finally call
:py:func:`~ferenda.testutil.parametrize_repotester` in your top-level
test code to set up one test for each test data file that you've
created.

.. literalinclude:: examples/repotester.py

Download tests
--------------
		    
See :py:meth:`~ferenda.testutil.RepoTester.download_test`.

For each download test, you need to create a JSON file under the
``source`` directory of your docroot, eg:
``myrepo/tests/files/source/basic.json`` that should look something
like this:

.. literalinclude:: examples/repotester-basic.json

Each key of the JSON object should be a URL, and the value should be
another JSON object, that should have the key ``file`` that specifies
the relative location of a file that corresponds to that URL.

When each download test runs, calls to requests.get et al are
intercepted and the given file is returned instead. This allows you to
run the download tests without hitting the remote server.

Each JSON object might also have the key ``expect``, which indicates
that the URL represents a document to be stored. The value specifieds
the location where the download method should store the corresponding
file, if that particular URL should be stored underneath the
``downloaded`` directory. In the above example, the index file is no

If you want to test your download code under any specific condition,
you can specify a special ``@settings`` key. Each key and sub-key
underneath this will be set directly on the repo object being
tested. For example, this sets the ``next_sfsnr`` key of the
:py:data:`~ferenda.DocumentRepository.config` object on the repo to
``2014:913``.

.. literalinclude:: examples/repotester-settings.json

Recording download tests
^^^^^^^^^^^^^^^^^^^^^^^^

If the environment variable ``FERENDA_SET_TESTFILE`` is set, the
download code runs like normal (calls to requests.get et al are not
intercepted) and instead each accessed URL is stored in the JSON
file. URL accessses that results in downloaded files results in
``expect`` entries in the JSON file. This allows you to record the
behaviour of existing download code to examine it or just to make sure
it doesn't change inadvertantly.



Distill and parse tests
-----------------------

See :py:meth:`~ferenda.testutil.RepoTester.distill_test` and
:py:meth:`~ferenda.testutil.RepoTester.parse_test`.

To create a distill or parse test, you first need to create whatever
files that your parse methods will need in the ``download`` directory of
your docroot.

Both :py:meth:`~ferenda.testutil.RepoTester.distill_test` and
:py:meth:`~ferenda.testutil.RepoTester.parse_test` will run your parse
method, and then compare it to expected results. For distill tests,
the expected result should be placed under
``distilled/[basefile].ttl``. For parse tests, the expected result
should be placed under ``parsed/[basefile].xhtml``.

Recording distill/parse tests
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

If the environment variable ``FERENDA_SET_TESTFILE`` is set, the
parse code runs like normal and the result of the parse is stored in
eg. ``distilled/[basefile].ttl`` or ``parsed/[basefile].xhtml``. This
is a quick way of recording existing behaviour as a baseline for your
tests.

Py23DocChecker
--------------

:py:class:`~ferenda.testutil.Py23DocChecker` is a small helper to
enable you to write doctest-style tests that run unmodified under
python 2 and 3. The main problem with cross-version compatible
doctests is with functions that return (unicode) strings. These are
formatted ``u'like this'`` in Python 2, and ``'like this'`` in
Python 3. Writing doctests for functions that return unicode strings
requires you to choose one of these syntaxes, and the result will fail
on the other platform. By strictly running doctests from within the
:py:mod:`unittest` framework through the ``load_tests`` mechanism, and
loading your doctests in this way, the tests will work even under
Python 2::

    from ferenda.testutil import Py23DocChecker
    def load_tests(loader,tests,ignore):
        tests.addTests(doctest.DocTestSuite(mymodule, checker=Py23DocChecker()))
        return tests
    

testparser
----------

:py:func:`.testparser` is a simple helper that tests
:py:class:`.FSMParser` based parsers.
